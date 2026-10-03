from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from backend.app.logger import get_logger
from .providers import AcoustIDRecordingVerifier, ArtworkProvider, MusicBrainzReleaseProvider, normalize
from .tags import AUDIO_SUFFIXES, read_track, write_track


logger = get_logger()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".metadata-plan-", suffix=".json", dir=str(path.parent))
    os.close(fd)
    temp = Path(name)
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(20):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                # Windows can briefly deny replacement while the status API is
                # reading the previous JSON file. Linux/Docker normally does
                # not need this, but a bounded retry keeps the update atomic.
                if os.name != "nt" or attempt == 19:
                    raise
                time.sleep(0.01)
    finally:
        temp.unlink(missing_ok=True)


def _safe_user(value: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", str(value))


class MetadataRebuildService:
    """Persistent, release-centric scanner/applicator with per-album rollback."""

    def __init__(
        self,
        state_root: Path,
        provider: Optional[MusicBrainzReleaseProvider] = None,
        artwork: Optional[ArtworkProvider] = None,
        verifier: Optional[AcoustIDRecordingVerifier] = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.provider = provider or MusicBrainzReleaseProvider()
        self.artwork = artwork or ArtworkProvider()
        self.verifier = verifier or AcoustIDRecordingVerifier()
        self._locks: dict[str, asyncio.Lock] = {}
        self._cancel_events: dict[Any, threading.Event] = {}

    def _user_dir(self, user_id: str) -> Path:
        return self.state_root / _safe_user(user_id)

    def _status_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "status.json"

    def _plans_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "plans.json"

    def _backup_path(self, user_id: str, plan_id: str) -> Path:
        return self._user_dir(user_id) / "backups" / f"{_safe_user(plan_id)}.json"

    def status(self, user_id: str) -> dict[str, Any]:
        path = self._status_path(user_id)
        if not path.exists():
            return {"status": "idle", "processed": 0, "total": 0, "message": "No scan has run."}
        return json.loads(path.read_text(encoding="utf-8"))

    def plans(self, user_id: str) -> list[dict[str, Any]]:
        path = self._plans_path(user_id)
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def plan(self, user_id: str, plan_id: str) -> dict[str, Any]:
        found = next((item for item in self.plans(user_id) if item["id"] == plan_id), None)
        if not found:
            raise KeyError(plan_id)
        return found

    def mark_queued(self, user_id: str) -> None:
        _atomic_json(self._status_path(user_id), {
            "status": "queued", "processed": 0, "total": 0,
            "started_at": _now(), "message": "Metadata inventory is queued.",
        })

    def reconcile_orphaned(self, user_id: str, has_live_task: bool) -> dict[str, Any]:
        """Turn persisted active state from a previous process into a restartable state."""
        status = self.status(user_id)
        if not has_live_task and status.get("status") in {"queued", "scanning", "cancelling"}:
            status.update({
                "status": "interrupted",
                "finished_at": _now(),
                "message": (
                    "The previous metadata scan was interrupted by an application restart. "
                    "No media files were modified; you can start it again."
                ),
            })
            _atomic_json(self._status_path(user_id), status)
        return status

    def request_cancel(self, user_id: Any) -> None:
        event = self._cancel_events.get(user_id)
        if event:
            event.set()
        status = self.status(user_id)
        if status.get("status") in {"queued", "scanning"}:
            status.update({"status": "cancelling", "message": "Stopping metadata scan…"})
            _atomic_json(self._status_path(user_id), status)

    async def scan(
        self,
        user_id: str,
        music_root: Path,
        aliases: Optional[dict[str, str]] = None,
        verify_acoustid: bool = False,
        acoustid_api_key: str = "",
        indexed_tracks: Optional[list[dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        if lock.locked():
            raise RuntimeError("A metadata rebuild operation is already running.")
        async with lock:
            root = music_root.resolve()
            job_id = str(uuid.uuid4())
            cancel_event = threading.Event()
            self._cancel_events[user_id] = cancel_event
            status = {
                "job_id": job_id, "status": "scanning", "phase": "inventory",
                "processed": 0, "total": 0, "files_scanned": 0, "folders_found": 0,
                "started_at": _now(), "message": "Reading audio tags from the library.",
            }
            _atomic_json(self._status_path(user_id), status)
            logger.info("Metadata rebuild %s: inventory started for %s", job_id, root)
            plans: list[dict[str, Any]] = []
            try:
                def inventory_progress(files_scanned: int, folders_found: int) -> None:
                    status.update({
                        "files_scanned": files_scanned,
                        "folders_found": folders_found,
                        "message": (
                            f"Inventory: read {files_scanned} audio files in "
                            f"{folders_found} album folders."
                        ),
                    })
                    _atomic_json(self._status_path(user_id), status)
                    if files_scanned and files_scanned % 250 == 0:
                        logger.info(
                            "Metadata rebuild %s: inventory read %d files in %d folders",
                            job_id, files_scanned, folders_found,
                        )

                if indexed_tracks:
                    groups = await asyncio.to_thread(
                        self._inventory_from_index, root, aliases or {}, indexed_tracks,
                        inventory_progress, cancel_event,
                    )
                else:
                    groups = await asyncio.to_thread(
                        self._inventory, root, aliases or {}, inventory_progress, cancel_event
                    )
                status.update({
                    "phase": "resolving", "processed": 0, "total": len(groups),
                    "message": f"Inventory complete. Resolving {len(groups)} albums with MusicBrainz.",
                })
                _atomic_json(self._status_path(user_id), status)
                logger.info(
                    "Metadata rebuild %s: inventory complete (%d files, %d albums)",
                    job_id, status["files_scanned"], len(groups),
                )
                provider_errors = 0
                for index, group in enumerate(groups, start=1):
                    if cancel_event.is_set():
                        raise asyncio.CancelledError
                    logger.info(
                        "Metadata rebuild %s: resolving album %d/%d: %s — %s",
                        job_id, index, len(groups), group["album_artist"], group["album"],
                    )
                    try:
                        candidates = await self.provider.match_release(
                            group["album"], group["album_artist"], group["tracks"]
                        )
                        plan = self._build_plan(root, group, candidates)
                    except Exception as exc:
                        # One provider/network failure must not discard plans
                        # already built for the rest of the library.
                        provider_errors += 1
                        plan = self._build_plan(root, group, [])
                        plan["provider_error"] = str(exc)
                        logger.warning(
                            "Metadata rebuild %s: provider failed for %s — %s: %s",
                            job_id, group["album_artist"], group["album"], exc,
                        )
                    if verify_acoustid and plan.get("selected_release"):
                        for change in plan["tracks"]:
                            proposed = change.get("proposed")
                            if proposed and proposed.get("recording_mbid"):
                                change["acoustid"] = await self.verifier.verify(
                                    change["path"], proposed["recording_mbid"], acoustid_api_key
                                )
                                if change["acoustid"]["status"] == "mismatch":
                                    plan["confidence"] = min(plan["confidence"], 0.49)
                                    plan["review"] = "required"
                    plans.append(plan)
                    status.update({
                        "processed": index,
                        "message": f"Resolved {group['album_artist']} — {group['album']}",
                    })
                    _atomic_json(self._status_path(user_id), status)
                    _atomic_json(self._plans_path(user_id), plans)
                status.update({
                    "status": "completed", "finished_at": _now(),
                    "provider_errors": provider_errors,
                    "message": (
                        f"Created {len(plans)} album plans without modifying media files"
                        + (f"; {provider_errors} provider lookups need retry." if provider_errors else ".")
                    ),
                })
                _atomic_json(self._status_path(user_id), status)
                logger.info("Metadata rebuild %s completed: %d album plans", job_id, len(plans))
                return plans
            except asyncio.CancelledError:
                cancel_event.set()
                status.update({
                    "status": "cancelled", "finished_at": _now(),
                    "message": "Metadata scan cancelled. Media files were not modified.",
                })
                _atomic_json(self._status_path(user_id), status)
                logger.warning("Metadata rebuild %s cancelled", job_id)
                raise
            except Exception as exc:
                status.update({"status": "failed", "finished_at": _now(), "message": str(exc)})
                _atomic_json(self._status_path(user_id), status)
                logger.exception("Metadata rebuild %s failed", job_id)
                raise
            finally:
                self._cancel_events.pop(user_id, None)

    def _inventory(
        self,
        root: Path,
        aliases: dict[str, str],
        progress: Optional[Callable[[int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> list[dict[str, Any]]:
        alias_map = {normalize(key): value for key, value in aliases.items()}
        folders: dict[Path, list[dict[str, Any]]] = {}
        files_scanned = 0
        for current, dirs, files in os.walk(root):
            if cancel_event and cancel_event.is_set():
                raise asyncio.CancelledError
            dirs[:] = [d for d in dirs if not d.startswith(".") and d.casefold() not in {"playlists", "explore"}]
            audio = [Path(current) / name for name in files if Path(name).suffix.lower() in AUDIO_SUFFIXES]
            if audio:
                tracks = []
                for path in sorted(audio):
                    if cancel_event and cancel_event.is_set():
                        raise asyncio.CancelledError
                    tracks.append(read_track(path))
                    files_scanned += 1
                    if progress and files_scanned % 25 == 0:
                        progress(files_scanned, len(folders) + 1)
                folders[Path(current)] = tracks
                if progress:
                    progress(files_scanned, len(folders))
        groups: list[dict[str, Any]] = []
        for folder, tracks in sorted(folders.items(), key=lambda item: str(item[0])):
            albums = [track["album"] for track in tracks if track["album"]]
            album = Counter(albums).most_common(1)[0][0] if albums else folder.name
            raw_album_artists = [track["album_artist"] for track in tracks if track["album_artist"]]
            raw_artists = [track["artist"] for track in tracks if track["artist"]]
            candidate = Counter(raw_album_artists).most_common(1)[0][0] if raw_album_artists else (
                Counter(raw_artists).most_common(1)[0][0] if raw_artists else folder.parent.name
            )
            # Featuring belongs to the track credit, never to the album grouping artist.
            candidate = re.split(r"(?i)\s+(?:feat\.?|ft\.?|featuring)\s+", candidate)[0].strip()
            canonical = alias_map.get(normalize(candidate), candidate)
            identity = hashlib.sha256("\n".join(track["path"] for track in tracks).encode()).hexdigest()[:20]
            groups.append({
                "id": identity, "folder": str(folder), "album": album,
                "album_artist": canonical, "tracks": tracks,
                "variants": {
                    "albums": sorted(set(albums)),
                    "album_artists": sorted(set(raw_album_artists)),
                    "dates": sorted(set(track["date"] for track in tracks if track["date"])),
                },
            })
        return groups

    def _inventory_from_index(
        self,
        root: Path,
        aliases: dict[str, str],
        rows: list[dict[str, Any]],
        progress: Optional[Callable[[int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> list[dict[str, Any]]:
        """Build album groups from the unified index, reopening changed files only."""
        alias_map = {normalize(key): value for key, value in aliases.items()}
        folders: dict[Path, list[dict[str, Any]]] = {}
        for index, row in enumerate(rows, start=1):
            if cancel_event and cancel_event.is_set():
                raise asyncio.CancelledError
            path = Path(row["filepath"])
            if not path.exists() or not path.resolve().is_relative_to(root):
                continue
            stat = path.stat()
            if (int(row.get("size") or -1) != stat.st_size
                    or int(row.get("mtime_ns") or -1) != stat.st_mtime_ns):
                track = read_track(path)
            else:
                try:
                    artists = json.loads(row.get("artists_json") or "[]")
                except Exception:
                    artists = []
                track = {
                    "path": str(path), "title": row.get("title") or path.stem,
                    "artist": row.get("artist") or "", "artists": artists,
                    "album": row.get("album") or "", "album_artist": row.get("album_artist") or "",
                    "album_artist_mbid": "", "date": row.get("year") or "",
                    "track": row.get("track_num") or 0, "track_total": row.get("total_tracks") or 0,
                    "disc": row.get("disc_num") or 1, "disc_total": row.get("total_discs") or 1,
                    "release_mbid": row.get("album_mbid") or "",
                    "recording_mbid": row.get("track_mbid") or "",
                    "compilation": bool(row.get("compilation")), "duration": row.get("duration") or 0,
                    "mtime_ns": stat.st_mtime_ns, "size": stat.st_size,
                }
            folders.setdefault(path.parent, []).append(track)
            if progress and (index % 100 == 0 or index == len(rows)):
                progress(index, len(folders))
        groups = []
        for folder, tracks in sorted(folders.items(), key=lambda item: str(item[0])):
            albums = [track["album"] for track in tracks if track["album"]]
            album = Counter(albums).most_common(1)[0][0] if albums else folder.name
            raw_album_artists = [track["album_artist"] for track in tracks if track["album_artist"]]
            raw_artists = [track["artist"] for track in tracks if track["artist"]]
            candidate = Counter(raw_album_artists).most_common(1)[0][0] if raw_album_artists else (
                Counter(raw_artists).most_common(1)[0][0] if raw_artists else folder.parent.name
            )
            candidate = re.split(r"(?i)\s+(?:feat\.?|ft\.?|featuring)\s+", candidate)[0].strip()
            canonical = alias_map.get(normalize(candidate), candidate)
            identity = hashlib.sha256("\n".join(track["path"] for track in tracks).encode()).hexdigest()[:20]
            groups.append({
                "id": identity, "folder": str(folder), "album": album, "album_artist": canonical,
                "tracks": tracks, "variants": {
                    "albums": sorted(set(albums)), "album_artists": sorted(set(raw_album_artists)),
                    "dates": sorted(set(track["date"] for track in tracks if track["date"])),
                },
            })
        return groups

    def _build_plan(
        self,
        root: Path,
        group: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        winner = candidates[0] if candidates else None
        changes: list[dict[str, Any]] = []
        matched = 0
        if winner:
            remaining = list(winner["tracks"])
            for local in group["tracks"]:
                remote = self._match_track(local, remaining)
                proposed = None
                if remote:
                    remaining.remove(remote)
                    matched += 1
                    album_entities = winner.get("album_artists") or []
                    album_artist_mbid = (album_entities or [{}])[0].get("mbid", "")
                    proposed = {
                        "title": remote["title"], "artist": remote["artist"],
                        "artists": [item["name"] for item in remote["artists"]],
                        "album": winner["album"], "album_artist": winner["album_artist"],
                        "album_artists": [item["name"] for item in album_entities],
                        "album_artist_mbid": album_artist_mbid,
                        "album_artist_mbids": [item["mbid"] for item in album_entities if item.get("mbid")],
                        "date": winner["date"],
                        "track": remote["track"], "track_total": winner["track_total"],
                        "disc": remote["disc"], "disc_total": winner["disc_total"],
                        "release_mbid": winner["release_mbid"],
                        "recording_mbid": remote["recording_mbid"],
                        "compilation": normalize(winner["album_artist"]) == normalize("Various Artists"),
                    }
                changes.append({
                    "path": local["path"], "expected_mtime_ns": local["mtime_ns"],
                    "expected_size": local["size"], "current": self._public_tags(local),
                    "proposed": proposed,
                })
        else:
            changes = [{
                "path": local["path"], "expected_mtime_ns": local["mtime_ns"],
                "expected_size": local["size"], "current": self._public_tags(local), "proposed": None,
            } for local in group["tracks"]]
        coverage = matched / max(1, len(group["tracks"]))
        confidence = round(min(float(winner.get("confidence", 0)) if winner else 0, coverage), 4)
        return {
            "id": group["id"], "folder": group["folder"], "album": group["album"],
            "album_artist": group["album_artist"], "variants": group["variants"],
            "confidence": confidence,
            "review": "automatic" if confidence >= 0.90 else ("required" if winner else "unmatched"),
            "state": "planned", "selected_release": winner, "candidates": candidates,
            "matched_tracks": matched, "total_tracks": len(group["tracks"]), "tracks": changes,
            "created_at": _now(), "root": str(root),
        }

    @staticmethod
    def _match_track(local: dict[str, Any], remaining: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if local.get("track"):
            numbered = [
                item for item in remaining
                if item["track"] == local["track"] and item["disc"] == (local.get("disc") or 1)
            ]
            if len(numbered) == 1:
                title_similarity = _similarity(local.get("title", ""), numbered[0]["title"])
                if title_similarity >= 0.55:
                    return numbered[0]
        titled = [item for item in remaining if normalize(item["title"]) == normalize(local.get("title", ""))]
        if len(titled) == 1:
            return titled[0]
        duration_matches = [
            item for item in titled
            if local.get("duration") and item.get("duration") and abs(local["duration"] - item["duration"]) <= 5
        ]
        return duration_matches[0] if len(duration_matches) == 1 else None

    @staticmethod
    def _public_tags(track: dict[str, Any]) -> dict[str, Any]:
        excluded = {"path", "cover", "mtime_ns", "size", "duration"}
        return {key: value for key, value in track.items() if key not in excluded}

    async def select_release(self, user_id: str, plan_id: str, release_mbid: str) -> dict[str, Any]:
        plans = self.plans(user_id)
        index = next((i for i, plan in enumerate(plans) if plan["id"] == plan_id), None)
        if index is None:
            raise KeyError(plan_id)
        details = await self.provider.release_details(release_mbid)
        if not details:
            raise ValueError("MusicBrainz release was not found.")
        details["confidence"] = 1.0
        group = {
            "id": plans[index]["id"], "folder": plans[index]["folder"],
            "album": plans[index]["album"], "album_artist": plans[index]["album_artist"],
            "variants": plans[index]["variants"],
            "tracks": [read_track(Path(change["path"])) for change in plans[index]["tracks"]],
        }
        rebuilt = self._build_plan(Path(plans[index]["root"]), group, [details])
        rebuilt["review"] = "selected"
        plans[index] = rebuilt
        _atomic_json(self._plans_path(user_id), plans)
        return rebuilt

    async def apply(
        self,
        user_id: str,
        plan_id: str,
        music_root: Path,
        include_artwork: bool = True,
        allow_itunes_artwork: bool = True,
        rescan: Optional[Callable[[], Awaitable[Any]]] = None,
    ) -> dict[str, Any]:
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        if lock.locked():
            raise RuntimeError("A metadata rebuild operation is already running.")
        async with lock:
            plans = self.plans(user_id)
            index = next((i for i, plan in enumerate(plans) if plan["id"] == plan_id), None)
            if index is None:
                raise KeyError(plan_id)
            plan = plans[index]
            release = plan.get("selected_release")
            if not release or plan.get("matched_tracks") != plan.get("total_tracks"):
                raise ValueError("Every local track must be matched to the selected release before applying.")
            root = music_root.resolve()
            backup = {"plan_id": plan_id, "created_at": _now(), "tracks": []}
            for change in plan["tracks"]:
                path = Path(change["path"]).resolve()
                if not path.is_relative_to(root):
                    raise PermissionError("Track is outside the user's library.")
                stat = path.stat()
                if stat.st_mtime_ns != change["expected_mtime_ns"] or stat.st_size != change["expected_size"]:
                    raise RuntimeError(f"File changed after scan: {path}")
                backup["tracks"].append(read_track(path, include_cover=True))
            _atomic_json(self._backup_path(user_id, plan_id), backup)

            cover = None
            cover_source = None
            if include_artwork:
                cover, _, cover_source = await self.artwork.fetch(
                    release["release_mbid"], release.get("release_group_mbid", ""),
                    release["album_artist"], release["album"], allow_itunes_artwork,
                )
            written: list[Path] = []
            try:
                for change in plan["tracks"]:
                    path = Path(change["path"])
                    await asyncio.to_thread(
                        write_track, path, change["proposed"], cover,
                        bool(include_artwork and cover),
                    )
                    written.append(path)
            except Exception:
                await asyncio.to_thread(self._restore_backup, backup, root, {str(path) for path in written})
                raise
            plan["state"] = "applied"
            plan["applied_at"] = _now()
            plan["artwork_source"] = cover_source
            plans[index] = plan
            _atomic_json(self._plans_path(user_id), plans)
            if rescan:
                await rescan()
            return {"status": "applied", "updated_tracks": len(written), "artwork_source": cover_source}

    async def rollback(
        self,
        user_id: str,
        plan_id: str,
        music_root: Path,
        rescan: Optional[Callable[[], Awaitable[Any]]] = None,
    ) -> dict[str, Any]:
        backup_path = self._backup_path(user_id, plan_id)
        if not backup_path.exists():
            raise FileNotFoundError("No rollback backup exists for this album.")
        backup = json.loads(backup_path.read_text(encoding="utf-8"))
        root = music_root.resolve()
        restored = await asyncio.to_thread(self._restore_backup, backup, root, None)
        plans = self.plans(user_id)
        for plan in plans:
            if plan["id"] == plan_id:
                plan["state"] = "rolled_back"
                plan["rolled_back_at"] = _now()
        _atomic_json(self._plans_path(user_id), plans)
        if rescan:
            await rescan()
        return {"status": "rolled_back", "restored_tracks": restored}

    @staticmethod
    def _restore_backup(backup: dict[str, Any], root: Path, only: Optional[set[str]]) -> int:
        restored = 0
        for snapshot in backup["tracks"]:
            path = Path(snapshot["path"]).resolve()
            if only is not None and str(path) not in only:
                continue
            if not path.is_relative_to(root) or not path.exists():
                continue
            cover = base64.b64decode(snapshot.get("cover") or "") if snapshot.get("cover") else None
            write_track(path, snapshot, cover, replace_cover=True)
            restored += 1
        return restored


def _similarity(left: str, right: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, normalize(left), normalize(right)).ratio()
