"""
MusicBrainz API client for VeryDisco.
- Searches for recordings, artists, and releases via the MusicBrainz JSON API.
- Downloads cover art from the Cover Art Archive (CAA).
- Enforces the ToS rate limit of 1 request/second via an asyncio lock.
"""

import asyncio
import re
import time
from typing import Optional, Dict, Any, List
import httpx
from backend.app.logger import get_logger

logger = get_logger()

_MB_BASE = "https://musicbrainz.org/ws/2"
_CAA_BASE = "https://coverartarchive.org"
_USER_AGENT = "VeryDisco/1.0 (homelab music manager)"

# MusicBrainz ToS: max 1 request per second for unauthenticated clients
_rate_lock = asyncio.Lock()
_last_request_time: float = 0.0
_MIN_INTERVAL = 1.05  # seconds between requests


async def _mb_get(path: str, params: dict = None, timeout: int = 15) -> Optional[dict]:
    """Make a rate-limited GET to the MusicBrainz API."""
    global _last_request_time
    async with _rate_lock:
        now = time.monotonic()
        wait = _MIN_INTERVAL - (now - _last_request_time)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_time = time.monotonic()

    url = f"{_MB_BASE}{path}"
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 404:
                return None
            if resp.status_code == 503:
                logger.warning("MusicBrainz rate-limited (503). Sleeping 2s.")
                await asyncio.sleep(2)
                return None
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning(f"MusicBrainz request failed ({path}): {e}")
        return None


def _normalize(s: str) -> str:
    return re.sub(r"[^\w]", "", s).lower()


def _fuzzy_match(a: str, b: str) -> bool:
    na, nb = _normalize(a), _normalize(b)
    return na in nb or nb in na or na == nb


class MusicBrainzClient:
    """Thin wrapper around the MusicBrainz JSON API and Cover Art Archive."""

    # -----------------------------------------------------------------------
    # Artist resolution
    # -----------------------------------------------------------------------

    async def search_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """Find an artist by name. Returns the best-matching artist dict."""
        data = await _mb_get("/artist", params={
            "query": f'artist:"{artist_name}"',
            "limit": 5,
            "fmt": "json"
        })
        if not data:
            return None
        artists = data.get("artists", [])
        for a in artists:
            if _fuzzy_match(a.get("name", ""), artist_name):
                return a
        return artists[0] if artists else None

    async def get_canonical_artist_name(self, artist_name: str) -> str:
        """Return the MusicBrainz canonical artist name. Falls back to input."""
        artist = await self.search_artist(artist_name)
        if artist:
            return artist.get("name", artist_name)
        return artist_name

    async def resolve_artist_aliases(self, artist_names: List[str]) -> Dict[str, str]:
        """For each name, return { input_name: canonical_mb_name }."""
        result: Dict[str, str] = {}
        for name in artist_names:
            canonical = await self.get_canonical_artist_name(name)
            result[name] = canonical
        return result

    async def get_artist_all_names(self, artist_name: str) -> List[str]:
        """Return all known names / aliases for a given artist name."""
        artist = await self.search_artist(artist_name)
        if not artist:
            return [artist_name]
        names = {artist.get("name", artist_name)}
        for alias in artist.get("aliases", []):
            n = alias.get("name")
            if n:
                names.add(n)
        sort_name = artist.get("sort-name")
        if sort_name:
            names.add(sort_name)
        return list(names)

    # -----------------------------------------------------------------------
    # Recording / track lookup
    # -----------------------------------------------------------------------

    async def search_recording(
        self, artist: str, title: str, album: str = ""
    ) -> Optional[Dict[str, Any]]:
        """Search for a recording by artist + title. Returns the best match."""
        query_parts = [f'recording:"{title}"', f'artist:"{artist}"']
        if album:
            query_parts.append(f'release:"{album}"')
        data = await _mb_get("/recording", params={
            "query": " AND ".join(query_parts),
            "limit": 5,
            "inc": "artists+releases+isrcs",
            "fmt": "json"
        })
        if not data:
            return None
        recordings = data.get("recordings", [])
        for r in recordings:
            artists_str = " ".join(
                a.get("name", "") for a in r.get("artist-credit", [])
                if isinstance(a, dict)
            )
            if _fuzzy_match(r.get("title", ""), title) and _fuzzy_match(artists_str, artist):
                return r
        return recordings[0] if recordings else None

    # -----------------------------------------------------------------------
    # Release (album) lookup
    # -----------------------------------------------------------------------

    async def search_release(
        self, artist: str, album: str
    ) -> Optional[Dict[str, Any]]:
        """Find a release (album/EP/single) by artist + album title."""
        data = await _mb_get("/release", params={
            "query": f'release:"{album}" AND artist:"{artist}"',
            "limit": 5,
            "inc": "artist-credits+labels+date",
            "fmt": "json"
        })
        if not data:
            return None
        releases = data.get("releases", [])
        for r in releases:
            if _fuzzy_match(r.get("title", ""), album):
                return r
        return releases[0] if releases else None

    async def get_release_details(self, release_mbid: str) -> Optional[Dict[str, Any]]:
        """Fetch full release details including track list and artist credits."""
        return await _mb_get(f"/release/{release_mbid}", params={
            "inc": "recordings+artists+artist-credits",
            "fmt": "json"
        })

    # -----------------------------------------------------------------------
    # Cover Art Archive
    # -----------------------------------------------------------------------

    async def get_cover_art(self, release_mbid: str) -> Optional[bytes]:
        """Download the front cover art for a release from the Cover Art Archive."""
        url = f"{_CAA_BASE}/release/{release_mbid}/front"
        try:
            async with httpx.AsyncClient(
                timeout=15,
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT}
            ) as client:
                resp = await client.get(url)
                if resp.status_code in (200,):
                    return resp.content if resp.content else None
                if resp.status_code == 404:
                    return None
                logger.debug(f"CAA returned {resp.status_code} for {release_mbid}")
                return None
        except Exception as e:
            logger.debug(f"Failed to fetch cover art from CAA for {release_mbid}: {e}")
            return None

    async def get_cover_art_for_artist_album(
        self, artist: str, album: str
    ) -> Optional[bytes]:
        """Resolve release MBID for (artist, album) then download cover art."""
        try:
            release = await self.search_release(artist, album)
            if not release:
                return None
            mbid = release.get("id")
            if not mbid:
                return None
            return await self.get_cover_art(mbid)
        except Exception as e:
            logger.debug(f"MB cover art lookup failed for '{artist} - {album}': {e}")
            return None

    # -----------------------------------------------------------------------
    # Full metadata lookup for a track (recording)
    # -----------------------------------------------------------------------

    async def get_track_metadata(
        self, artist: str, title: str, album: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        High-level method: returns a dict with metadata for embed_metadata calls.

        Returns:
          - 'title'         canonical recording title
          - 'artist'        full artist string (primary [feat. X])
          - 'album_artist'  primary artist only
          - 'album'         album/release title
          - 'date'          release date string (YYYY or YYYY-MM-DD)
          - 'track_num'     track position on release (int or None)
          - 'release_mbid'  MusicBrainz Release ID for cover art
          - 'recording_id'  MusicBrainz Recording ID
        or None if no result found.
        """
        recording = await self.search_recording(artist, title, album)
        if not recording:
            return None

        credits = recording.get("artist-credit", [])
        artist_parts = []
        for credit in credits:
            if isinstance(credit, dict) and "artist" in credit:
                artist_parts.append(credit["artist"].get("name", ""))
                joinphrase = credit.get("joinphrase", "")
                if joinphrase:
                    artist_parts.append(joinphrase)

        full_artist = "".join(artist_parts).strip() or artist
        primary_artist = (
            credits[0]["artist"]["name"]
            if credits and isinstance(credits[0], dict)
            else artist
        )

        releases = recording.get("releases", [])
        best_release = None
        for rel in releases:
            if (rel.get("status") or "").lower() == "official":
                best_release = rel
                break
        if not best_release and releases:
            best_release = releases[0]

        release_mbid = None
        release_title = album or recording.get("title", title)
        release_date = ""
        track_num = None

        if best_release:
            release_mbid = best_release.get("id")
            release_title = best_release.get("title", release_title)
            release_date = best_release.get("date", "")
            for m in best_release.get("media", []):
                for t in m.get("track", []):
                    rec_id = (t.get("recording") or {}).get("id") or t.get("id")
                    if rec_id == recording.get("id"):
                        track_num = t.get("position") or t.get("number")
                        break

        return {
            "title": recording.get("title", title),
            "artist": full_artist,
            "album_artist": primary_artist,
            "album": release_title,
            "date": release_date,
            "track_num": track_num,
            "release_mbid": release_mbid,
            "recording_id": recording.get("id"),
        }


# Singleton
musicbrainz_client = MusicBrainzClient()
