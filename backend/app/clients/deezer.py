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
    """Check if a Deezer result is a reasonable match for the expected artist/title."""
    r_artist = _normalize(result.get("artist", {}).get("name", ""))
    r_title = _normalize(result.get("title", ""))
    n_artist = _normalize(artist)
    n_title = _normalize(title)

    # Title must match at least partially (allow for remaster/version suffixes)
    title_match = n_title in r_title or r_title in n_title
    if not title_match:
        return False

    # At least one significant word of the artist must appear
    artist_words = [w for w in n_artist.split() if len(w) > 2]
    artist_match = any(w in r_artist for w in artist_words) if artist_words else (n_artist in r_artist)
    return artist_match

import asyncio

_deezer_semaphore = asyncio.Semaphore(5)

class DeezerClient:
    def __init__(self, timeout: int = 15):
        self.base_url = "https://api.deezer.com"
        self.timeout = timeout

    async def get_track_metadata(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        """
        Search for a track on Deezer and return its metadata (including album, cover art URL, etc).
        Returns the best-matching result or None if nothing matches.
        """
        clean_title = title.split('(')[0].split('[')[0].strip()
        clean_artist = artist.split('feat')[0].split('ft.')[0].strip()
        query = f"{clean_artist} {clean_title}"

        url = f"{self.base_url}/search?q={urllib.parse.quote(query)}&limit=5"

        try:
            client = get_http_client()
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

            results = data.get("data", [])
            if not results:
                logger.warning(f"Deezer search returned no results for '{artist} - {title}'")
                return None

            # Find the first result that is a reasonable match
            for result in results:
                if _result_matches(result, clean_artist, clean_title):
                    return result

            # Fallback: return first result if nothing matches strictly
            logger.warning(f"Deezer: no strong match found for '{artist} - {title}', using first result")
            return results[0]

        except Exception as e:
            logger.error(f"Failed to fetch metadata from Deezer for '{artist} - {title}': {e}")
            return None

    async def get_album_tracks(self, album_id: int) -> Optional[Dict[str, Any]]:
        """Fetch tracks of a given album from Deezer."""
        url = f"{self.base_url}/album/{album_id}/tracks?limit=100"
        async with _deezer_semaphore:
            for attempt in range(3):
                try:
                    client = get_http_client()
                    resp = await client.get(url)
                    if resp.status_code == 429:
                        await asyncio.sleep(0.8 * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    return resp.json()
                except Exception as e:
                    if attempt == 2:
                        logger.error(f"Failed to fetch album tracks for {album_id} from Deezer: {e}")
                    await asyncio.sleep(0.4)
            return None

    async def get_album_metadata(self, album_id: int) -> Optional[Dict[str, Any]]:
        """Fetch album details (including release date) from Deezer."""
        url = f"{self.base_url}/album/{album_id}"
        try:
            client = get_http_client()
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch album metadata for {album_id}: {e}")
            return None

    async def get_track_details(self, track_id: int) -> Optional[Dict[str, Any]]:
        """Fetch full track details (including contributors) from Deezer."""
        url = f"{self.base_url}/track/{track_id}"
        try:
            client = get_http_client()
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch track details for {track_id}: {e}")
            return None

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
        try:
            client = get_http_client()
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
        except Exception as e:
            logger.error(f"Failed to fetch artist releases for {artist_id} from Deezer: {e}")
            return None

    async def download_cover_art(self, cover_url: str) -> Optional[bytes]:
        """Download the cover art from the given URL."""
        if not cover_url:
            return None

        try:
            client = get_http_client()
            resp = await client.get(cover_url)
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            logger.error(f"Failed to download cover art from '{cover_url}': {e}")
            return None

    async def get_album_cover(self, artist: str, album: str) -> Optional[bytes]:
        """Search Deezer for album cover_xl art bytes for given (artist, album)."""
        from backend.app.sync import extract_main_artist
        clean_artist = extract_main_artist(artist)
        clean_album = re.sub(r'[\(\[].*?[\)\]]', '', album).strip()
        query = f"{clean_artist} {clean_album}".strip()

        url = f"{self.base_url}/search/album?q={urllib.parse.quote(query)}&limit=5"
        try:
            client = get_http_client()
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("data", [])
            if not results:
                # Fallback: try searching tracks directly on Deezer to extract album cover
                dz_tr = await self.get_track_metadata(clean_artist, clean_album)
                if dz_tr:
                    cover_url = dz_tr.get("album", {}).get("cover_xl") or dz_tr.get("album", {}).get("cover_big")
                    if cover_url:
                        return await self.download_cover_art(cover_url)
                return None
            best_cover_url = None
            for res in results:
                r_title = _normalize(res.get("title", ""))
                a_title = _normalize(clean_album)
                if a_title in r_title or r_title in a_title:
                    best_cover_url = res.get("cover_xl") or res.get("cover_big")
                    break
            if not best_cover_url and results:
                best_cover_url = results[0].get("cover_xl") or results[0].get("cover_big")

            if best_cover_url:
                return await self.download_cover_art(best_cover_url)
        except Exception as e:
            logger.debug(f"Deezer get_album_cover failed for '{artist} - {album}': {e}")
        return None

