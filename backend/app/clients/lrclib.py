import httpx
from typing import Dict, Any, Optional, Tuple
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from backend.app.logger import get_logger
from backend.app.clients.http_client import get_http_client

logger = get_logger()

class LrcLibClient:
    def __init__(self, base_url: str = "https://lrclib.net", timeout: int = 20):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True
    )
    async def get_lyrics(self, artist: str, title: str, album: Optional[str] = None) -> Tuple[Optional[str], str]:
        """
        Search for lyrics on LRCLIB.
        Returns a tuple (lyrics_content, type) where type can be 'synced', 'plain', or 'missing'.
        """
        url = f"{self.base_url}/api/search"
        params = {
            "artist_name": artist,
            "track_name": title
        }
        if album:
            params["album_name"] = album
        
        logger.info(f"Searching lyrics for '{artist} - {title}' on LRCLIB...")
        
        client = get_http_client()
        resp = await client.get(url, params=params)
        
        if resp.status_code == 404:
            logger.info(f"No lyrics found (404) for '{artist} - {title}'")
            return None, "missing"
        
        resp.raise_for_status()
        results = resp.json()
        
        if not results:
            logger.info(f"No lyrics found (empty list) for '{artist} - {title}'")
            return None, "missing"
        
        # Select the first result
        best_match = results[0]
        
        synced = best_match.get("syncedLyrics")
        plain = best_match.get("plainLyrics")
        
        if synced and synced.strip():
            logger.info(f"Found synced lyrics for '{artist} - {title}'")
            return synced, "synced"
        elif plain and plain.strip():
            logger.info(f"Found plain lyrics (no sync) for '{artist} - {title}'")
            return plain, "plain"
        
        logger.info(f"Lyrics result was empty for '{artist} - {title}'")
        return None, "missing"

    async def search_lyrics(self, artist: str, title: str) -> list:
        """Search for lyrics on LRCLIB and return all candidate listings."""
        url = f"{self.base_url}/api/search"
        params = {
            "artist_name": artist,
            "track_name": title
        }
        try:
            client = get_http_client()
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                return resp.json()
            return []
        except Exception as e:
            logger.warning(f"LRCLIB search failed: {e}")
            return []
