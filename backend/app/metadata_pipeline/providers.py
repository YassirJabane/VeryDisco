from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
import urllib.parse
from difflib import SequenceMatcher
from typing import Any, Awaitable, Callable, Optional

from backend.app.clients.http_client import get_http_client


MB_BASE = "https://musicbrainz.org/ws/2"
CAA_BASE = "https://coverartarchive.org"
ITUNES_SEARCH = "https://itunes.apple.com/search"
USER_AGENT = "VeryDisco/2.0 (release-centric metadata rebuild)"


def normalize(value: str) -> str:
    value = (value or "").replace("$", "s").casefold()
    return re.sub(r"[^\w]", "", value)


def artist_credit(credits: list[dict[str, Any]] | None) -> tuple[str, list[dict[str, str]]]:
    display: list[str] = []
    entities: list[dict[str, str]] = []
    for credit in credits or []:
        artist = credit.get("artist") or {}
        # Use the current canonical MusicBrainz artist name, not a historical
        # credited-as alias (for example "Travi$ Scott"). Join phrases still
        # come from the authoritative artist-credit relation.
        name = artist.get("name") or credit.get("name") or ""
        canonical = artist.get("name") or name
        if name:
            display.append(name)
            display.append(credit.get("joinphrase") or "")
        if canonical:
            entities.append({"name": canonical, "mbid": artist.get("id") or ""})
    return "".join(display).strip(), entities


class MusicBrainzReleaseProvider:
    """MusicBrainz release matcher with the public API rate limit enforced."""

    def __init__(
        self,
        cache_get: Optional[Callable[[str], Awaitable[Optional[Any]]]] = None,
        cache_set: Optional[Callable[[str, Any, int], Awaitable[None]]] = None,
    ) -> None:
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._cache_get = cache_get
        self._cache_set = cache_set

    async def _get(self, path: str, params: dict[str, Any]) -> Optional[dict[str, Any]]:
        async with self._lock:
            delay = 1.05 - (time.monotonic() - self._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            client = await get_http_client()
            response = await client.get(
                f"{MB_BASE}{path}",
                params=params,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            self._last_request = time.monotonic()
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def release_details(self, release_mbid: str) -> Optional[dict[str, Any]]:
        cache_key = f"musicbrainz:release:{release_mbid}"
        if self._cache_get:
            cached = await self._cache_get(cache_key)
            if cached is not None:
                return cached
        raw = await self._get(
            f"/release/{release_mbid}",
            {"inc": "recordings+artist-credits+release-groups+media+labels", "fmt": "json"},
        )
        result = self._normalize_release(raw) if raw else None
        if result and self._cache_set:
            await self._cache_set(cache_key, result, 30 * 24 * 3600)
        return result

    async def match_release(
        self,
        album: str,
        album_artist: str,
        local_tracks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        known_mbids = {track.get("release_mbid") for track in local_tracks if track.get("release_mbid")}
        if len(known_mbids) == 1:
            known = await self.release_details(next(iter(known_mbids)))
            if known:
                known["confidence"] = self._release_confidence(known, album, album_artist, local_tracks)
                return [known]
        query = f'release:"{album}"'
        if album_artist:
            query += f' AND artist:"{album_artist}"'
        search_key = f"musicbrainz:search:{normalize(album_artist)}:{normalize(album)}"
        data = await self._cache_get(search_key) if self._cache_get else None
        if data is None:
            data = await self._get("/release", {"query": query, "limit": 8, "fmt": "json"})
            if self._cache_set:
                await self._cache_set(search_key, data or {}, 7 * 24 * 3600)
        releases = (data or {}).get("releases", [])
        ranked = sorted(
            releases,
            key=lambda item: self._search_score(item, album, album_artist),
            reverse=True,
        )[:4]
        candidates: list[dict[str, Any]] = []
        for item in ranked:
            details = await self.release_details(item.get("id", ""))
            if not details:
                continue
            details["confidence"] = self._release_confidence(details, album, album_artist, local_tracks)
            candidates.append(details)
            # Most well-tagged albums need only search + one release request.
            # Alternatives are loaded only when the best candidate is uncertain.
            if len(candidates) == 1 and details["confidence"] >= 0.90:
                break
        candidates.sort(key=lambda item: item["confidence"], reverse=True)
        return candidates

    @staticmethod
    def _search_score(item: dict[str, Any], album: str, album_artist: str) -> float:
        title = SequenceMatcher(None, normalize(album), normalize(item.get("title", ""))).ratio()
        display, entities = artist_credit(item.get("artist-credit"))
        names = [display, *(entity["name"] for entity in entities)]
        artist = max(
            (SequenceMatcher(None, normalize(album_artist), normalize(name)).ratio() for name in names if name),
            default=0.0,
        )
        official = 0.05 if item.get("status") == "Official" else 0.0
        return title * 0.65 + artist * 0.30 + official

    @staticmethod
    def _normalize_release(raw: dict[str, Any]) -> dict[str, Any]:
        album_artist, album_entities = artist_credit(raw.get("artist-credit"))
        tracks: list[dict[str, Any]] = []
        media = raw.get("media") or []
        for disc_index, medium in enumerate(media, start=1):
            disc_number = int(medium.get("position") or disc_index)
            medium_tracks = medium.get("tracks") or medium.get("track") or []
            for track_index, track in enumerate(medium_tracks, start=1):
                recording = track.get("recording") or {}
                display, entities = artist_credit(track.get("artist-credit") or recording.get("artist-credit"))
                length_ms = track.get("length") or recording.get("length") or 0
                tracks.append({
                    "title": recording.get("title") or track.get("title") or "",
                    "artist": display or album_artist,
                    "artists": entities or album_entities,
                    "recording_mbid": recording.get("id") or "",
                    "track": int(track.get("position") or track_index),
                    "disc": disc_number,
                    "duration": round(float(length_ms) / 1000, 2) if length_ms else 0,
                })
        release_group = raw.get("release-group") or {}
        labels = [
            (entry.get("label") or {}).get("name")
            for entry in (raw.get("label-info") or [])
            if (entry.get("label") or {}).get("name")
        ]
        return {
            "release_mbid": raw.get("id") or "",
            "release_group_mbid": release_group.get("id") or "",
            "album": raw.get("title") or "",
            "album_artist": album_artist,
            "album_artists": album_entities,
            "date": raw.get("date") or release_group.get("first-release-date") or "",
            "country": raw.get("country") or "",
            "status": raw.get("status") or "",
            "barcode": raw.get("barcode") or "",
            "labels": labels,
            "disc_total": len(media) or 1,
            "track_total": len(tracks),
            "tracks": tracks,
            "artwork_url": f"{CAA_BASE}/release/{raw.get('id')}/front",
        }

    @staticmethod
    def _release_confidence(
        release: dict[str, Any],
        album: str,
        album_artist: str,
        local_tracks: list[dict[str, Any]],
    ) -> float:
        title_score = SequenceMatcher(None, normalize(album), normalize(release["album"])).ratio()
        artist_names = [release["album_artist"], *(a["name"] for a in release["album_artists"])]
        artist_score = max(
            (SequenceMatcher(None, normalize(album_artist), normalize(name)).ratio() for name in artist_names if name),
            default=0.0,
        )
        remote_titles = {normalize(track["title"]) for track in release["tracks"] if track["title"]}
        local_titles = {normalize(track.get("title", "")) for track in local_tracks if track.get("title")}
        overlap = len(local_titles & remote_titles) / max(1, len(local_titles))
        count_score = 1.0 - min(abs(len(local_tracks) - len(release["tracks"])) / max(1, len(release["tracks"])), 1.0)
        official = 1.0 if release.get("status") == "Official" else 0.5
        return round(title_score * 0.25 + artist_score * 0.20 + overlap * 0.35 + count_score * 0.15 + official * 0.05, 4)


class ArtworkProvider:
    """Artwork chain: CAA release, CAA release-group, then strict iTunes fallback."""

    async def fetch(
        self,
        release_mbid: str,
        release_group_mbid: str,
        album_artist: str,
        album: str,
        allow_itunes: bool = True,
    ) -> tuple[Optional[bytes], Optional[str], Optional[str]]:
        client = await get_http_client()
        urls = [
            (f"{CAA_BASE}/release/{release_mbid}/front", "cover-art-archive:release"),
        ]
        if release_group_mbid:
            urls.append((f"{CAA_BASE}/release-group/{release_group_mbid}/front", "cover-art-archive:release-group"))
        for url, source in urls:
            response = await client.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
            if response.status_code == 200 and self._image_mime(response.content):
                return response.content, self._image_mime(response.content), source
        if allow_itunes:
            query = urllib.parse.quote(f"{album_artist} {album}")
            response = await client.get(f"{ITUNES_SEARCH}?term={query}&entity=album&limit=8")
            if response.status_code == 200:
                for result in response.json().get("results", []):
                    if normalize(result.get("collectionName", "")) != normalize(album):
                        continue
                    if normalize(result.get("artistName", "")) != normalize(album_artist):
                        continue
                    url = (result.get("artworkUrl100") or "").replace("100x100", "1200x1200")
                    if url:
                        art = await client.get(url, follow_redirects=True)
                        if art.status_code == 200 and self._image_mime(art.content):
                            return art.content, self._image_mime(art.content), "itunes:verified-album"
        return None, None, None

    @staticmethod
    def _image_mime(data: bytes) -> Optional[str]:
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        return None


class AcoustIDRecordingVerifier:
    """Optional recording verifier. It never selects an album release."""

    async def verify(self, path: str, expected_recording_mbid: str, api_key: str) -> dict[str, Any]:
        if not api_key or not expected_recording_mbid:
            return {"status": "skipped", "reason": "missing API key or recording MBID"}
        try:
            process = await asyncio.create_subprocess_exec(
                "fpcalc", "-json", path,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            if process.returncode != 0:
                return {"status": "unavailable", "reason": "fpcalc failed"}
            fingerprint = json.loads(stdout.decode("utf-8"))
            client = await get_http_client()
            response = await client.post("https://api.acoustid.org/v2/lookup", data={
                "client": api_key,
                "meta": "recordingids",
                "duration": int(fingerprint["duration"]),
                "fingerprint": fingerprint["fingerprint"],
            })
            response.raise_for_status()
            mbids = {
                recording.get("id")
                for result in response.json().get("results", [])
                if float(result.get("score", 0)) >= 0.6
                for recording in result.get("recordings", [])
                if recording.get("id")
            }
            if not mbids:
                return {"status": "unknown", "reason": "no AcoustID recording match"}
            return {
                "status": "match" if expected_recording_mbid in mbids else "mismatch",
                "recording_mbids": sorted(mbids),
            }
        except FileNotFoundError:
            return {"status": "unavailable", "reason": "fpcalc is not installed"}
        except Exception as exc:
            return {"status": "unavailable", "reason": str(exc)}
