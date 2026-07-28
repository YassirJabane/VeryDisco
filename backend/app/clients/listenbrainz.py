import re
import httpx
from typing import List, Dict, Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from backend.app.logger import get_logger
from backend.app.clients.http_client import get_http_client

logger = get_logger()

def find_key_recursive(d: Any, target_key: str) -> Optional[Any]:
    """Recursively search for a key in a nested dict or list."""
    if isinstance(d, dict):
        if target_key in d:
            return d[target_key]
        for k, v in d.items():
            res = find_key_recursive(v, target_key)
            if res is not None:
                return res
    elif isinstance(d, list):
        for item in d:
            res = find_key_recursive(item, target_key)
            if res is not None:
                return res
    return None

def extract_uuid(identifier: str) -> Optional[str]:
    """Extract UUID from a JSPF identifier string (URL)."""
    match = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", identifier, re.IGNORECASE)
    return match.group(0) if match else None

class ListenBrainzClient:
    def __init__(self, username: str, playlist_source: str, token: str = "", timeout: int = 20):
        self.username = username
        self.playlist_source = playlist_source
        self.token = token
        self.timeout = timeout
        self.base_url = "https://api.listenbrainz.org"

    def _get_headers(self) -> Dict[str, str]:
        headers = {"User-Agent": "VeryDisco/1.0.0"}
        if self.token:
            headers["Authorization"] = f"Token {self.token}"
        return headers

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=3),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True
    )
    async def get_playlists(self) -> List[Dict[str, Any]]:
        """Fetch all playlists created for the user."""
        url = f"{self.base_url}/1/user/{self.username}/playlists/createdfor"
        logger.info(f"Fetching ListenBrainz playlists created for user '{self.username}'...")
        client = await get_http_client()
        resp = await client.get(url, headers=self._get_headers())
        resp.raise_for_status()
        data = resp.json()
        return data.get("playlists", [])

    async def resolve_playlist_mbid(self) -> str:
        """Find the matching playlist source and resolve its MBID."""
        playlists = await self.get_playlists()
        matching_playlists = []

        for p_wrapper in playlists:
            p = p_wrapper.get("playlist", {})
            # Look up the playlist_source in the extension metadata recursively
            source_val = find_key_recursive(p.get("extension", {}), "source_patch")
            
            # Fallback check if it's stored directly or as playlist_source or under algorithm_metadata
            if not source_val:
                source_val = find_key_recursive(p, "playlist_source")
            if not source_val:
                source_val = find_key_recursive(p, "playlist_type")

            if source_val == self.playlist_source:
                matching_playlists.append(p)

        if not matching_playlists:
            raise ValueError(f"No playlist found with source matching '{self.playlist_source}' for user '{self.username}'.")

        # Sort by creation date or date modified if available.
        # Format of date field: e.g. "date": "2024-03-25T12:00:00Z"
        # If no dates, we keep the order as returned by the API
        def get_date(playlist_dict: Dict[str, Any]) -> str:
            date_str = playlist_dict.get("created") or playlist_dict.get("date") or ""
            ext = playlist_dict.get("extension", {}).get("https://musicbrainz.org/doc/jspf#playlist", {})
            if not ext:
                # Try finding it generically
                ext = find_key_recursive(playlist_dict, "https://musicbrainz.org/doc/jspf#playlist") or {}
            last_mod = ext.get("last_modified_at") or ""
            return str(last_mod) if last_mod else str(date_str)

        matching_playlists.sort(key=get_date, reverse=True)
        target_playlist = matching_playlists[0]
        
        identifier = target_playlist.get("identifier", "")
        mbid = extract_uuid(identifier)
        if not mbid:
            raise ValueError(f"Could not extract a valid MBID from playlist identifier: '{identifier}'")

        logger.info(f"Resolved playlist: Title='{target_playlist.get('title')}', MBID='{mbid}'")
        return mbid

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=3),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True
    )
    async def get_playlist_tracks(self, mbid: str) -> List[Dict[str, str]]:
        """Fetch JSPF playlist tracks by its MBID."""
        url = f"{self.base_url}/1/playlist/{mbid}"
        logger.info(f"Fetching JSPF tracks for playlist MBID '{mbid}'...")
        headers = self._get_headers()
        headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        headers["Pragma"] = "no-cache"
        headers["Expires"] = "0"
        client = await get_http_client()
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        
        playlist_data = data.get("playlist", {})
        tracks_raw = playlist_data.get("track", [])
        
        tracks = []
        for t in tracks_raw:
            artist = t.get("creator")
            title = t.get("title")
            duration = t.get("duration")  # Duration in milliseconds (JSPF standard)
            album = t.get("album")
            if artist and title:
                tracks.append({
                    "artist": artist.strip(),
                    "title": title.strip(),
                    "duration": duration,  # can be None
                    "album": album.strip() if album else None
                })
        
        logger.info(f"Loaded {len(tracks)} tracks from playlist MBID '{mbid}'")
        return tracks

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=5),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True
    )
    async def resolve_recording_mbid(self, artist: str, title: str) -> Optional[str]:
        """Query MusicBrainz to resolve the MBID for a recording by artist and title."""
        from backend.app.clients.musicbrainz import MusicBrainzClient
        mb_client = MusicBrainzClient()
        logger.info(f"Resolving MBID via MusicBrainz for '{artist} - {title}'...")
        rec = await mb_client.search_recording(artist, title)
        if rec:
            return rec.get("id")
        return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True
    )
    async def get_user_feedback(self, score: Optional[int] = None, count: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Fetch user feedback (loved/unloved tracks) from ListenBrainz with metadata."""
        url = f"{self.base_url}/1/feedback/user/{self.username}/get-feedback"
        params = {
            "metadata": "true",
            "count": count,
            "offset": offset
        }
        if score is not None:
            params["score"] = score

        logger.info(f"Fetching user feedback from ListenBrainz for '{self.username}' (score={score})...")
        client = await get_http_client()
        resp = await client.get(url, headers=self._get_headers(), params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("feedback", [])

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=3),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True
    )
    async def submit_feedback(self, artist: str, title: str, score: int = 1, mbid: Optional[str] = None) -> bool:
        """Submit feedback (Love/Hate) to ListenBrainz. 1 = Love, -1 = Hate, 0 = Un-Love.
        
        Args:
            artist: Artist name.
            title: Track title.
            score: 1 = Love, -1 = Hate, 0 = Un-Love.
            mbid: Optional pre-resolved MusicBrainz recording ID. If not provided,
                  a MusicBrainz lookup will be performed automatically.
        """
        if not self.token:
            logger.error("Cannot submit feedback to ListenBrainz: No token provided.")
            return False

        # Use the provided mbid, or fall back to a MusicBrainz lookup
        if not mbid:
            mbid = await self.resolve_recording_mbid(artist, title)

        if not mbid:
            # Can't submit without an MBID, but don't block the rest of the pipeline
            logger.warning(
                f"Skipping ListenBrainz feedback for '{artist} - {title}': "
                "could not resolve a MusicBrainz recording ID. "
                "The track will still be downloaded/promoted."
            )
            return True  # Return True so callers don't treat this as a hard failure

        url = f"{self.base_url}/1/feedback/recording-feedback"
        payload = {
            "recording_mbid": mbid,
            "score": score
        }
        logger.info(f"Submitting ListenBrainz feedback (score={score}) for MBID '{mbid}'")
        client = await get_http_client()
        resp = await client.post(url, json=payload, headers=self._get_headers())
        if resp.status_code == 200:
            logger.info(f"Feedback submitted successfully for '{artist} - {title}'.")
            return True
        else:
            logger.error(f"Failed to submit feedback. Status: {resp.status_code}, Body: {resp.text}")
            return False
