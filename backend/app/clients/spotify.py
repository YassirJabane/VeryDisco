import base64
import re
from typing import Any, Dict, List
from urllib.parse import urlparse

from backend.app.clients.http_client import get_http_client


class SpotifyClient:
    """Spotify Web API client for public playlist imports."""

    def __init__(self, client_id: str, client_secret: str, timeout: int = 20):
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.timeout = timeout

    @staticmethod
    def playlist_id(value: str) -> str:
        value = (value or "").strip()
        if re.fullmatch(r"[A-Za-z0-9]{22}", value):
            return value
        parsed = urlparse(value)
        if parsed.netloc.lower() not in {"open.spotify.com", "spotify.com", "www.spotify.com"}:
            raise ValueError("Inserisci un link di playlist Spotify valido.")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2 or parts[0] != "playlist" or not re.fullmatch(r"[A-Za-z0-9]{22}", parts[1]):
            raise ValueError("Il link deve puntare a una playlist Spotify.")
        return parts[1]

    async def _access_token(self) -> str:
        if not self.client_id or not self.client_secret:
            raise ValueError("Spotify client ID e client secret non sono configurati.")
        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        client = await get_http_client()
        response = await client.post("https://accounts.spotify.com/api/token",
                                     headers={"Authorization": f"Basic {credentials}"},
                                     data={"grant_type": "client_credentials"}, timeout=self.timeout)
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise ValueError("Spotify non ha restituito un access token.")
        return token

    async def get_playlist(self, value: str) -> Dict[str, Any]:
        playlist_id = self.playlist_id(value)
        token = await self._access_token()
        client = await get_http_client()
        response = await client.get(
            f"https://api.spotify.com/v1/playlists/{playlist_id}",
            params={"fields": "id,name,description,images,tracks.next,tracks.items(track(id,name,artists(name),album(name),duration_ms))"},
            headers={"Authorization": f"Bearer {token}"}, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        tracks: List[Dict[str, Any]] = []
        page = data.get("tracks", {})
        while True:
            for item in page.get("items", []):
                track = item.get("track") or {}
                artists = track.get("artists") or []
                if track.get("name") and artists:
                    tracks.append({"artist": artists[0].get("name", ""), "title": track["name"],
                                   "album": (track.get("album") or {}).get("name", ""),
                                   "duration": track.get("duration_ms")})
            next_url = page.get("next")
            if not next_url:
                break
            response = await client.get(next_url, headers={"Authorization": f"Bearer {token}"}, timeout=self.timeout)
            response.raise_for_status()
            page = response.json()
        data["tracks"] = tracks
        return data
