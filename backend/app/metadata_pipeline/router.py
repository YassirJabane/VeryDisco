from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.app.auth import get_current_user
from .service import MetadataRebuildService


class ScanRequest(BaseModel):
    verify_acoustid: bool = False


class SelectReleaseRequest(BaseModel):
    release_mbid: str


class ApplyRequest(BaseModel):
    include_artwork: bool = True
    allow_itunes_artwork: bool = True


def create_metadata_rebuild_router(
    db: Any,
    config_manager: Any,
    rescan: Optional[Callable[[], Awaitable[Any]]] = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/metadata-rebuild", tags=["metadata-rebuild"])
    state_root = Path(db.db_path).resolve().parent / "metadata-rebuild"
    service = MetadataRebuildService(state_root)
    tasks: set[asyncio.Task] = set()

    async def context(request: Request) -> tuple[dict[str, Any], Path]:
        user = await get_current_user(request)
        row = await db.get_user_by_id(user["id"])
        if not row or not row.get("music_dir"):
            raise HTTPException(status_code=400, detail="User music directory is not configured.")
        root = Path(row["music_dir"]).resolve()
        configured_root = Path(config_manager.config.paths.music_dir).resolve()
        if not root.is_relative_to(configured_root):
            raise HTTPException(status_code=403, detail="Music directory is outside the configured library.")
        return user, root

    @router.post("/scan")
    async def start_scan(body: ScanRequest, request: Request):
        user, root = await context(request)
        if service.status(user["id"]).get("status") in {"queued", "scanning", "applying"}:
            raise HTTPException(status_code=409, detail="A metadata rebuild operation is already running.")
        aliases = dict(getattr(config_manager.config.artist_aliases, "aliases", {}) or {})
        api_key = getattr(config_manager.config.acoustid, "api_key", "") or ""

        async def run() -> None:
            await service.scan(user["id"], root, aliases, body.verify_acoustid, api_key)

        service.mark_queued(user["id"])
        task = asyncio.create_task(run())
        tasks.add(task)
        def finished(completed: asyncio.Task) -> None:
            tasks.discard(completed)
            # scan() persists a useful failed status; consuming the exception
            # prevents an unhandled-background-task warning in the server log.
            try:
                completed.result()
            except Exception:
                pass
        task.add_done_callback(finished)
        return {"status": "started", "message": "Read-only metadata inventory started."}

    @router.get("/status")
    async def get_status(request: Request):
        user, _ = await context(request)
        return service.status(user["id"])

    @router.get("/albums")
    async def get_albums(request: Request):
        user, _ = await context(request)
        return service.plans(user["id"])

    @router.get("/albums/{plan_id}")
    async def get_album(plan_id: str, request: Request):
        user, _ = await context(request)
        try:
            return service.plan(user["id"], plan_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Metadata plan not found.")

    @router.post("/albums/{plan_id}/release")
    async def select_release(plan_id: str, body: SelectReleaseRequest, request: Request):
        user, _ = await context(request)
        try:
            return await service.select_release(user["id"], plan_id, body.release_mbid)
        except KeyError:
            raise HTTPException(status_code=404, detail="Metadata plan not found.")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/albums/{plan_id}/apply")
    async def apply_album(plan_id: str, body: ApplyRequest, request: Request):
        user, root = await context(request)
        try:
            result = await service.apply(
                user["id"], plan_id, root, body.include_artwork,
                body.allow_itunes_artwork, rescan,
            )
            await db.clear_file_metadata_cache()
            return result
        except KeyError:
            raise HTTPException(status_code=404, detail="Metadata plan not found.")
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

    @router.post("/albums/{plan_id}/rollback")
    async def rollback_album(plan_id: str, request: Request):
        user, root = await context(request)
        try:
            result = await service.rollback(user["id"], plan_id, root, rescan)
            await db.clear_file_metadata_cache()
            return result
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    return router
