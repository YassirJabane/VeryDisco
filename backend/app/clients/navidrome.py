import hashlib
import string
import random
from typing import Dict, Any, Optional
import os
import time
from backend.app.logger import get_logger
from backend.app.clients.http_client import get_http_client

logger = get_logger()

class NavidromeClient:
    def __init__(self, url: str, username: str = "", password: str = "", token: str = "", salt: str = "", timeout: int = 15):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.token = token
        self.salt = salt
        self.timeout = timeout

    def _generate_auth_params(self) -> dict:
        """
        Generate authentication parameters for Subsonic API.
        Prefers token + salt (MD5 hash of password + salt) if password is provided,
        or uses pre-configured token and salt.
        """
        if self.password:
            # Generate random salt
            salt = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
            # Create token: md5(password + salt)
            token = hashlib.md5((self.password + salt).encode('utf-8')).hexdigest()
        else:
            token = self.token
            salt = self.salt

        return {
            "u": self.username,
            "t": token,
            "s": salt,
            "v": "1.16.1",
            "c": "VeryDisco",
            "f": "json"
        }

    async def test_connection(self) -> str:
        """Ping the Subsonic server to verify credentials and connectivity."""
        if not self.url or not self.username or (not self.password and not (self.token and self.salt)):
            raise ValueError("Navidrome integration requires URL, username, and password (or token/salt).")
            
        params = self._generate_auth_params()
        url = f"{self.url}/rest/ping.view"
        
        client = get_http_client()
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        
        data = resp.json()
        subsonic_resp = data.get("subsonic-response", {})
        if subsonic_resp.get("status") == "failed":
            err = subsonic_resp.get("error", {})
            msg = err.get("message", "Unknown Subsonic error")
            raise ValueError(f"Navidrome error: {msg}")
            
        server_version = subsonic_resp.get("serverVersion", "unknown")
        return f"Connected to Navidrome server v{server_version}!"

    async def trigger_scan(self) -> bool:
        """Trigger a library rescan on the Navidrome/Subsonic server."""
        if not self.url or not self.username or (not self.password and not (self.token and self.salt)):
            return False
            
        params = self._generate_auth_params()
        url = f"{self.url}/rest/startScan.view"
        
        try:
            client = get_http_client()
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            subsonic_resp = data.get("subsonic-response", {})
            if subsonic_resp.get("status") == "ok":
                logger.info("Successfully triggered library scan on Navidrome.")
                return True
            else:
                err_msg = subsonic_resp.get("error", {}).get("message", "Unknown error")
                logger.warning(f"Navidrome scan trigger response status was not 'ok': {err_msg}")
                return False
        except Exception as e:
            logger.error(f"Failed to trigger Navidrome rescan: {e}")
            return False

    async def get_server_stats(self) -> dict:
        """Fetch server stats (total songs, albums, artists) via Subsonic API."""
        if not self.url or not self.username or (not self.password and not (self.token and self.salt)):
            return {"songs": 0, "albums": 0, "artists": 0}

        params = self._generate_auth_params()
        songs_count = 0
        albums_count = 0
        artists_count = 0

        try:
            client = get_http_client()
            # 1. Count artists via getArtists (more reliable than getIndexes)
            try:
                url_artists = f"{self.url}/rest/getArtists.view"
                resp = await client.get(url_artists, params=params)
                if resp.status_code == 200:
                    data = resp.json().get("subsonic-response", {})
                    if data.get("status") == "ok":
                        for idx in data.get("artists", {}).get("index", []):
                            artists_count += len(idx.get("artist", []))
            except Exception as e:
                logger.debug(f"Failed to count Navidrome artists: {e}")

            # 2. Count albums via getAlbumList2
            try:
                url_albums = f"{self.url}/rest/getAlbumList2.view"
                params_albums = {**params, "type": "newest", "size": 500}
                resp = await client.get(url_albums, params=params_albums)
                if resp.status_code == 200:
                    data = resp.json().get("subsonic-response", {})
                    if data.get("status") == "ok":
                        albums_list = data.get("albumList2", {}).get("album", [])
                        albums_count = len(albums_list)
            except Exception as e:
                logger.debug(f"Failed to count Navidrome albums: {e}")

            # 3. Fallback songs count estimate
            songs_count = albums_count * 10

            return {
                "songs": songs_count,
                "albums": albums_count,
                "artists": artists_count
            }
        except Exception as e:
            logger.error(f"Failed to fetch Navidrome server stats: {e}")
            return {"songs": 0, "albums": 0, "artists": 0}

    async def get_artists_list(self) -> list[str]:
        """Fetch all artist names from Navidrome."""
        if not self.url or not self.username or (not self.password and not (self.token and self.salt)):
            return []

        params = self._generate_auth_params()
        artists = []

        try:
            client = get_http_client()
            url_artists = f"{self.url}/rest/getArtists.view"
            resp = await client.get(url_artists, params=params)
            if resp.status_code == 200:
                data = resp.json().get("subsonic-response", {})
                if data.get("status") == "ok":
                    for idx in data.get("artists", {}).get("index", []):
                        for art in idx.get("artist", []):
                            if art.get("name"):
                                artists.append(art["name"])
            return artists
        except Exception as e:
            logger.error(f"Failed to fetch Navidrome artists list: {e}")
            return []

    async def get_all_artists(self) -> list[str]:
        """Alias for get_artists_list."""
        return await self.get_artists_list()

    async def get_starred_tracks(self) -> list[dict]:
        """Fetch all starred/liked tracks from Navidrome."""
        if not self.url or not self.username or (not self.password and not (self.token and self.salt)):
            return []

        params = self._generate_auth_params()
        tracks = []

        try:
            client = get_http_client()
            url = f"{self.url}/rest/getStarred2.view"
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json().get("subsonic-response", {})
                if data.get("status") == "ok":

                    starred_data = data.get("starred2") or data.get("starred") or {}
                    songs_list = starred_data.get("song", [])
                    if isinstance(songs_list, dict):
                        songs_list = [songs_list]
                    elif not isinstance(songs_list, list):
                        songs_list = []

                    for s in songs_list:
                        tracks.append({
                            "id": s.get("id"),
                            "artist": s.get("artist", ""),
                            "title": s.get("title", ""),
                            "album": s.get("album", ""),
                            "starred_at": s.get("starred"),
                            "mbid": s.get("musicBrainzId") or None
                        })
            return tracks

        except Exception as e:
            logger.error(f"Failed to fetch Navidrome starred tracks: {e}")
            return []

    async def get_history(self) -> list[dict]:
        """Fetch the play history/now playing from Subsonic/Navidrome."""
        params = self._generate_auth_params()
        url = f"{self.url}/rest/getNowPlaying.view"
        try:
            client = get_http_client()
            resp = await client.get(url, params=params)
            if resp.status_code == 404:
                logger.debug("getNowPlaying.view endpoint not available on Navidrome server.")
                return []
            resp.raise_for_status()
            data = resp.json()
            sub = data.get("subsonic-response", {})
            if sub.get("status") == "ok":
                hist = sub.get("nowPlaying", {}) or sub.get("history", {}) or sub.get("playQueue", {})
                entries = hist.get("entry", [])
                if isinstance(entries, dict):
                    return [entries]
                return entries or []
            return []
        except Exception as e:
            logger.debug(f"Could not fetch play history from Navidrome: {e}")
            return []

    async def get_top_albums(self) -> list[dict]:
        """Fetch the most frequently played albums from Navidrome."""
        params = self._generate_auth_params()
        params.update({"type": "frequent", "size": 10})
        url = f"{self.url}/rest/getAlbumList2.view"
        try:
            client = get_http_client()
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            sub = data.get("subsonic-response", {})
            if sub.get("status") == "ok":
                albums = sub.get("albumList2", {}).get("album", [])
                if isinstance(albums, dict):
                    return [albums]
                return albums or []
            return []
        except Exception as e:
            logger.error(f"Failed to fetch top albums from Navidrome: {e}")
            return []

    async def trigger_rescan(self, full_scan: bool = True) -> bool:
        """Trigger an immediate library rescan on Navidrome via startScan.view Subsonic API."""
        if not self.url or not self.username or (not self.password and not (self.token and self.salt)):
            return False
        params = self._generate_auth_params()
        if full_scan:
            params["fullScan"] = "true"
        url = f"{self.url}/rest/startScan.view"
        try:
            client = get_http_client()
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            sub = data.get("subsonic-response", {})
            if sub.get("status") == "ok":
                logger.info("Successfully triggered Navidrome library rescan (startScan.view, fullScan=true).")
                return True
            return False
        except Exception as e:
            logger.warning(f"Failed to trigger Navidrome library rescan: {e}")
            return False
