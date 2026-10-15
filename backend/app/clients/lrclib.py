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
        
        client = await get_http_client()
        resp = await client.get(url, params=params)
        
        if resp.status_code == 404:
            results = []
        else:
            resp.raise_for_status()
            results = resp.json()
        
        if not results:
            logger.info(f"Strict lyrics lookup missed '{artist} - {title}'; trying album/title and title-only fallbacks")
            results = await self.search_lyrics(artist, title, album, include_strict=False)
            if not results:
                logger.info(f"No lyrics found for '{artist} - {title}'")
                return None, "missing"
        
        import re
        def _normalize(s: str) -> str:
            return re.sub(r'[^\w]', '', s).lower() if s else ""
            
        from backend.app.media_versions import remaster_identity
        # Remaster labels describe mastering, not different words. This relaxed
        # comparison is lyrics-only; download/library matching stays strict.
        n_artist, n_title, n_album = _normalize(artist), _normalize(remaster_identity(title)[0]), _normalize(remaster_identity(album or '')[0])
        matches = []
        for result in results:
            r_artist = _normalize(result.get("artistName", ""))
            r_title = _normalize(remaster_identity(result.get("trackName", ""))[0])
            artist_matches = bool(n_artist and r_artist and n_artist == r_artist)
            album_matches = bool(n_album and n_album == _normalize(remaster_identity(result.get("albumName", ""))[0]))
            if n_title and n_title == r_title and (artist_matches or album_matches):
                matches.append((artist_matches, album_matches, bool(result.get("syncedLyrics")), result))
        if not matches:
            logger.info("No confident lyrics match for '%s - %s'", artist, title)
            return None, "missing"
        best_match = max(matches, key=lambda item: item[:3])[3]

        if best_match.get("instrumental") is True:
            logger.info(f"LRCLIB identifies '{artist} - {title}' as instrumental")
            return None, "instrumental"
        
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

    async def search_lyrics(self, artist: str, title: str, album: Optional[str] = None,
                            include_strict: bool = True) -> list:
        """Search LRCLIB progressively: artist+title, album+title, then title only."""
        url = f"{self.base_url}/api/search"
        try:
            client = await get_http_client()
            strategies = []
            if include_strict:
                strategies.append({"artist_name": artist, "track_name": title})
            if album:
                strategies.append({"album_name": album, "track_name": title})
            strategies.append({"track_name": title})
            from backend.app.media_versions import remaster_identity
            base_title, version = remaster_identity(title)
            base_album, _ = remaster_identity(album or '')
            if version is not None:
                strategies.append({"artist_name": artist, "track_name": base_title})
                if base_album:
                    strategies.append({"album_name": base_album, "track_name": base_title})
            seen, combined = set(), []
            for params in strategies:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    continue
                for item in resp.json() or []:
                    key = item.get("id") or (
                        (item.get("artistName") or "").casefold(),
                        (item.get("trackName") or "").casefold(),
                        round(float(item.get("duration") or 0)),
                    )
                    if key not in seen:
                        seen.add(key)
                        combined.append(item)
                if combined and params.get("artist_name"):
                    break
            return combined
        except Exception as e:
            logger.warning(f"LRCLIB search failed: {e}")
            return []
