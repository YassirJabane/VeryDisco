"""Optional, recording-scoped tempo proposals from the AcousticBrainz archive.

An MBID is an identity, not an audio feature. Never substitute an artist/album
tempo, guess by title, or manufacture a value when this archive has no analysis.
"""
from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Awaitable, Callable, Optional
from uuid import UUID

import httpx

from backend.app.clients.http_client import get_http_client
from backend.app.logger import get_logger
from .extras import normalize_extras

logger = get_logger()


class RecordingBpmProvider:
    def __init__(
        self,
        cache_get: Optional[Callable[[str], Awaitable[Optional[Any]]]] = None,
        cache_set: Optional[Callable[[str, Any, int], Awaitable[None]]] = None,
    ) -> None:
        self._cache_get, self._cache_set = cache_get, cache_set
        self._memory: dict[str, tuple[float, dict]] = {}
        self._lock = asyncio.Lock()
        self._next_request = 0.0
        self._unavailable_until = 0.0

    async def lookup(self, recording_mbid: str, duration: float = 0) -> dict:
        try:
            mbid = str(UUID(recording_mbid))
        except (ValueError, TypeError, AttributeError):
            return {"status": "missing_mbid"}
        key = f"acousticbrainz:bpm:v1:{mbid}"
        async with self._lock:
            cached = self._memory.get(key)
            result = cached[1] if cached and cached[0] > time.monotonic() else None
            if result is None and self._cache_get:
                result = await self._cache_get(key)
            if result is None:
                if time.monotonic() < self._unavailable_until:
                    return {"status": "unavailable"}
                await asyncio.sleep(max(0, self._next_request - time.monotonic()))
                self._next_request = time.monotonic() + 0.3
                try:
                    client = await get_http_client()
                    response = await client.get(
                        f"https://acousticbrainz.org/api/v1/{mbid}/low-level",
                        timeout=5.0,
                    )
                    if response.status_code == 404:
                        result = {"status": "not_found"}
                    else:
                        response.raise_for_status()
                        raw = response.json()
                        bpm = normalize_extras({"bpm": (raw.get("rhythm") or {}).get("bpm")}).get("bpm")
                        result = {"status": "not_found"}
                        if bpm:
                            metadata_ids = (raw.get("metadata") or {}).get("tags", {}).get("musicbrainz_recordingid", [])
                            if isinstance(metadata_ids, str):
                                metadata_ids = [metadata_ids]
                            if metadata_ids and mbid not in {str(value).lower() for value in metadata_ids}:
                                raise ValueError("Archive returned a different recording MBID")
                            length = (raw.get("metadata") or {}).get("audio_properties", {}).get("length", 0)
                            length = float(length or 0)
                            if not math.isfinite(length) or length < 0:
                                raise ValueError("Invalid analysis duration")
                            result = {"status": "found", "bpm": [format(float(bpm[0]), '.2f').rstrip('0').rstrip('.')], "duration": length,
                                      "source": "AcousticBrainz audio analysis", "recording_mbid": mbid}
                except (httpx.HTTPError, ValueError, TypeError, AttributeError) as error:
                    # A provider outage must not add a timeout per library track.
                    self._unavailable_until = time.monotonic() + 60
                    if isinstance(error, httpx.HTTPStatusError):
                        try:
                            delay = float(error.response.headers.get('Retry-After') or error.response.headers.get('X-RateLimit-Reset-In') or 60)
                            if math.isfinite(delay):
                                self._unavailable_until = time.monotonic() + max(60, min(delay, 600))
                        except ValueError:
                            pass
                    logger.warning("BPM archive unavailable for %s: %s", mbid, error)
                    result = {"status": "unavailable"}
                ttl = {"found": 30 * 86400, "not_found": 7 * 86400, "unavailable": 300}[result["status"]]
                if len(self._memory) >= 2048:
                    self._memory.pop(next(iter(self._memory)))
                self._memory[key] = (time.monotonic() + ttl, result)
                if self._cache_set:
                    await self._cache_set(key, result, ttl)
            # Duration is a per-track guard, never part of the shared MBID cache.
            length = result.get("duration", 0)
            if result.get("status") == "found" and duration and length and abs(duration - length) > max(5, duration * .02):
                return {"status": "duration_mismatch"}
            return dict(result)
