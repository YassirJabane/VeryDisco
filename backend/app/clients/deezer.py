import httpx
from typing import Dict, Any, Optional, List
import urllib.parse
import re
from backend.app.logger import get_logger
from backend.app.clients.http_client import get_http_client

logger = get_logger()

def _normalize(s: str) -> str:
    """Normalize a string for fuzzy comparison."""
    return re.sub(r'[^\w]', '', s).lower()

def _result_matches(result: dict, artist: str, title: str) -> bool:
    """Avoid attaching metadata from a similarly named song or another artist."""
    r_artist = _normalize(result.get("artist", {}).get("name", ""))
    r_title = _normalize(result.get("title", ""))
    n_artist = _normalize(artist)
    n_title = _normalize(title)

    return bool(n_artist and n_title and n_artist == r_artist and n_title == r_title)

import asyncio

_deezer_semaphore = asyncio.Semaphore(5)

class DeezerClient:
    def __init__(self, timeout: int = 15):
        self.base_url = "https://api.deezer.com"
        self.timeout = timeout

    async def _request_json(self, url: str) -> Optional[Dict[str, Any]]:
        """Helper to fetch JSON from Deezer with retries and error checking."""
        async with _deezer_semaphore:
            for attempt in range(3):
                try:
                    client = await get_http_client()
                    resp = await client.get(url)
                    if resp.status_code == 429:
                        await asyncio.sleep(0.8 * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    if "error" in data:
                        if data["error"].get("type") == "QuotaException":
                            await asyncio.sleep(0.8 * (attempt + 1))
                            continue
                        logger.error(f"Deezer API error on {url}: {data['error']}")
                        return None
                    return data
                except Exception as e:
                    if attempt == 2:
                        logger.error(f"Deezer request failed for {url}: {e}")
                    await asyncio.sleep(0.4)
            return None

    async def get_track_metadata(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        """
        Search for a track on Deezer and return its metadata (including album, cover art URL, etc).
        Returns the best-matching result or None if nothing matches.
        """
        clean_title = title.split('(')[0].split('[')[0].strip()
        clean_artist = artist.split('feat')[0].split('ft.')[0].strip()
        query = f"{clean_artist} {clean_title}"

        url = f"{self.base_url}/search?q={urllib.parse.quote(query)}&limit=5"

        data = await self._request_json(url)
        if not data:
            return None

        results = data.get("data", [])
        if not results:
            logger.warning(f"Deezer search returned no results for '{artist} - {title}'")
            return None

        # Find the first result that is a reasonable match
        for result in results:
            if _result_matches(result, clean_artist, clean_title):
                return result

        logger.warning(f"Deezer: no matching track result found for '{artist} - {title}'")
        return None

    async def get_album_tracks(self, album_id: int) -> Optional[Dict[str, Any]]:
        """Fetch tracks of a given album from Deezer."""
        url = f"{self.base_url}/album/{album_id}/tracks?limit=100"
        return await self._request_json(url)

    async def get_album_metadata(self, album_id: int) -> Optional[Dict[str, Any]]:
        """Fetch album details (including release date) from Deezer."""
        url = f"{self.base_url}/album/{album_id}"
        return await self._request_json(url)

    async def get_track_details(self, track_id: int) -> Optional[Dict[str, Any]]:
        """Fetch full track details (including contributors) from Deezer."""
        url = f"{self.base_url}/track/{track_id}"
        return await self._request_json(url)

    def resolve_joint_artists(self, data: dict) -> tuple[str, str]:
        """
        Resolve joint artists from a Deezer track or album dictionary.
        Returns a tuple: (artist_string, album_artist_string)
        """
        contributors = data.get("contributors", [])
        if not contributors:
            single_name = data.get("artist", {}).get("name", "")
            return single_name, single_name

        main = [c.get("name") for c in contributors if c.get("role", "").lower() == "main"]
        featured = [c.get("name") for c in contributors if c.get("role", "").lower() in ["featured", "feature"]]

        main_str = " & ".join(main) if main else data.get("artist", {}).get("name", "")
        artist_str = main_str
        if featured:
            artist_str += " feat. " + " & ".join(featured)
            
        return artist_str, main_str

    async def get_artist_releases(self, artist_id: int) -> Optional[List[Dict[str, Any]]]:
        """Fetch all releases (albums, EPs, singles) for a given artist ID from Deezer."""
        url = f"{self.base_url}/artist/{artist_id}/albums?limit=100"
        data = await self._request_json(url)
        return data.get("data", []) if data else None

    async def download_cover_art(self, cover_url: str) -> Optional[bytes]:
        """Download the cover art from the given URL."""
        if not cover_url:
            return None

        async with _deezer_semaphore:
            for attempt in range(3):
                try:
                    client = await get_http_client()
                    resp = await client.get(cover_url)
                    if resp.status_code == 429:
                        await asyncio.sleep(0.8 * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    return resp.content
                except Exception as e:
                    if attempt == 2:
                        logger.error(f"Failed to download cover art from '{cover_url}': {e}")
                    await asyncio.sleep(0.4)
            return None

    async def get_album_cover(self, artist: str, album: str) -> Optional[bytes]:
        """Search Deezer for album cover_xl art bytes for given (artist, album)."""
        from backend.app.sync import extract_main_artist
        clean_artist = extract_main_artist(artist)
        clean_album = re.sub(r'[\(\[].*?[\)\]]', '', album).strip()
        query = f"{clean_artist} {clean_album}".strip()

        url = f"{self.base_url}/search/album?q={urllib.parse.quote(query)}&limit=5"
        try:
            data = await self._request_json(url)
            if not data:
                return None
            results = data.get("data", [])
            if not results:
                return None

            best_cover_url = None
            for res in results:
                r_title = _normalize(res.get("title", ""))
                a_title = _normalize(clean_album)
                r_artist = _normalize(res.get("artist", {}).get("name", ""))
                n_artist = _normalize(clean_artist)

                title_match = bool(a_title) and a_title == r_title
                artist_match = bool(n_artist) and n_artist == r_artist

                if title_match and artist_match:
                    best_cover_url = res.get("cover_xl") or res.get("cover_big")
                    break

            if best_cover_url:
                return await self.download_cover_art(best_cover_url)
        except Exception as e:
            logger.debug(f"Deezer get_album_cover failed for '{artist} - {album}': {e}")
        return None

    async def search_album_artwork(self, artist: str, album: str) -> List[Dict[str, Any]]:
        """Expose only album-cover candidates with exact artist and album matches."""
        query = urllib.parse.quote(f"{artist} {album}")
        data = await self._request_json(f"{self.base_url}/search/album?q={query}&limit=10")
        expected_artist = _normalize(artist)
        expected_album = _normalize(album)
        results = []
        for item in (data or {}).get("data", []):
            item_artist = (item.get("artist") or {}).get("name", "")
            item_album = item.get("title", "")
            if (_normalize(item_artist) != expected_artist or _normalize(item_album) != expected_album
                    or not expected_artist or not expected_album):
                continue
            cover_url = item.get("cover_xl") or item.get("cover_big")
            if cover_url:
                results.append({
                    "artist": item_artist,
                    "album": item_album,
                    "url": cover_url,
                    "thumbnail": item.get("cover_medium") or cover_url,
                    "resolution": "High resolution" if item.get("cover_xl") else "Large",
                    "source": "Deezer",
                    "release_date": "",
                })
        return results

