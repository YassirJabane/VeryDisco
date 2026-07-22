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


async def _mb_get(path: str, params: dict = None, timeout: int = 30) -> Optional[dict]:
    """Make a rate-limited GET to the MusicBrainz API with automatic retries."""
    global _last_request_time
    url = f"{_MB_BASE}{path}"
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}

    for attempt in range(3):
        async with _rate_lock:
            now = time.monotonic()
            wait = _MIN_INTERVAL - (now - _last_request_time)
            if wait > 0:
                await asyncio.sleep(wait)
            _last_request_time = time.monotonic()

        try:
            from backend.app.clients.http_client import get_http_client
            client = get_http_client()
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 404:
                return None
            if resp.status_code in (503, 429):
                logger.warning(f"MusicBrainz rate-limited ({resp.status_code}). Sleeping 3s before retry {attempt+1}/3.")
                await asyncio.sleep(3)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"MusicBrainz request failed ({path}, attempt {attempt+1}/3): {e}")
            if attempt < 2:
                await asyncio.sleep(2)
    return None


async def get_release_with_media(release_mbid: str) -> Optional[dict]:
    """Fetch a release with full media+recordings so disc and track positions are accurate."""
    return await _mb_get(f"/release/{release_mbid}", params={
        "inc": "media+recordings",
        "fmt": "json"
    })



def _normalize(s: str) -> str:
    return re.sub(r"[^\w]", "", s).lower()


def _fuzzy_match(a: str, b: str) -> bool:
    na, nb = _normalize(a), _normalize(b)
    return na in nb or nb in na or na == nb


def score_release(r: dict, album: str) -> int:
    """Score a MusicBrainz release for multi-disc / official tracklist matching."""
    score = 0
    title = r.get("title", "")
    status = (r.get("status") or "").lower()
    country = r.get("country") or r.get("release-event-count", "")
    disambiguation = (r.get("disambiguation") or "").lower()
    media = r.get("media") or []
    formats = {(m.get("format") or "").lower() for m in media}
    rg = r.get("release-group") or {}
    secondary_types = [t.lower() for t in (rg.get("secondary-types") or [])]

    if status == "official":
        score += 20
    if "cd" in formats:
        score += 15
        if len(media) == 1:
            score += 10  # Bonus for standard 1-CD release
    elif "digital media" in formats and "cd" not in formats:
        score -= 5

    # Penalize video/multimedia formats (DVD, Blu-ray, etc.) in special box sets
    video_formats = {"dvd", "dvd-video", "blu-ray", "vhs", "dvd-audio", "data track"}
    if any(fmt in video_formats for fmt in formats):
        score -= 20

    if isinstance(country, str):
        if country.upper() == "XW":
            score += 10
        elif country.upper() in ("US", "GB"):
            score += 5

    if _normalize(title) == _normalize(album):
        score += 10
    elif _fuzzy_match(title, album):
        score += 5

    if "explicit" in disambiguation:
        score += 5
    if "clean" in disambiguation or "instrumental" in disambiguation or "karaoke" in disambiguation:
        score -= 15

    # Penalize collector/deluxe/bonus/expanded/reissue editions unless requested
    special_edition_terms = ("collector", "deluxe", "special edition", "anniversary", "bonus", "expanded", "limited", "box set", "book", "misprint", "promo")
    if any(term in disambiguation for term in special_edition_terms):
        score -= 15

    for bad_type in ("live", "compilation", "remix", "dj-mix", "spokenword", "mixtape"):
        if bad_type in secondary_types:
            score -= 20

    return score


async def inspect_album_releases(artist: str, album: str) -> Dict[str, Any]:
    """Query MusicBrainz for candidate releases, score them, and return winner details."""
    data = await _mb_get("/release", params={
        "query": f'release:"{album}" AND artist:"{artist}"',
        "limit": 20,
        "inc": "artist-credits+media+release-groups",
        "fmt": "json",
    })
    releases = data.get("releases", []) if data else []
    if not releases:
        return {"artist": artist, "album": album, "candidates": [], "winner": None}

    candidates = [r for r in releases if _fuzzy_match(r.get("title", ""), album)]
    if not candidates:
        candidates = releases

    scored_candidates = []
    for r in candidates:
        s = score_release(r, album)
        media = r.get("media") or []
        formats = [m.get("format") or "Unknown" for m in media]
        rg = r.get("release-group") or {}
        scored_candidates.append({
            "id": r.get("id"),
            "title": r.get("title"),
            "status": r.get("status") or "Unknown",
            "country": r.get("country") or "Unknown",
            "score": s,
            "formats": formats,
            "disambiguation": r.get("disambiguation") or "",
            "secondary_types": rg.get("secondary-types") or [],
            "media_count": len(media)
        })

    scored_candidates.sort(key=lambda x: x["score"], reverse=True)
    best_candidate = scored_candidates[0]

    for c in scored_candidates:
        c["is_winner"] = (c["id"] == best_candidate["id"])

    winner_details = None
    if best_candidate.get("id"):
        full = await get_release_with_media(best_candidate["id"])
        if full:
            media_list = full.get("media", [])
            discs = []
            for m in media_list:
                disc_num = m.get("position", 1)
                tracks = []
                for t in m.get("tracks", []):
                    rec = t.get("recording") or {}
                    tracks.append({
                        "position": int(t.get("position") or t.get("number") or 0),
                        "title": rec.get("title") or t.get("title"),
                        "recording_id": rec.get("id")
                    })
                discs.append({
                    "disc_num": disc_num,
                    "format": m.get("format") or "CD",
                    "track_count": len(tracks),
                    "tracks": tracks
                })
            winner_details = {
                "id": best_candidate["id"],
                "title": full.get("title"),
                "status": full.get("status"),
                "country": full.get("country"),
                "date": full.get("date"),
                "disc_total": len(media_list),
                "discs": discs
            }

    return {
        "artist": artist,
        "album": album,
        "candidates": scored_candidates,
        "winner": winner_details
    }



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
    async def get_artist_release_groups(self, artist_mbid: str) -> List[Dict[str, Any]]:
        """
        Fetch all release groups (albums, EPs, singles) for a MusicBrainz artist.
        Returns a list of dictionaries with id, title, record_type, release_date, cover_medium.
        """
        data = await _mb_get("/release-group", params={
            "artist": artist_mbid,
            "limit": 100,
            "fmt": "json"
        })
        if not data:
            return []
        
        rgroups = data.get("release-groups", [])
        result = []
        for rg in rgroups:
            rg_id = rg.get("id")
            primary_type = (rg.get("primary-type") or "Album").lower()
            if primary_type not in ["album", "single", "ep"]:
                primary_type = "album"
            
            first_release_date = rg.get("first-release-date") or ""
            
            result.append({
                "id": rg_id,
                "mbid": rg_id,
                "title": rg.get("title", ""),
                "record_type": primary_type,
                "release_date": first_release_date,
                "cover_medium": f"https://coverartarchive.org/release-group/{rg_id}/front-250",
            })
            
        return result

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
            "inc": "recordings+artists+artist-credits+media",
            "fmt": "json"
        })

    async def get_album_tracklist(self, artist: str, album: str) -> Optional[Dict[str, Any]]:
        """
        Query MusicBrainz for candidate releases of (artist, album), score them,
        and return a normalized dict with tracks list, release_date, release_mbid, etc.
        """
        res = await inspect_album_releases(artist, album)
        winner = res.get("winner")
        if not winner:
            return None

        flat_tracks = []
        total_discs = winner.get("disc_total", 1)
        total_tracks = 0
        for d in winner.get("discs", []):
            total_tracks += d.get("track_count", 0)

        for d in winner.get("discs", []):
            disc_num = d.get("disc_num", 1)
            for t in d.get("tracks", []):
                flat_tracks.append({
                    "title": t.get("title"),
                    "track_position": t.get("position"),
                    "disk_number": disc_num,
                    "disc_num": disc_num,
                    "id": t.get("recording_id"),
                    "release_mbid": winner.get("id"),
                    "nb_tracks": total_tracks,
                    "nb_discs": total_discs,
                })

        return {
            "tracks": flat_tracks,
            "release_date": winner.get("date") or "",
            "release_mbid": winner.get("id"),
            "nb_tracks": total_tracks,
            "nb_discs": total_discs,
            "title": winner.get("title", album),
        }

    # -----------------------------------------------------------------------
    # Cover Art Archive
    # -----------------------------------------------------------------------

    async def get_cover_art(self, release_mbid: str) -> Optional[bytes]:
        """Download the front cover art for a release from the Cover Art Archive."""
        url = f"{_CAA_BASE}/release/{release_mbid}/front"
        try:
            from backend.app.clients.http_client import get_http_client
            client = get_http_client()
            resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
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
        """
        clean_art = re.split(r'[\(\[]?\s*(?:\b(?:feat|ft|featuring|and|with|vs)\.?\s+|&\s+)', artist, flags=re.IGNORECASE)[0].strip()
        clean_tit = re.split(r'[\(\[]\s*(?:feat|ft|featuring)', title, flags=re.IGNORECASE)[0].strip()
        
        # If album is provided, check the official album tracklist first to get exact canonical album/disc/track MBID info
        if album:
            try:
                mb_album = await self.get_album_tracklist(clean_art or artist, album)
                if mb_album and mb_album.get("tracks"):
                    clean_target = _normalize(clean_tit)
                    for t in mb_album["tracks"]:
                        t_title = _normalize(t.get("title", ""))
                        if clean_target and (clean_target == t_title or clean_target in t_title or t_title in clean_target or _fuzzy_match(t.get("title", ""), clean_tit)):
                            return {
                                "title": t.get("title", title),
                                "artist": artist,
                                "album_artist": artist,
                                "album": mb_album.get("title", album),
                                "date": mb_album.get("release_date", ""),
                                "track_num": t.get("track_position"),
                                "track_total": mb_album.get("nb_tracks"),
                                "disc_num": t.get("disk_number", 1),
                                "disc_total": mb_album.get("nb_discs", 1),
                                "release_mbid": mb_album.get("release_mbid"),
                                "recording_id": t.get("id"),
                            }
            except Exception as e:
                logger.debug(f"get_album_tracklist lookup in get_track_metadata failed: {e}")

        recording = await self.search_recording(clean_art, clean_tit, album)
        if not recording and album:
            recording = await self.search_recording(clean_art, clean_tit, "")
        if not recording:
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
        if releases:
            best_release = sorted(releases, key=lambda r: score_release(r, album or title), reverse=True)[0]

        release_mbid = None
        release_title = album or recording.get("title", title)
        release_date = ""
        track_num = None
        track_total = None
        disc_num = 1
        disc_total = 1

        if best_release:
            release_mbid = best_release.get("id")
            release_title = best_release.get("title", release_title)
            release_date = best_release.get("date", "")

            # The search_recording endpoint does not include media data.
            # Fetch the full release with inc=media+recordings to get disc/track positions.
            media_list = best_release.get("media", [])
            if not media_list and release_mbid:
                full_release = await get_release_with_media(release_mbid)
                if full_release:
                    media_list = full_release.get("media", [])

            disc_total = len(media_list) if media_list else 1
            for m_idx, m in enumerate(media_list, start=1):
                tracks = m.get("track", []) or m.get("tracks", [])
                for t in tracks:
                    rec_id = (t.get("recording") or {}).get("id") or t.get("id")
                    if rec_id == recording.get("id"):
                        try:
                            track_num = int(t.get("position") or t.get("number") or 1)
                        except (ValueError, TypeError):
                            track_num = 1
                        track_total = len(tracks)
                        disc_num = m.get("position") or m_idx
                        break

        return {
            "title": recording.get("title", title),
            "artist": full_artist,
            "album_artist": primary_artist,
            "album": release_title,
            "date": release_date,
            "track_num": track_num,
            "track_total": track_total,
            "disc_num": disc_num,
            "disc_total": disc_total,
            "release_mbid": release_mbid,
            "recording_id": recording.get("id"),
        }


# Singleton
musicbrainz_client = MusicBrainzClient()
