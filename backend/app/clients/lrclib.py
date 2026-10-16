import asyncio
import time
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import httpx
from typing import Optional, Tuple
from backend.app.logger import get_logger
from backend.app.clients.http_client import get_http_client

logger = get_logger()
_request_lock = asyncio.Lock()
_next_request = 0.0
_unavailable_until = 0.0


class LrcLibUnavailable(httpx.RequestError):
    """Temporary provider failure, never a definitive lyrics-not-found result."""


def retry_after_seconds(response):
    value = response.headers.get('Retry-After', '')
    try:
        seconds = float(value)
        return max(0, seconds) if math.isfinite(seconds) else 0
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0

class LrcLibClient:
    def __init__(self, base_url: str = "https://lrclib.net", timeout: int = 20):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _search(self, params):
        global _next_request, _unavailable_until
        for attempt in range(3):
            async with _request_lock:
                if _unavailable_until > time.monotonic():
                    raise LrcLibUnavailable('LRCLIB temporarily unavailable; retry after the cooldown.')
                await asyncio.sleep(max(0, _next_request - time.monotonic()))
                _next_request = time.monotonic() + 1.0
                delay = 2 ** (attempt + 1)
                try:
                    client = await get_http_client()
                    response = await client.get(f"{self.base_url}/api/search", params=params, timeout=self.timeout)
                    if response.status_code == 429:
                        _next_request = time.monotonic() + max(60, retry_after_seconds(response))
                        response.raise_for_status()
                    if response.status_code == 404:
                        return []
                    if response.status_code == 408 or 500 <= response.status_code < 600:
                        delay = max(delay, retry_after_seconds(response))
                    response.raise_for_status()
                    results = response.json()
                    if not isinstance(results, list) or any(not isinstance(item, dict) for item in results):
                        raise ValueError('Invalid LRCLIB search response')
                    return results
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 408 and not 500 <= exc.response.status_code < 600:
                        raise
                    error = exc
                except (httpx.TransportError, ValueError) as exc:
                    error = exc
                # A long Retry-After closes the gate instead of holding a job asleep.
                if attempt == 2 or delay > 30:
                    _unavailable_until = time.monotonic() + max(60, delay)
                    _next_request = max(_next_request, _unavailable_until)
                    raise LrcLibUnavailable('LRCLIB unavailable after bounded retries; retry later.') from error
                detail = f'HTTP {error.response.status_code}' if isinstance(error, httpx.HTTPStatusError) else type(error).__name__
                logger.warning('LRCLIB temporary failure (%s); retry %s/3 in %.1fs',
                               detail, attempt + 2, delay)
                _next_request = max(_next_request, time.monotonic() + delay)

    async def find_automatic(self, artist, title, album='', duration=0, synced_only=False):
        """Conservative unattended matching; never use an album-only artist match."""
        import re
        from backend.app.artist_identity import name_key
        from backend.app.media_versions import remaster_identity
        normalize = lambda value: re.sub(r'[^\w]', '', value or '').casefold()
        def candidates(results):
            matches = []
            for item in results:
                if name_key(item.get('artistName') or '') != name_key(artist):
                    continue
                if normalize(remaster_identity(item.get('trackName') or '')[0]) != normalize(remaster_identity(title)[0]):
                    continue
                try:
                    candidate_duration = float(item.get('duration') or 0)
                except (ValueError, TypeError):
                    continue
                if duration and candidate_duration and abs(float(duration) - candidate_duration) > 5:
                    continue
                synced = item.get('syncedLyrics') or ''
                plain = item.get('plainLyrics') or ''
                if synced.strip() and re.search(r'\[\d{1,3}:\d{2}(?:\.\d+)?\]', synced):
                    matches.append((2, synced))
                elif not synced_only and plain.strip() and not item.get('instrumental'):
                    matches.append((1, plain))
            return matches
        results = await self.search_lyrics(artist, title, album, raise_errors=True, prefer_synced=True,
                                          stop_when=lambda items: any(rank == 2 for rank, _ in candidates(items)))
        matches = candidates(results)
        if not matches:
            return None, 'missing'
        priority, text = max(matches, key=lambda match: match[0])
        return text, 'synced' if priority == 2 else 'plain'

    async def get_lyrics(self, artist: str, title: str, album: Optional[str] = None) -> Tuple[Optional[str], str]:
        """
        Search for lyrics on LRCLIB.
        Returns a tuple (lyrics_content, type) where type can be 'synced', 'plain', or 'missing'.
        """
        params = {
            "artist_name": artist,
            "track_name": title
        }
        if album:
            params["album_name"] = album
        
        logger.info(f"Searching lyrics for '{artist} - {title}' on LRCLIB...")
        
        results = await self._search(params)
        
        if not results:
            logger.info(f"Strict lyrics lookup missed '{artist} - {title}'; trying album/title and title-only fallbacks")
            results = await self.search_lyrics(artist, title, album, include_strict=False, raise_errors=True)
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
                            include_strict: bool = True, *, raise_errors: bool = False,
                            prefer_synced: bool = False, stop_when=None) -> list:
        """Search LRCLIB progressively: artist+title, album+title, then title only."""
        try:
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
                items = await self._search(params)
                for item in items:
                    key = item.get("id") or (
                        (item.get("artistName") or "").casefold(),
                        (item.get("trackName") or "").casefold(),
                        round(float(item.get("duration") or 0)),
                    )
                    if key not in seen:
                        seen.add(key)
                        combined.append(item)
                if stop_when and stop_when(combined):
                    break
                if combined and params.get("artist_name") and not prefer_synced:
                    break
            return combined
        except Exception as e:
            if raise_errors:
                raise
            logger.warning(f"LRCLIB search failed: {e}")
            return []
