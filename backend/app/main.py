import os
import json
import asyncio
import shutil
import urllib.parse
import re
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Response, Request
from pydantic import BaseModel
import httpx
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.config import ConfigManager, AppConfig
from backend.app.database import Database
from backend.app.scheduler import SchedulerManager
from backend.app.sync import run_sync
import backend.app.sync as sync_module
import backend.app.logger as app_logger

# Locate the configuration path
CONFIG_PATH = os.getenv("CONFIG_PATH", "config.yml")

# Global instances
config_manager = ConfigManager(CONFIG_PATH)
db = Database("/data/verydisco.db")
scheduler_manager = SchedulerManager(db)
logger = app_logger.get_logger()

# Background task registry to prevent GC of fire-and-forget tasks
_background_tasks: set = set()

# Active tasks registry
_active_tasks: dict = {}

def _create_tracked_task(coro, task_id: Optional[str] = None, task_type: Optional[str] = None, metadata: Optional[dict] = None) -> asyncio.Task:
    """Create a background task that logs exceptions and prevents GC, and registers it for cancellation."""
    from datetime import datetime
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    
    if task_id:
        _active_tasks[task_id] = {
            "task": task,
            "type": task_type or "unknown",
            "metadata": metadata or {},
            "started_at": datetime.utcnow().isoformat()
        }
        
    def _on_done(t: asyncio.Task):
        _background_tasks.discard(t)
        if task_id:
            _active_tasks.pop(task_id, None)
        if not t.cancelled() and t.exception():
            logger.error(f"Background task failed: {t.exception()}")
            
    task.add_done_callback(_on_done)
    return task

import time
last_navidrome_scan_time = 0
navidrome_scan_lock = asyncio.Lock()

async def trigger_navidrome_scan_debounced():
    global last_navidrome_scan_time
    if not config_manager.config:
        return
    cfg = config_manager.config
    if not (cfg.navidrome.url and cfg.navidrome.username and cfg.navidrome.password):
        return
        
    async with navidrome_scan_lock:
        now = time.time()
        if now - last_navidrome_scan_time < 15:
            logger.info("Navidrome rescan skipped (debounced)")
            return
        last_navidrome_scan_time = now
        try:
            from backend.app.clients.navidrome import NavidromeClient
            nd_client = NavidromeClient(url=cfg.navidrome.url, username=cfg.navidrome.username, password=cfg.navidrome.password)
            await nd_client.trigger_scan()
            logger.info("Successfully triggered library scan on Navidrome.")
        except Exception as e:
            logger.warning(f"Failed to trigger Navidrome scan: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup initial logging
    log_level = "INFO"
    if config_manager.config:
        log_level = config_manager.config.log_level
    app_logger.setup_logging(log_level)

    # Init database
    await db.initialize()
    app_logger.db_ref = db

    # Start scheduler if configuration is valid
    if config_manager.is_configured and config_manager.config:
        scheduler_manager.start(config_manager.config)
        logger.info("Application boot completed with scheduler running.")
        
        # --- AUTO RESUME LOGIC ---
        try:
            # 1. Resume interrupted Sync
            latest_run = await db.get_latest_run()
            if latest_run and latest_run['status'] == 'running':
                logger.warning("Detected interrupted sync run on startup. Marking as failed.")
                async with db.get_db() as conn:
                    await conn.execute("UPDATE runs SET status = 'failed', ended_at = CURRENT_TIMESTAMP WHERE id = ?", (latest_run['id'],))
                    await conn.commit()
            
            # 2. Resume interrupted Album Downloads
            pending_albums = await db.get_pending_album_downloads()
            if pending_albums:
                from backend.app.album_sync import download_album_task
                logger.info(f"Found {len(pending_albums)} pending album downloads. Resuming...")
                for pa in pending_albums:
                    _create_tracked_task(
                        download_album_task(
                            pa['id'], pa['artist'], pa['title'] or "", pa['album'], config_manager.config, db
                        ),
                        task_id=f"album:{pa['id']}",
                        task_type="album",
                        metadata={"download_id": pa['id'], "artist": pa['artist'], "album": pa['album']}
                    )
        except Exception as e:
            logger.error(f"Error during auto-resume on startup: {e}")
        # --- END AUTO RESUME LOGIC ---
        
        # 3. Populate Library Cache in Background
        async def initial_cache_warmup():
            try:
                await asyncio.sleep(30)
                logger.info("Starting background library cache warmup...")
                await run_full_library_audit()
                logger.info("Library cache warmup completed.")
            except Exception as e:
                logger.error(f"Error during library cache warmup: {e}")
        
        asyncio.create_task(initial_cache_warmup())
    else:
        logger.warning("Application booted in unconfigured state. Scheduler is idle.")

    yield

    # Clean shutdown
    scheduler_manager.shutdown()

app = FastAPI(title="VeryDisco API", lifespan=lifespan)

# Allow CORS for dev & reverse proxy environments with credentials
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

# API Endpoints

# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/auth/login")
async def login(req: LoginRequest, response: Response):
    """Validate Navidrome credentials and set a secure httpOnly session cookie."""
    from backend.app.auth import validate_navidrome_login, create_access_token, set_auth_cookie
    from datetime import timedelta

    cfg = config_manager.config
    if not cfg:
        raise HTTPException(status_code=503, detail="App not configured.")

    nd_url = cfg.navidrome.url
    user_info = await validate_navidrome_login(nd_url, req.username, req.password)

    # Derive per-user music directory: /music/<username>
    music_base = cfg.paths.music_dir.rstrip("/")
    music_dir = f"{music_base}/{req.username}"

    # Upsert user in DB
    await db.get_or_create_user(
        user_id=user_info["id"],
        username=user_info["username"],
        display_name=user_info.get("name", req.username),
        is_admin=user_info["is_admin"],
        music_dir=music_dir,
    )

    import hashlib
    import secrets
    # Generate Subsonic token and salt for background API calls
    salt = secrets.token_hex(8)
    token_str = req.password + salt
    subsonic_token = hashlib.md5(token_str.encode('utf-8')).hexdigest()

    async with db.get_db() as conn:
        await conn.execute(
            "UPDATE users SET subsonic_token = ?, subsonic_salt = ? WHERE id = ?",
            (subsonic_token, salt, user_info["id"])
        )
        await conn.commit()

    token = create_access_token(
        user_id=user_info["id"],
        username=user_info["username"],
        is_admin=user_info["is_admin"],
        secret_key=cfg.auth.secret_key,
        expires_delta=timedelta(days=cfg.auth.session_days),
    )
    set_auth_cookie(response, token, cfg.auth.session_days, secure=cfg.auth.cookie_secure)
    logger.info(f"User '{req.username}' logged in successfully (admin={user_info['is_admin']}).")
    return {
        "status": "ok",
        "user": {
            "id": user_info["id"],
            "username": user_info["username"],
            "displayName": user_info.get("name", req.username),
            "isAdmin": user_info["is_admin"],
            "musicDir": music_dir,
        }
    }

@app.get("/api/auth/me")
async def auth_me(request: Request):
    """Return current user info from the session cookie."""
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_row = await db.get_user_by_id(user["id"])
    if not user_row:
        raise HTTPException(status_code=404, detail="User not found.")
    return {
        "id": user_row["id"],
        "username": user_row["username"],
        "displayName": user_row["display_name"],
        "isAdmin": bool(user_row["is_admin"]),
        "musicDir": user_row["music_dir"],
    }

@app.post("/api/auth/logout")
async def logout(response: Response):
    """Clear the session cookie."""
    from backend.app.auth import clear_auth_cookie
    clear_auth_cookie(response)
    return {"status": "ok", "message": "Logged out."}

@app.get("/api/users")
async def list_users(request: Request):
    """List all VeryDisco users with configs (admin only)."""
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required.")
    users = await db.list_users()
    users_with_config = []
    for u in users:
        u_dict = dict(u)
        cfg = await db.get_user_config(u["id"])
        if cfg:
            u_dict["enabled_features"] = cfg.get("enabled_features", {})
        else:
            u_dict["enabled_features"] = {
                "starred_sync": True,
                "listenbrainz_sync": True,
                "discovery": True,
                "album_downloads": True
            }
        users_with_config.append(u_dict)
    return {"users": users_with_config}

@app.post("/api/users/import")
async def import_navidrome_users(request: Request):
    """Import all users from Navidrome into VeryDisco DB (admin only)."""
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required.")

    cfg = config_manager.config
    if not cfg:
        raise HTTPException(status_code=503, detail="App not configured.")

    # Use Navidrome Subsonic admin API to list users
    params = {
        "u": cfg.navidrome.username,
        "p": cfg.navidrome.password,
        "v": "1.16.1",
        "c": "VeryDisco",
        "f": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                cfg.navidrome.url.rstrip("/") + "/rest/getUsers.view",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
        subsonic = data.get("subsonic-response", {})
        if subsonic.get("status") != "ok":
            nd_error = subsonic.get("error", {})
            err_msg = nd_error.get("message", "Unknown error from Navidrome")
            logger.error(f"Navidrome getUsers.view returned error: {nd_error}")
            raise HTTPException(status_code=502, detail=f"Navidrome error: {err_msg}")
        users_data = subsonic.get("users", {}).get("user", [])
        if isinstance(users_data, dict):
            users_data = [users_data]  # single user comes as dict

        music_base = cfg.paths.music_dir.rstrip("/")
        playlists_base = cfg.paths.navidrome_playlists_dir.rstrip("/")
        imported = []
        for u in users_data:
            uname = u.get("username", "")
            if not uname:
                continue
            # username is the stable ID used by login (Subsonic API has no UUID)
            await db.get_or_create_user(
                user_id=uname,
                username=uname,
                display_name=uname,
                is_admin=bool(u.get("adminRole", False)),
                music_dir=f"{music_base}/{uname}",
            )
            # Also set playlist_dir
            await db.update_user_paths(
                user_id=uname,
                music_dir=f"{music_base}/{uname}",
                playlist_dir=f"{playlists_base}/{uname}",
            )
            imported.append(uname)

        logger.info(f"Imported {len(imported)} Navidrome users: {imported}")
        return {"status": "ok", "imported": imported, "count": len(imported)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to import Navidrome users: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to import users: {e}")

@app.get("/api/users/me/config")
async def get_my_config(request: Request):
    """Get current user's ListenBrainz configuration."""
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    cfg = await db.get_user_config(user["id"])
    if not cfg:
        return {
            "lb_username": "",
            "lb_token": "",
            "active_playlists": [],
            "music_dir": "",
            "playlist_dir": "",
            "renaming_pattern": "",
            "enabled_features": {
                "starred_sync": True,
                "listenbrainz_sync": True,
                "discovery": True,
                "album_downloads": True
            }
        }
    return {
        "lb_username": cfg.get("lb_username", ""),
        "lb_token": cfg.get("lb_token", ""),
        "active_playlists": cfg.get("active_playlists", []),
        "music_dir": cfg.get("music_dir", ""),
        "playlist_dir": cfg.get("playlist_dir", ""),
        "renaming_pattern": cfg.get("renaming_pattern", ""),
        "enabled_features": cfg.get("enabled_features", {}),
    }

class UserConfigRequest(BaseModel):
    lb_username: str = ""
    lb_token: str = ""
    active_playlists: List[str] = []
    music_dir: str = ""
    playlist_dir: str = ""
    renaming_pattern: str = ""
    enabled_features: Dict[str, bool] = {}

@app.put("/api/users/me/config")
async def save_my_config(req: UserConfigRequest, request: Request):
    """Save current user's ListenBrainz and library configurations."""
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    
    # 1. Save ListenBrainz config
    await db.save_user_config(
        user_id=user["id"],
        lb_username=req.lb_username,
        lb_token=req.lb_token,
        active_playlists=req.active_playlists,
    )
    
    # 2. Update library paths and renaming pattern in users table
    await db.update_user_paths(
        user_id=user["id"],
        music_dir=req.music_dir,
        playlist_dir=req.playlist_dir,
        renaming_pattern=req.renaming_pattern,
    )

    # 3. Save enabled features if provided
    if req.enabled_features:
        await db.save_user_features(
            user_id=user["id"],
            enabled_features=req.enabled_features
        )
            
    return {"status": "ok", "message": "Your settings saved successfully."}

class AdminUserPathsRequest(BaseModel):
    music_dir: str = ""
    playlist_dir: str = ""

@app.put("/api/admin/users/{user_id}/paths")
async def admin_update_user_paths(user_id: str, req: AdminUserPathsRequest, request: Request):
    """Admin: update any user's music and playlist directories."""
    from backend.app.auth import get_current_user
    admin = await get_current_user(request)
    if not admin["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required.")
    target = await db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    await db.update_user_paths(
        user_id=user_id,
        music_dir=req.music_dir,
        playlist_dir=req.playlist_dir,
    )
    return {"status": "ok", "message": f"Paths updated for user '{target['username']}'."}

class AdminUserFeaturesRequest(BaseModel):
    enabled_features: Dict[str, bool]

@app.put("/api/admin/users/{user_id}/features")
async def admin_update_user_features(user_id: str, req: AdminUserFeaturesRequest, request: Request):
    """Admin: update any user's enabled features."""
    from backend.app.auth import get_current_user
    admin = await get_current_user(request)
    if not admin["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required.")
    target = await db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    await db.save_user_features(user_id, req.enabled_features)
    return {"status": "ok", "message": f"Features updated for user '{target['username']}'."}

class OnboardingSetupRequest(BaseModel):
    navidrome_url: str
    navidrome_username: str
    navidrome_password: str
    slskd_url: str
    slskd_api_key: str = ""

@app.post("/api/setup")
async def setup_app(req: OnboardingSetupRequest, response: Response):
    """Bootstrap application settings on first install."""
    # Check if already configured with a Navidrome URL
    if config_manager.is_configured:
        raise HTTPException(status_code=400, detail="Application is already configured.")

    from backend.app.auth import validate_navidrome_login, create_access_token, set_auth_cookie
    from datetime import timedelta
    import yaml

    # 1. Validate credentials against the supplied Navidrome URL
    user_info = await validate_navidrome_login(
        req.navidrome_url, req.navidrome_username, req.navidrome_password
    )

    if not user_info.get("is_admin"):
        raise HTTPException(
            status_code=400,
            detail="The supplied Navidrome credentials must belong to an Admin user."
        )

    # 2. Update config.yml contents
    try:
        data = yaml.safe_load(config_manager.raw_yaml) or {}
    except Exception:
        data = {}

    if "navidrome" not in data:
        data["navidrome"] = {}
    data["navidrome"]["url"] = req.navidrome_url
    data["navidrome"]["username"] = req.navidrome_username
    data["navidrome"]["password"] = req.navidrome_password

    if "slskd" not in data:
        data["slskd"] = {}
    data["slskd"]["base_url"] = req.slskd_url
    data["slskd"]["api_key"] = req.slskd_api_key

    # Ensure a valid listenbrainz block exists so config validator passes
    if "listenbrainz" not in data:
        data["listenbrainz"] = {}
    if data["listenbrainz"].get("username") in ("your-username", "", None):
        data["listenbrainz"]["username"] = "shared-onboarding"

    # Save to disk
    is_valid, err = config_manager.save(data)
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail=f"Configuration error during bootstrap: {err}"
        )

    # Hot-reload system parameters
    cfg = config_manager.config
    if not cfg:
        raise HTTPException(status_code=500, detail="Configuration was saved but failed to load. Check logs.")
    app_logger.setup_logging(cfg.log_level)
    scheduler_manager.update_schedule(cfg)
    if not scheduler_manager.scheduler.running:
        scheduler_manager.start(cfg)

    # 3. Create the admin user in database
    music_base = cfg.paths.music_dir.rstrip("/")
    music_dir = f"{music_base}/{req.navidrome_username}"

    await db.get_or_create_user(
        user_id=user_info["id"],
        username=user_info["username"],
        display_name=user_info.get("name", req.navidrome_username),
        is_admin=user_info["is_admin"],
        music_dir=music_dir,
    )

    # 4. Generate access token and authenticate user immediately
    token = create_access_token(
        user_id=user_info["id"],
        username=user_info["username"],
        is_admin=user_info["is_admin"],
        secret_key=cfg.auth.secret_key,
        expires_delta=timedelta(days=cfg.auth.session_days),
    )
    set_auth_cookie(response, token, cfg.auth.session_days, secure=cfg.auth.cookie_secure)

    logger.info(f"Application bootstrapped successfully by admin '{req.navidrome_username}'.")

    return {
        "status": "ok",
        "message": "Configuration bootstrapped and admin authenticated.",
        "user": {
            "id": user_info["id"],
            "username": user_info["username"],
            "displayName": user_info.get("name", req.navidrome_username),
            "isAdmin": user_info["is_admin"],
            "musicDir": music_dir,
        }
    }

@app.get("/api/config")
async def get_config(request: Request):
    """Retrieve current config content, configuration state, and any validation errors."""
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return {
        "raw_yaml": config_manager.raw_yaml,
        "is_configured": config_manager.is_configured,
        "validation_errors": config_manager.validation_errors,
        "parsed": config_manager.config.model_dump() if config_manager.config else None
    }

@app.put("/api/config")
async def update_config(payload: Dict[str, Any], request: Request):
    """Update config.yml file, validate and apply changes instantly without restarts."""
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required.")
    is_valid, err = config_manager.save(payload)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Configuration error: {err}")

    # Hot-reload system parameters
    config = config_manager.config
    if config:
        # 1. Reload logger level
        app_logger.setup_logging(config.log_level)
        # 2. Reload scheduler
        scheduler_manager.update_schedule(config)
        # If it was not started because of invalid config, start it
        if not scheduler_manager.scheduler.running:
            scheduler_manager.start(config)

        logger.info("Configuration hot-reloaded successfully.")
    
    return {"status": "success", "message": "Config updated and applied"}

@app.get("/api/status")
async def get_status(request: Request = None):
    """Returns dashboard statuses, next run schedule, and the latest execution records for current user."""
    user_id = None
    active_playlists = []
    if request:
        try:
            from backend.app.auth import get_current_user
            user = await get_current_user(request)
            user_id = user["id"]
        except Exception:
            pass

    if user_id:
        user_cfg = await db.get_user_config(user_id)
        if user_cfg and user_cfg.get("active_playlists"):
            active_playlists = user_cfg["active_playlists"]
            if isinstance(active_playlists, str):
                try:
                    active_playlists = json.loads(active_playlists)
                except Exception:
                    active_playlists = []

    if not active_playlists and config_manager.is_configured:
        active_playlists = config_manager.config.listenbrainz.active_playlists

    latest_run = await db.get_latest_run(user_id=user_id)
    latest_runs = {}
    for p in active_playlists:
        latest_runs[p] = await db.get_latest_run(p, user_id=user_id)
    next_run = scheduler_manager.get_next_run_time() if config_manager.is_configured else None

    # Merge sync module variables: only show if the current sync belongs to this user
    is_syncing = False
    progress = {
        "status": "idle",
        "tracks_found": 0,
        "tracks_downloaded": 0,
        "tracks_skipped": 0,
        "tracks_failed": 0,
        "started_at": None,
        "current_source": None
    }
    if sync_module.is_syncing:
        sync_user_id = sync_module.sync_progress.get("user_id")
        if sync_user_id == user_id:
            is_syncing = True
            progress = {
                "status": "running",
                "tracks_found": sync_module.sync_progress["tracks_found"],
                "tracks_downloaded": sync_module.sync_progress["tracks_downloaded"],
                "tracks_skipped": sync_module.sync_progress["tracks_skipped"],
                "tracks_failed": sync_module.sync_progress["tracks_failed"],
                "started_at": sync_module.sync_progress["started_at"],
                "current_source": sync_module.sync_progress.get("playlist_source")
            }

    return {
        "is_configured": config_manager.is_configured,
        "validation_errors": config_manager.validation_errors,
        "is_syncing": is_syncing,
        "next_run": next_run,
        "progress": progress,
        "latest_run": latest_run,
        "latest_runs": latest_runs,
        "active_playlists": active_playlists
    }

@app.get("/api/runs")
async def get_runs(request: Request, limit: int = 20, offset: int = 0):
    """Retrieve execution log history for current user."""
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    runs, total = await db.get_runs(limit, offset, user_id=user_id)
    return {"runs": runs, "total": total}

@app.get("/api/runs/{run_id}/tracks")
async def get_run_tracks(run_id: int, request: Request):
    """Retrieve detailed track status items for a specific execution run."""
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    async with db.get_db() as conn:
        async with conn.execute("SELECT user_id FROM runs WHERE id = ?", (run_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Run not found.")
            run_user_id = row[0]
            if run_user_id != user["id"]:
                raise HTTPException(status_code=403, detail="Forbidden.")
    tracks = await db.get_tracks_for_run(run_id)
    return {"tracks": tracks}

@app.post("/api/trigger")
async def trigger_sync(source: Optional[str] = None, request: Request = None):
    """Manual sync execution request."""
    if not config_manager.is_configured or not config_manager.config:
        raise HTTPException(status_code=400, detail="Cannot run sync: application is not configured yet.")

    if sync_module.is_syncing:
        raise HTTPException(status_code=400, detail="Sync process already in progress")

    if not source:
        raise HTTPException(status_code=400, detail="source parameter is required")

    user_id = None
    if request:
        try:
            from backend.app.auth import get_current_user
            user = await get_current_user(request)
            user_id = user["id"]
        except Exception:
            pass

    logger.info(f"Manual synchronization triggered via WebUI for source '{source}' (user: {user_id}).")
    _create_tracked_task(
        run_sync(db, config_manager.config, source, user_id=user_id),
        task_id=f"sync:{source}",
        task_type="sync",
        metadata={"source": source, "user_id": user_id}
    )
    return {"status": "success", "message": "Synchronization triggered successfully"}

@app.post("/api/sync/stop")
async def stop_sync():
    """Stop an ongoing sync execution."""
    if not sync_module.is_syncing or getattr(sync_module, 'current_sync_task', None) is None:
        raise HTTPException(status_code=400, detail="No sync process is currently running.")
    
    logger.info("Sync cancellation requested via WebUI.")
    sync_module.current_sync_task.cancel()
    return {"status": "success", "message": "Synchronization stop requested"}

@app.get("/api/tasks")
async def get_active_tasks():
    """Retrieve all currently running tasks (sync, album downloads, track downloads)."""
    tasks_list = []
    for tid, info in _active_tasks.items():
        tasks_list.append({
            "id": tid,
            "type": info["type"],
            "metadata": info["metadata"],
            "started_at": info["started_at"]
        })
    return {"tasks": tasks_list}

@app.post("/api/tasks/{task_id}/stop")
async def stop_active_task(task_id: str):
    """Stop/cancel a running task by its task_id."""
    if task_id not in _active_tasks:
        raise HTTPException(status_code=404, detail="Task not found or already finished.")
        
    task_info = _active_tasks[task_id]
    task = task_info["task"]
    task.cancel()
    logger.info(f"Task {task_id} cancellation requested via WebUI.")
    return {"status": "success", "message": f"Task {task_id} cancellation requested."}

@app.get("/api/downloads/albums")
async def get_album_downloads(request: Request):
    """Retrieve all album download queue items (pending, completed, failed)."""
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    
    async with db.get_db() as conn:
        # Check if the table has user_id (it was altered dynamically, let's query row keys safely)
        cursor = await conn.execute("PRAGMA table_info(album_downloads)")
        columns = [col[1] for col in await cursor.fetchall()]
        has_user_id = "user_id" in columns

        if user.get("is_admin"):
            # Admins see all downloads
            cursor = await conn.execute("SELECT * FROM album_downloads ORDER BY added_at DESC")
        else:
            # Users only see their own downloads if user_id column exists
            if has_user_id:
                cursor = await conn.execute("SELECT * FROM album_downloads WHERE user_id = ? ORDER BY added_at DESC", (user["id"],))
            else:
                cursor = await conn.execute("SELECT * FROM album_downloads ORDER BY added_at DESC")
        
        rows = await cursor.fetchall()
        downloads = []
        for r in rows:
            downloads.append({
                "id": r["id"],
                "artist": r["artist"],
                "title": r["title"] or "",
                "album": r["album"],
                "status": r["status"],
                "added_at": r["added_at"],
                "user_id": r["user_id"] if has_user_id else None
            })
        return {"downloads": downloads}

@app.delete("/api/downloads/albums/{download_id}")
async def delete_album_download(download_id: int):
    """Delete an album download entry from the database queue and cancel it if running."""
    task_key = f"album:{download_id}"
    if task_key in _active_tasks:
        _active_tasks[task_key]["task"].cancel()
        
    async with db.get_db() as conn:
        await conn.execute("DELETE FROM album_downloads WHERE id = ?", (download_id,))
        await conn.commit()
    logger.info(f"Deleted album download record {download_id} from database queue.")
    return {"status": "success", "message": f"Album download {download_id} deleted."}

async def scan_acoustid_batch_task(batch_size: int = 50, user_id: Optional[str] = None):
    """
    Scans a batch of up to `batch_size` files in the library using AcoustID.
    Respects rate limits (2 queries/sec).
    """
    logger.info(f"Starting AcoustID library verification batch scan (size: {batch_size})")
    cfg = config_manager.config
    if not cfg or not cfg.acoustid.api_key:
        logger.error("Cannot run AcoustID scan: API key is not configured.")
        return

    # Get all audio files in the music directory
    music_dir = Path(cfg.paths.music_dir)
    if user_id:
        user_row = await db.get_user_by_id(user_id)
        if user_row and user_row.get("music_dir"):
            music_dir = Path(user_row["music_dir"])

    # Fetch already scanned files from the database
    scanned_results = await db.get_acoustid_results()
    scanned_paths = {r["file_path"] for r in scanned_results}

    # Find unscanned files
    unscanned_files = []
    for root, dirs, files in os.walk(str(music_dir)):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in ["playlists", "navidrome_playlists", "explore"]]
        if ".staging" in root:
            continue
        for f in files:
            if f.lower().endswith((".mp3", ".flac", ".m4a")):
                f_path = str(Path(root) / f)
                if f_path not in scanned_paths:
                    unscanned_files.append(Path(root) / f)
                    # batch_size=0 means scan ALL remaining
                    if batch_size > 0 and len(unscanned_files) >= batch_size:
                        break
        if batch_size > 0 and len(unscanned_files) >= batch_size:
            break

    if not unscanned_files:
        logger.info("No unscanned files left in the library.")
        return

    logger.info(f"Selected {len(unscanned_files)} files for AcoustID verification. Processing...")

    from backend.app.clients.acoustid import acoustid_client
    
    for idx, file_path in enumerate(unscanned_files, 1):
        try:
            is_valid, reason = await acoustid_client.verify_track_against_metadata(file_path)
            status = "verified" if is_valid else "failed"
            await db.save_acoustid_result(str(file_path), status, reason)
            
            if is_valid:
                logger.info(f"[{idx}/{len(unscanned_files)}] Verified: {file_path.name}")
            else:
                logger.warning(f"[{idx}/{len(unscanned_files)}] Failure: {file_path.name} -> {reason}")
        except Exception as e:
            logger.error(f"Error scanning {file_path}: {e}")
            await db.save_acoustid_result(str(file_path), "failed", f"Unexpected error during scan: {e}")

        # Sleep for 0.5 seconds to respect the 2 queries/sec rate limit
        await asyncio.sleep(0.5)

    # Invalidate maintenance cache so failures appear immediately in Server Health
    if user_id:
        await db.delete_cache(f"maintenance_{user_id}")
    await db.delete_cache("maintenance")
    logger.info("AcoustID verification batch scan completed.")

@app.post("/api/acoustid/scan")
async def trigger_acoustid_scan(batch_size: Optional[int] = 50, request: Request = None):
    """Trigger a batch scan of AcoustID verification."""
    if not config_manager.is_configured or not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured yet.")
    if not config_manager.config.acoustid.api_key or not config_manager.config.acoustid.api_key.strip():
        raise HTTPException(status_code=400, detail="AcoustID API key is not configured in settings.")
        
    if "acoustid_scan" in _active_tasks:
        raise HTTPException(status_code=400, detail="AcoustID scan is already running.")

    user_id = None
    if request:
        try:
            from backend.app.auth import get_current_user
            user = await get_current_user(request)
            user_id = user["id"]
        except Exception:
            pass

    _create_tracked_task(
        scan_acoustid_batch_task(batch_size=batch_size, user_id=user_id),
        task_id="acoustid_scan",
        task_type="acoustid_scan",
        metadata={"batch_size": batch_size}
    )
    return {"status": "success", "message": f"AcoustID scan triggered for a batch of {batch_size} files."}

@app.get("/api/acoustid/stats")
async def get_acoustid_stats(request: Request = None):
    """Get the current progress of the library AcoustID verification."""
    cfg = config_manager.config
    if not cfg:
        return {"total": 0, "scanned": 0, "verified": 0, "failed": 0, "running": False}

    music_dir = Path(cfg.paths.music_dir)
    user_id = None
    if request:
        try:
            from backend.app.auth import get_current_user
            user = await get_current_user(request)
            user_id = user["id"]
            user_row = await db.get_user_by_id(user_id)
            if user_row and user_row.get("music_dir"):
                music_dir = Path(user_row["music_dir"])
        except Exception:
            pass

    def _count_acoustid_files_sync() -> int:
        count = 0
        if music_dir.exists():
            for root, dirs, files in os.walk(str(music_dir)):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in ["playlists", "navidrome_playlists", "explore"]]
                if ".staging" in root:
                    continue
                for f in files:
                    if f.lower().endswith((".mp3", ".flac", ".m4a")):
                        count += 1
        return count

    cache_path = get_file_checks_cache_path()
    total_files = 0
    cache_data = {}
    try:
        if cache_path.exists():
            import json
            with open(cache_path, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
                if "acoustid_total_files" in cache_data:
                    total_files = cache_data["acoustid_total_files"]
    except Exception:
        pass

    if total_files == 0:
        total_files = await asyncio.to_thread(_count_acoustid_files_sync)
        cache_data["acoustid_total_files"] = total_files
        try:
            import json
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f)
        except Exception:
            pass

    results = await db.get_acoustid_results()
    
    # Filter results that actually exist in the current user's library directory
    scanned_count = 0
    verified_count = 0
    failed_count = 0
    
    for r in results:
        r_path = Path(r["file_path"])
        try:
            if music_dir in r_path.parents or r_path.parent == music_dir:
                scanned_count += 1
                if r["status"] == "verified":
                    verified_count += 1
                else:
                    failed_count += 1
        except Exception:
            pass

    is_running = "acoustid_scan" in _active_tasks

    return {
        "total": total_files,
        "scanned": scanned_count,
        "verified": verified_count,
        "failed": failed_count,
        "remaining": max(0, total_files - scanned_count),
        "running": is_running
    }

async def fetch_itunes_metadata(artist: str, title: str) -> dict:
    url = "https://itunes.apple.com/search"
    params = {"term": f"{artist} {title}", "entity": "song", "limit": 1}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("resultCount", 0) > 0:
                    res = data["results"][0]
                    # Get highest res artwork
                    artwork = res.get("artworkUrl100", "").replace("100x100bb", "300x300bb")
                    return {
                        "album": res.get("collectionName", ""),
                        "artwork": artwork
                    }
    except Exception as e:
        logger.warning(f"Failed to fetch iTunes metadata for {artist} - {title}: {e}")
    return {"album": "", "artwork": ""}

async def resolve_user_lb_credentials(request: Request) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """
    Resolves the ListenBrainz username and token for the currently logged-in user.
    Admin users can fall back to the global config values if they haven't set personal ones.
    Non-admin users must configure their own and do not fall back.
    
    Returns (lb_username, lb_token, user_dict) or raises HTTPException.
    """
    from backend.app.auth import get_current_user
    try:
        user = await get_current_user(request)
    except Exception:
        # Anonymous or unauthenticated request (if any) -> fall back to global config
        cfg = config_manager.config
        return cfg.listenbrainz.username, cfg.listenbrainz.token, None

    user_id = user["id"]
    cfg = config_manager.config
    
    user_cfg = await db.get_user_config(user_id)
    lb_username = ""
    lb_token = ""
    if user_cfg:
        lb_username = user_cfg.get("lb_username") or ""
        lb_token = user_cfg.get("lb_token") or ""
        
    # If the user is an admin, they can fall back to global credentials
    if user.get("is_admin"):
        if not lb_username:
            lb_username = cfg.listenbrainz.username
        if not lb_token:
            lb_token = cfg.listenbrainz.token
            
    return lb_username, lb_token, user


import time
_cached_playlists = {} # schema: {source: {"tracks": [...], "time": timestamp}}

@app.get("/api/playlist/current")
async def get_current_playlist(source: Optional[str] = None, request: Request = None):
    """Fetch current playlist from ListenBrainz with iTunes artwork and merge DB status. Uses 12h cache."""
    global _cached_playlists
    
    if not config_manager.is_configured or not config_manager.config:
        return {"tracks": []}
    
    cfg = config_manager.config
    lb_username, lb_token, user = await resolve_user_lb_credentials(request)
    if not lb_username:
        raise HTTPException(
            status_code=400,
            detail="ListenBrainz username is not configured. Please configure it in settings."
        )
    user_id = user["id"] if user else None

    if not source:
        active_playlists = []
        if user_id:
            user_cfg = await db.get_user_config(user_id)
            if user_cfg and user_cfg.get("active_playlists"):
                active_playlists = user_cfg["active_playlists"]
                if isinstance(active_playlists, str):
                    try:
                        active_playlists = json.loads(active_playlists)
                    except Exception:
                        active_playlists = []
        if not active_playlists and cfg.listenbrainz.active_playlists:
            active_playlists = cfg.listenbrainz.active_playlists
        source = active_playlists[0] if active_playlists else "weekly-exploration"
    
    # Fetch DB status
    db_tracks_map = {}
    run_to_use = None
    if sync_module.current_run_id:
        run_to_use = sync_module.current_run_id
    else:
        latest_run = await db.get_latest_run(source)
        if latest_run:
            run_to_use = latest_run["id"]

    if run_to_use:
        db_tracks = await db.get_tracks_for_run(run_to_use)
        for dbt in db_tracks:
            key = f"{dbt['artist'].lower()}-{dbt['title'].lower()}"
            db_tracks_map[key] = {
                "status": dbt['status'],
                "error_reason": dbt.get('error_reason') or ""
            }

    # Use cache if valid (< 12 hours) (include username in cache key to avoid collisions)
    current_time = time.time()
    cache_key = f"{lb_username}:{source}"
    cached = _cached_playlists.get(cache_key)
    if cached and (current_time - cached["time"]) < 43200:
        tracks_copy = [dict(t) for t in cached["tracks"]]
        for t in tracks_copy:
            key = f"{t['artist'].lower()}-{t['title'].lower()}"
            info = db_tracks_map.get(key, {"status": "pending", "error_reason": ""})
            t['status'] = info["status"]
            t['error_reason'] = info["error_reason"]
        return {"tracks": tracks_copy}

    from backend.app.clients.listenbrainz import ListenBrainzClient
    lb_client = ListenBrainzClient(
        username=lb_username,
        playlist_source=source,
        token=lb_token,
        timeout=cfg.timeouts.http_seconds
    )
    
    try:
        mbid = await lb_client.resolve_playlist_mbid()
        tracks = await lb_client.get_playlist_tracks(mbid)
    except ValueError as ve:
        logger.warning(f"Playlist {source} not found on ListenBrainz for user {lb_username}: {ve}")
        return {"tracks": []}
    except Exception as e:
        logger.error(f"Error fetching playlist {source} for UI: {repr(e)}")
        fallback_tracks = []
        if cached:
            logger.info("Falling back to cached playlist due to fetch error.")
            fallback_tracks = cached["tracks"]
        elif run_to_use:
            db_tracks = await db.get_tracks_for_run(run_to_use)
            if db_tracks:
                logger.info(f"Falling back to DB tracks for latest run of {source} due to fetch error.")
                for dbt in db_tracks:
                    fallback_tracks.append({
                        "artist": dbt.get("artist", ""),
                        "title": dbt.get("title", ""),
                        "album": dbt.get("album", ""),
                        "artwork": dbt.get("artwork", ""),
                        "status": dbt.get("status", "pending"),
                        "error_reason": dbt.get("error_reason") or ""
                    })

        # Cache fallback for 60 seconds to prevent UI polling spam when ListenBrainz has outages
        if fallback_tracks:
            _cached_playlists[cache_key] = {
                "time": current_time - 43200 + 60,
                "tracks": fallback_tracks
            }
            tracks_copy = [dict(t) for t in fallback_tracks]
            for t in tracks_copy:
                key = f"{t['artist'].lower()}-{t['title'].lower()}"
                info = db_tracks_map.get(key, {"status": "pending", "error_reason": ""})
                t['status'] = info["status"]
                t['error_reason'] = info["error_reason"]
            return {"tracks": tracks_copy}
            
        raise HTTPException(status_code=503, detail="ListenBrainz API is temporarily unavailable.")

    # Enrich metadata in parallel
    async def enrich_track(t):
        meta = await fetch_itunes_metadata(t['artist'], t['title'])
        t['album'] = meta['album']
        t['artwork'] = meta['artwork']
        key = f"{t['artist'].lower()}-{t['title'].lower()}"
        info = db_tracks_map.get(key, {"status": "pending", "error_reason": ""})
        t['status'] = info["status"]
        t['error_reason'] = info["error_reason"]
        return t

    enriched_tracks = await asyncio.gather(*(enrich_track(t) for t in tracks))
    
    _cached_playlists[cache_key] = {
        "tracks": enriched_tracks,
        "time": current_time
    }
    
    return {"tracks": enriched_tracks}

@app.get("/api/playlist/feedback")
async def get_feedback(request: Request, score: Optional[int] = None, count: int = 100, offset: int = 0):
    """Retrieve user feedback (loved/unloved tracks) from ListenBrainz."""
    if not config_manager.is_configured or not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured yet.")
        
    cfg = config_manager.config
    from backend.app.auth import get_current_user
    lb_username, lb_token, user = await resolve_user_lb_credentials(request)
    if not lb_username or not lb_token:
        raise HTTPException(
            status_code=400,
            detail="ListenBrainz integration is not configured. Please configure your ListenBrainz username and token in settings."
        )
    user_id = user["id"] if user else None
    active_playlists = []
    if user_id:
        user_cfg = await db.get_user_config(user_id)
        if user_cfg and user_cfg.get("active_playlists"):
            active_playlists = user_cfg["active_playlists"]
            if isinstance(active_playlists, str):
                try:
                    active_playlists = json.loads(active_playlists)
                except Exception:
                    active_playlists = []
    if not active_playlists and cfg.listenbrainz.active_playlists:
        active_playlists = cfg.listenbrainz.active_playlists
                
    from backend.app.clients.listenbrainz import ListenBrainzClient
    lb_client = ListenBrainzClient(
        username=lb_username,
        playlist_source=active_playlists[0] if active_playlists else "weekly-exploration",
        token=lb_token,
        timeout=cfg.timeouts.http_seconds
    )
    
    try:
        feedback_raw = await lb_client.get_user_feedback(score=score, count=count, offset=offset)
        
        async def enrich_feedback_item(item):
            scr = item.get("score")
            mbid = item.get("recording_mbid")
            
            track_meta = item.get("track_metadata", {})
            artist = track_meta.get("artist_name") or item.get("artist_name") or "Unknown Artist"
            title = track_meta.get("track_name") or item.get("recording_name") or item.get("track_name") or "Unknown Track"
            
            meta = await fetch_itunes_metadata(artist, title)
            return {
                "artist": artist,
                "title": title,
                "mbid": mbid,
                "score": scr,
                "artwork": meta.get("artwork", ""),
                "album": meta.get("album", "")
            }

        feedback_list = await asyncio.gather(*(enrich_feedback_item(item) for item in feedback_raw))
        return {"feedback": feedback_list}
    except Exception as e:
        logger.error(f"Error fetching feedback from ListenBrainz: {repr(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch feedback: {e}")

@app.get("/api/deezer/search")
async def deezer_search(query: str, type: str = "track"):
    """Proxy request to Deezer public search API."""
    import urllib.parse
    if type == "album":
        url = f"https://api.deezer.com/search/album?q={urllib.parse.quote(query)}"
    elif type == "artist":
        url = f"https://api.deezer.com/search/artist?q={urllib.parse.quote(query)}"
    else:
        url = f"https://api.deezer.com/search?q={urllib.parse.quote(query)}"
        
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"Deezer API search failed: {e}")
        raise HTTPException(status_code=502, detail=f"Deezer search failed: {e}")

@app.get("/api/search/check")
async def check_existence(artist: str, title: str, request: Request, album_id: Optional[int] = None):
    """Check if track or album exists in staging, playlist dir, or music library, and check for upgrades."""
    if not config_manager.is_configured or not config_manager.config:
        return {"exists": False, "status": "missing", "upgrade_available": False, "tracks": []}
        
    cfg = config_manager.config
    music_dir = cfg.paths.music_dir
    playlists_dir = cfg.paths.navidrome_playlists_dir
    active_playlists = cfg.listenbrainz.active_playlists or ["weekly-exploration"]
    
    from backend.app.auth import get_current_user
    try:
        user = await get_current_user(request)
        if user:
            user_row = await db.get_user_by_id(user["id"])
            if user_row:
                if user_row.get("music_dir"):
                    music_dir = user_row["music_dir"]
                if user_row.get("playlist_dir"):
                    playlists_dir = user_row["playlist_dir"]
            user_cfg = await db.get_user_config(user["id"])
            if user_cfg and user_cfg.get("active_playlists"):
                active_playlists = user_cfg["active_playlists"]
    except Exception:
        pass
        
    from pathlib import Path
    from backend.app.sync import find_existing_track_file, get_file_audio_info, check_quality_status
    
    playlist_dirs = [os.path.join(playlists_dir, p) for p in active_playlists]
    
    def check_track(t_artist: str, t_title: str) -> dict:
        found_path = None
        for playlist_output_dir in playlist_dirs:
            audio_path, _ = find_existing_track_file(music_dir, playlist_output_dir, "", t_artist, t_title)
            if audio_path:
                found_path = audio_path
                break
        
        if not found_path:
            return {"exists": False, "quality_status": "worse", "existing_quality": None}
            
        ext, bitrate, bit_depth, sample_rate = get_file_audio_info(found_path)
        status = check_quality_status(ext, bitrate, bit_depth, sample_rate, cfg)
        return {
            "exists": True,
            "quality_status": status,
            "existing_quality": ext.upper() if ext else None
        }

    if album_id:
        from backend.app.clients.deezer import DeezerClient
        dz = DeezerClient(timeout=10)
        tracks_data = await dz.get_album_tracks(album_id)
        if not tracks_data or "data" not in tracks_data:
            return {"exists": False, "status": "missing", "upgrade_available": False, "tracks": []}
            
        tracks = tracks_data["data"]
        checked_tracks = []
        exists_count = 0
        upgrade_available = False
        
        for t in tracks:
            t_title = t.get("title", "")
            t_artist = t.get("artist", {}).get("name", artist)
            
            res = check_track(t_artist, t_title)
            res["title"] = t_title
            checked_tracks.append(res)
            
            if res["exists"]:
                exists_count += 1
                if res["quality_status"] == "worse":
                    upgrade_available = True
                    
        status = "missing"
        if exists_count == len(tracks):
            status = "full"
        elif exists_count > 0:
            status = "partial"
            
        return {
            "exists": exists_count > 0,
            "status": status,
            "upgrade_available": upgrade_available,
            "tracks": checked_tracks
        }
    else:
        res = check_track(artist, title)
        return {
            "exists": res["exists"],
            "quality_status": res["quality_status"],
            "existing_quality": res["existing_quality"]
        }

class DownloadTrackRequest(BaseModel):
    artist: str
    title: str
    album: str
    force: Optional[bool] = False

@app.post("/api/download/track")
async def download_single_track(req: DownloadTrackRequest, request: Request = None):
    """Trigger background single track search and download."""
    if not config_manager.is_configured or not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured yet.")
        
    cfg = config_manager.config
    from backend.app.album_sync import download_single_track_task
    
    user_id = None
    if request:
        try:
            from backend.app.auth import get_current_user
            user = await get_current_user(request)
            user_id = user["id"]
        except Exception:
            pass

    _create_tracked_task(
        download_single_track_task(
            artist=req.artist,
            title=req.title,
            album=req.album,
            config=cfg,
            db=db,
            force=req.force,
            user_id=user_id
        ),
        task_id=f"track:{req.artist}:{req.title}",
        task_type="track",
        metadata={"artist": req.artist, "title": req.title, "album": req.album}
    )
    return {"status": "success", "message": f"Single track search/download queued for '{req.title}'."}

class DownloadAlbumRequest(BaseModel):
    artist: str
    album: str
    force: Optional[bool] = False

@app.post("/api/download/album")
async def download_single_album(req: DownloadAlbumRequest, request: Request = None):
    """Trigger background album search and download."""
    if not config_manager.is_configured or not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured yet.")
        
    cfg = config_manager.config
    
    user_id = None
    if request:
        try:
            from backend.app.auth import get_current_user
            user = await get_current_user(request)
            user_id = user["id"]
        except Exception:
            pass

    from backend.app.album_sync import download_album_task
    download_id = await db.add_album_download(req.artist, "", req.album, user_id=user_id)
    
    _create_tracked_task(
        download_album_task(
            download_id=download_id,
            artist=req.artist,
            track_title="",
            album=req.album,
            config=cfg,
            db=db,
            force=req.force,
            user_id=user_id
        ),
        task_id=f"album:{download_id}",
        task_type="album",
        metadata={"download_id": download_id, "artist": req.artist, "album": req.album}
    )
    return {"status": "success", "message": f"Album download queued for '{req.album}'."}

@app.get("/api/download/album/search")
async def search_album_candidates(artist: str, album: str, request: Request):
    """Search Slskd for album candidates and group them by directory."""
    if not config_manager.is_configured or not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured yet.")
        
    cfg = config_manager.config
    
    from backend.app.clients.slskd import SlskdClient
    from backend.app.album_sync import clean_artist_name, clean_album_name, check_remix_mismatch, check_album_match, get_quality_priority, check_artist_match
    import re
    
    slskd_client = SlskdClient(
        base_url=cfg.slskd.base_url,
        api_key=cfg.slskd.api_key,
        timeout=cfg.timeouts.http_seconds
    )
    
    clean_art = clean_artist_name(artist)
    clean_alb = clean_album_name(album)
    
    art_lower = clean_art.lower()
    alb_lower = clean_alb.lower()
    
    queries = []
    if art_lower == alb_lower:
        queries.append((clean_art, False))
    elif art_lower in alb_lower:
        queries.append((clean_alb, False))
    elif alb_lower in art_lower:
        queries.append((clean_art, False))
    else:
        queries.append((f"{clean_art} {clean_alb}", False))
        
        stripped_alb = re.sub(r'(?i)\b(single|ep|lp|deluxe|remastered|version)\b', '', album)
        stripped_alb = re.sub(r'\s+-\s+', ' ', stripped_alb)
        stripped_alb = re.sub(r'[^\w\s-]', ' ', stripped_alb)
        stripped_alb = re.sub(r'\s+', ' ', stripped_alb).strip()
        
        if stripped_alb and stripped_alb.lower() != clean_alb.lower():
            queries.append((f"{clean_art} {stripped_alb}", False))
        
        queries.append((clean_alb, True))
        
    # Deduplicate queries
    seen = set()
    deduped_queries = []
    for q_str, req_art in queries:
        if q_str not in seen:
            seen.add(q_str)
            deduped_queries.append((q_str, req_art))
            
    logger.info(f"Manual search queries generated: {deduped_queries}")
    
    # Create searches sequentially to prevent Slskd database concurrency issues
    search_ids = []
    for q_str, _ in deduped_queries:
        sid = await slskd_client.create_search(q_str)
        search_ids.append(sid)
    
    # Filter search IDs and match with queries
    active_searches = []
    for i, search_id in enumerate(search_ids):
        if search_id:
            active_searches.append((search_id, deduped_queries[i][1]))
            
    if not active_searches:
        return []
        
    try:
        # Wait up to 15 seconds for results to populate across all searches
        elapsed = 0
        poll_interval = 2
        while elapsed < 15:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            
            all_complete = True
            for search_id, _ in active_searches:
                status = await slskd_client.get_search_status(search_id)
                if not status or not status[0]:
                    all_complete = False
                    break
            if all_complete:
                break
                
        # Gather results concurrently
        get_responses_tasks = [slskd_client.get_search_responses(search_id) for search_id, _ in active_searches]
        responses_list = await asyncio.gather(*get_responses_tasks)
        
        results = []
        for idx, res_list in enumerate(responses_list):
            if res_list:
                require_art = active_searches[idx][1]
                for res in res_list:
                    res["require_artist_match"] = require_art
                    results.append(res)
    finally:
        # Delete searches sequentially to clean up and avoid Slskd concurrency errors
        for search_id, _ in active_searches:
            try:
                await slskd_client.delete_search(search_id)
            except Exception as e:
                logger.error(f"Failed to delete search {search_id}: {e}")
        
    if not results:
        return []
        
    directories = {}
    for res in results:
        username = res.get("username")
        if not username:
            continue
        require_artist = res.get("require_artist_match", False)
        files = res.get("files", [])
        for f in files:
            filename = f.get("filename")
            if not filename:
                continue
            if not filename.lower().endswith((".mp3", ".flac", ".m4a")):
                continue
                
            bitrate = f.get("bitrate") or f.get("bitRate") or 0
            bit_depth = f.get("bitDepth") or f.get("bit_depth") or 0
            sample_rate = f.get("sampleRate") or f.get("sample_rate") or 0
            
            priority = get_quality_priority(filename, bitrate, bit_depth, sample_rate, cfg)
            if priority < 0:
                priority = 999
                
            parent_dir = "\\".join(filename.split("\\")[:-1]) if "\\" in filename else "/".join(filename.split("/")[:-1])
            if not parent_dir:
                continue
                
            is_album_match = check_album_match(album, parent_dir)
            is_file_title_match = False
            filename_stem = filename.split("\\")[-1].split("/")[-1]
            filename_stem = os.path.splitext(filename_stem)[0]
            norm_title = re.sub(r'[^\w]', '', album).lower()
            norm_file = re.sub(r'[^\w]', '', filename_stem).lower()
            if len(norm_title) >= 3 and norm_title in norm_file:
                is_file_title_match = True

            if check_remix_mismatch(album, parent_dir):
                continue
                
            if not is_album_match and not is_file_title_match:
                continue
                
            if check_remix_mismatch(album, filename):
                continue
                
            if require_artist and not check_artist_match(artist, parent_dir, filename):
                continue
                
            key = (username, parent_dir)
            if key not in directories:
                directories[key] = []
            
            # Deduplicate files in folder
            if not any(item["filename"] == filename for item in directories[key]):
                directories[key].append({
                    "filename": filename,
                    "size": f.get("size", 0),
                    "bitrate": bitrate,
                    "priority": priority
                })
            
    sorted_dirs = sorted(
        directories.items(),
        key=lambda item: (len(item[1]), -item[1][0].get("priority", 999)),
        reverse=True
    )
    
    response_data = []
    for (username, folder), files in sorted_dirs:
        sample = files[0]
        response_data.append({
            "username": username,
            "folder": folder,
            "file_count": len(files),
            "files": files,
            "sample_bitrate": sample.get("bitrate", 0),
            "priority": sample.get("priority", 999)
        })
        
    return response_data

class GrabAlbumRequest(BaseModel):
    artist: str
    album: str
    username: str
    folder: str
    files: List[Dict[str, Any]]
    force: Optional[bool] = False

@app.post("/api/download/album/grab")
async def grab_single_album(req: GrabAlbumRequest, request: Request = None):
    """Trigger background album download from a specific peer/folder candidate."""
    if not config_manager.is_configured or not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured yet.")
        
    cfg = config_manager.config
    
    user_id = None
    if request:
        try:
            from backend.app.auth import get_current_user
            user = await get_current_user(request)
            user_id = user["id"]
        except Exception:
            pass

    from backend.app.album_sync import download_album_task
    download_id = await db.add_album_download(req.artist, "", req.album, user_id=user_id)
    
    _create_tracked_task(
        download_album_task(
            download_id=download_id,
            artist=req.artist,
            track_title="",
            album=req.album,
            config=cfg,
            db=db,
            force=req.force,
            user_id=user_id,
            chosen_username=req.username,
            chosen_folder=req.folder,
            chosen_files=req.files
        ),
        task_id=f"album:{download_id}",
        task_type="album",
        metadata={"download_id": download_id, "artist": req.artist, "album": req.album}
    )
    return {"status": "success", "message": f"Grabbed album from peer '{req.username}'."}


class GrabTrackRequest(BaseModel):
    artist: str
    title: str
    album: str
    username: str
    filename: str
    size: int


@app.get("/api/download/track/search")
async def search_track_candidates(artist: str, title: str, album: Optional[str] = None):
    """Search Slskd for single track candidates."""
    if not config_manager.is_configured or not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured yet.")
        
    cfg = config_manager.config
    
    from backend.app.clients.slskd import SlskdClient
    import re
    
    slskd_client = SlskdClient(
        base_url=cfg.slskd.base_url,
        api_key=cfg.slskd.api_key,
        timeout=cfg.timeouts.http_seconds
    )
    
    clean_title = re.sub(r'[\(\[].*?[\)\]]', '', title)
    clean_artist = re.sub(r'[\(\[].*?[\)\]]', '', artist)
    query = f"{clean_title} - {clean_artist}"
    query = re.sub(r'[^\w\s-]', ' ', query)
    query = re.sub(r'\s+', ' ', query).strip()
    
    logger.info(f"Manual single track search triggered for: '{query}'")
    audio_quality_dict = cfg.slskd.audio_quality.model_dump() if hasattr(cfg.slskd.audio_quality, "model_dump") else dict(cfg.slskd.audio_quality)
    
    candidates, search_id = await slskd_client.search_candidates(
        artist=artist,
        title=title,
        query=query,
        audio_quality=audio_quality_dict,
        album=album,
        search_timeout=cfg.timeouts.search_seconds,
        filter_quality=False
    )
    
    if search_id:
        try:
            await slskd_client.delete_search(search_id)
        except Exception:
            pass
            
    return candidates


@app.post("/api/download/track/grab")
async def grab_single_track(req: GrabTrackRequest, request: Request = None):
    """Trigger background single track download from a specific peer/file candidate."""
    if not config_manager.is_configured or not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured yet.")
        
    cfg = config_manager.config
    
    user_id = None
    if request:
        try:
            from backend.app.auth import get_current_user
            user = await get_current_user(request)
            user_id = user["id"]
        except Exception:
            pass

    from backend.app.album_sync import grab_single_track_task
    
    _create_tracked_task(
        grab_single_track_task(
            artist=req.artist,
            title=req.title,
            album=req.album,
            username=req.username,
            remote_filename=req.filename,
            size=req.size,
            config=cfg,
            db=db,
            user_id=user_id
        ),
        task_id=f"track:grab:{req.artist}:{req.title}",
        task_type="track",
        metadata={"artist": req.artist, "title": req.title, "album": req.album}
    )
    return {"status": "success", "message": f"Grabbed single track '{req.title}' from peer '{req.username}'."}


class MissingTrackItem(BaseModel):
    title: str
    track_number: Optional[int] = None

class DownloadMissingTracksRequest(BaseModel):
    artist: str
    album: str
    missing_tracks: List[MissingTrackItem]

@app.post("/api/download/missing")
async def download_missing_tracks(req: DownloadMissingTracksRequest, request: Request = None):
    """Trigger background search and download for multiple missing tracks of an album."""
    if not config_manager.is_configured or not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured yet.")
        
    cfg = config_manager.config
    from backend.app.album_sync import download_single_track_task
    
    user_id = None
    if request:
        try:
            from backend.app.auth import get_current_user
            user = await get_current_user(request)
            user_id = user["id"]
        except Exception:
            pass

    for track in req.missing_tracks:
        _create_tracked_task(
            download_single_track_task(
                artist=req.artist,
                title=track.title,
                album=req.album,
                config=cfg,
                db=db,
                force=True,
                user_id=user_id
            ),
            task_id=f"track:{req.artist}:{track.title}",
            task_type="track",
            metadata={"artist": req.artist, "title": track.title, "album": req.album}
        )
        
    return {"status": "success", "message": f"Queued {len(req.missing_tracks)} missing tracks for download."}

@app.get("/api/deezer/artist/{artist_id}/albums")
async def deezer_artist_albums(artist_id: int):
    """Fetch albums for a given artist ID from Deezer."""
    url = f"https://api.deezer.com/artist/{artist_id}/albums?limit=100"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"Deezer artist albums lookup failed: {e}")
        raise HTTPException(status_code=502, detail=f"Deezer lookup failed: {e}")

class PinArtistRequest(BaseModel):
    artist_name: str
    deezer_id: Optional[int] = None
    picture_url: Optional[str] = None

@app.get("/api/pinned_artists")
async def get_pinned_artists(request: Request):
    """Fetch all pinned/saved artists, automatically importing new ones from Navidrome or on-disk."""
    try:
        from backend.app.auth import get_current_user
        user = await get_current_user(request)
        user_id = user["id"]
        
        # 1. Database Self-Healing: Merge featuring/joint artists and remove duplicates
        async with db.get_db() as conn:
            cursor = await conn.execute("SELECT id, artist_name FROM pinned_artists WHERE user_id = ?", (user_id,))
            rows = await cursor.fetchall()
            
            # Sort rows by length so canonical (shorter) names are processed first
            sorted_rows = sorted(rows, key=lambda x: len(x["artist_name"]))
            
            seen_primaries = {}
            for row in sorted_rows:
                db_id = row["id"]
                name = row["artist_name"]
                primary = get_primary_artist(name)
                
                if primary.lower() in seen_primaries:
                    # Delete duplicate entry
                    await conn.execute("DELETE FROM pinned_artists WHERE id = ?", (db_id,))
                    logger.info(f"Self-healed duplicate pinned artist: Deleted '{name}' (Merged into '{seen_primaries[primary.lower()]}')")
                else:
                    seen_primaries[primary.lower()] = name
                    if name != primary:
                        # Rename entry to primary name safely
                        await conn.execute("UPDATE OR REPLACE pinned_artists SET artist_name = ? WHERE id = ?", (primary, db_id))
                        logger.info(f"Cleaned pinned artist name in DB: '{name}' -> '{primary}'")
            await conn.commit()
            
        artists = await db.get_pinned_artists(user_id)
        
        cfg = config_manager.config
        # Only auto-import from Navidrome for admin
        if user.get("is_admin") and cfg and cfg.navidrome and cfg.navidrome.url:
            from backend.app.clients.navidrome import NavidromeClient
            client = NavidromeClient(url=cfg.navidrome.url, username=cfg.navidrome.username, password=cfg.navidrome.password)
            nd_artists = await client.get_all_artists()
            
            pinned_names = {a["artist_name"].lower() for a in artists}
            new_artists_set = set()
            for art in nd_artists:
                art = art.strip()
                if not art:
                    continue
                primary = get_primary_artist(art)
                if primary.lower() not in pinned_names:
                    new_artists_set.add(primary)
            new_artists = list(new_artists_set)
            
            if new_artists:
                logger.info(f"Importing {len(new_artists)} new artists from Navidrome: {new_artists}")
                
                async def resolve_and_save(art_name):
                    d_id = 0
                    pic_url = ""
                    try:
                        url = f"https://api.deezer.com/search/artist?q={urllib.parse.quote(art_name)}&limit=1"
                        async with httpx.AsyncClient(timeout=10) as cl:
                            resp = await cl.get(url)
                            if resp.status_code == 200:
                                d = resp.json().get("data", [])
                                if d:
                                    d_id = d[0].get("id", 0)
                                    pic_url = d[0].get("picture_medium", "")
                    except Exception as de:
                        logger.warning(f"Failed to query Deezer metadata for artist {art_name}: {de}")
                    await db.add_pinned_artist(art_name, d_id, pic_url, user_id=user_id)
                
                await asyncio.gather(*(resolve_and_save(a) for a in new_artists))
                artists = await db.get_pinned_artists(user_id)
        
        # Auto-import immediate folders inside the user's music_dir as pinned artists
        if cfg:
            user_row = await db.get_user_by_id(user_id)
            music_dir_path = cfg.paths.music_dir
            if user_row and user_row.get("music_dir"):
                music_dir_path = user_row["music_dir"]
                
            music_dir = Path(music_dir_path)
            if music_dir.exists() and music_dir.is_dir():
                pinned_names = {a["artist_name"].lower() for a in artists}
                local_artists_set = set()
                for item in music_dir.iterdir():
                    if item.is_dir() and item.name.lower() not in ["playlists", "staging", "explore", "current"]:
                        primary = get_primary_artist(item.name)
                        if primary.lower() not in pinned_names:
                            local_artists_set.add(primary)
                local_artists = list(local_artists_set)
                
                if local_artists:
                    logger.info(f"Importing {len(local_artists)} local artists from on-disk directory: {local_artists}")
                    async def resolve_and_save(art_name):
                        d_id = 0
                        pic_url = ""
                        try:
                            url = f"https://api.deezer.com/search/artist?q={urllib.parse.quote(art_name)}&limit=1"
                            async with httpx.AsyncClient(timeout=10) as cl:
                                resp = await cl.get(url)
                                if resp.status_code == 200:
                                    d = resp.json().get("data", [])
                                    if d:
                                        d_id = d[0].get("id", 0)
                                        pic_url = d[0].get("picture_medium", "")
                        except Exception as de:
                            logger.warning(f"Failed to query Deezer metadata for artist {art_name}: {de}")
                        await db.add_pinned_artist(art_name, d_id, pic_url, user_id=user_id)
                    
                    await asyncio.gather(*(resolve_and_save(a) for a in local_artists))
                    artists = await db.get_pinned_artists(user_id)
                
        return artists
    except Exception as e:
        logger.error(f"Failed to fetch pinned artists: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

@app.post("/api/pinned_artists")
async def pin_artist(req: PinArtistRequest, request: Request):
    """Pin a new artist, searching Deezer if metadata isn't provided."""
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]

    artist_name = req.artist_name.strip()
    if not artist_name:
        raise HTTPException(status_code=400, detail="Artist name cannot be empty.")
        
    deezer_id = req.deezer_id
    picture_url = req.picture_url
    
    # Resolve metadata via Deezer if not provided
    if not deezer_id:
        try:
            url = f"https://api.deezer.com/search/artist?q={urllib.parse.quote(artist_name)}&limit=1"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json().get("data", [])
                if not data:
                    raise HTTPException(status_code=404, detail=f"Artist '{artist_name}' not found on Deezer.")
                first = data[0]
                artist_name = first.get("name", artist_name)
                deezer_id = first.get("id")
                picture_url = first.get("picture_medium")
        except httpx.HTTPError as e:
            logger.error(f"Deezer search for artist '{artist_name}' failed: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to verify artist on Deezer: {e}")
            
    try:
        artist_id = await db.add_pinned_artist(artist_name, deezer_id, picture_url, user_id=user_id)
        return {"status": "success", "id": artist_id, "artist_name": artist_name, "deezer_id": deezer_id, "picture_url": picture_url}
    except Exception as e:
        logger.error(f"Failed to pin artist '{artist_name}': {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

@app.delete("/api/pinned_artists/{id}")
async def unpin_artist(id: int, request: Request):
    """Unpin an artist by their database ID, deleting their music folder and triggering Navidrome rescan."""
    try:
        from backend.app.auth import get_current_user
        user = await get_current_user(request)
        user_id = user["id"]

        # Fetch artist name and verify ownership before deleting
        artist_name = None
        async with db.get_db() as conn:
            async with conn.execute("SELECT artist_name, user_id FROM pinned_artists WHERE id = ?", (id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Pinned artist not found.")
                artist_name, artist_user_id = row[0], row[1]
                if artist_user_id != user_id:
                    raise HTTPException(status_code=403, detail="Forbidden.")
        
        if artist_name and config_manager.config:
            from backend.app.sync import sanitize_filename
            user_row = await db.get_user_by_id(user_id)
            music_dir = Path(config_manager.config.paths.music_dir)
            if user_row and user_row.get("music_dir"):
                music_dir = Path(user_row["music_dir"])
            artist_folder = music_dir / sanitize_filename(artist_name)
            
            # Delete artist directory recursively if it exists
            if artist_folder.exists() and artist_folder.is_dir():
                logger.info(f"Deleting artist folder recursively from library: '{artist_folder}'")
                shutil.rmtree(str(artist_folder))
                
        # Delete from database
        await db.delete_pinned_artist(id)
        
        # Trigger Navidrome rescan
        await trigger_navidrome_scan_debounced()
                
        return {"status": "success", "message": "Artist deleted and folder removed successfully."}
    except Exception as e:
        logger.error(f"Failed to delete artist '{id}': {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete artist: {e}")

@app.get("/api/deezer/artist/{artist_id}/releases")
async def get_deezer_artist_releases(artist_id: int):
    """Fetch releases for a given artist ID from Deezer."""
    from backend.app.clients.deezer import DeezerClient
    dz = DeezerClient(timeout=10)
    releases = await dz.get_artist_releases(artist_id)
    if releases is None:
        raise HTTPException(status_code=502, detail="Failed to fetch artist releases from Deezer.")
    return releases

class LikeRequest(BaseModel):
    artist: str
    title: str
    album: str
    score: int = 1

@app.post("/api/playlist/like")
async def like_track(req: LikeRequest, request: Request = None):
    """Send Feedback to ListenBrainz and trigger background Album download if Love."""
    if not config_manager.is_configured or not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured yet.")
        
    cfg = config_manager.config
    lb_username, lb_token, user = await resolve_user_lb_credentials(request)
    if not lb_username or not lb_token:
        raise HTTPException(
            status_code=400,
            detail="ListenBrainz integration is not configured. Please configure your ListenBrainz username and token in settings."
        )
    user_id = user["id"] if user else None

    if not lb_token:
        raise HTTPException(status_code=400, detail="ListenBrainz user token is missing in configuration! Cannot submit Love.")

    from backend.app.clients.listenbrainz import ListenBrainzClient
    
    # Get active playlists
    active_playlists = []
    if user_id:
        user_cfg = await db.get_user_config(user_id)
        if user_cfg and user_cfg.get("active_playlists"):
            active_playlists = user_cfg["active_playlists"]
            if isinstance(active_playlists, str):
                try:
                    active_playlists = json.loads(active_playlists)
                except Exception:
                    active_playlists = []
    if not active_playlists and cfg.listenbrainz.active_playlists:
        active_playlists = cfg.listenbrainz.active_playlists

    fallback_source = (
        active_playlists[0]
        if active_playlists
        else "weekly-exploration"
    )
    lb_client = ListenBrainzClient(
        username=lb_username,
        playlist_source=fallback_source,
        token=lb_token,
    )
    
    # Submit Love/Hate to ListenBrainz.
    # A failure here (e.g. MusicBrainz can't resolve MBID) is logged as a warning
    # but does NOT block the album download for Love actions.
    lb_success = False
    lb_message = ""
    try:
        mbid = getattr(req, "mbid", None) or None
        lb_success = await lb_client.submit_feedback(req.artist, req.title, req.score, mbid=mbid)
        if lb_success:
            lb_message = "Feedback submitted to ListenBrainz."
        else:
            lb_message = "ListenBrainz feedback could not be submitted (check token/MBID). Music action will still proceed."
            logger.warning(f"LB feedback returned False for '{req.artist} - {req.title}'")
    except Exception as e:
        lb_message = f"ListenBrainz submission error: {e}"
        logger.error(f"Error submitting LB feedback for '{req.artist} - {req.title}': {e}")

    if req.score == 1:
        # Always trigger album download on Love regardless of LB outcome
        try:
            from backend.app.album_sync import download_album_task
            download_id = await db.add_album_download(req.artist, req.title, req.album, user_id=user_id)
            _create_tracked_task(
                download_album_task(download_id, req.artist, req.title, req.album, cfg, db, user_id=user_id),
                task_id=f"album:{download_id}",
                task_type="album",
                metadata={"download_id": download_id, "artist": req.artist, "album": req.album}
            )
            msg = "Love noted and album download queued."
            if not lb_success:
                msg += f" (Warning: {lb_message})"
            return {"status": "success", "message": msg}
        except Exception as e:
            logger.error(f"Error queuing album download: {e}")
            raise HTTPException(status_code=500, detail=f"Album download could not be queued: {e}")
    else:
        if not lb_success:
            raise HTTPException(status_code=500, detail=lb_message)
        return {"status": "success", "message": "Feedback submitted successfully."}

@app.post("/api/test/listenbrainz")
async def test_listenbrainz(request: Request):
    """Test connectivity to ListenBrainz and resolve the weekly playlist MBID for the current user."""
    from backend.app.auth import get_current_user
    user = await get_current_user(request)

    cfg = await db.get_user_config(user["id"])
    if not cfg or not cfg.get("lb_username"):
        raise HTTPException(status_code=400, detail="Please configure and save your ListenBrainz username first.")

    from backend.app.clients.listenbrainz import ListenBrainzClient
    active_playlists_raw = cfg.get("active_playlists", "[]")
    active_playlists = []
    if active_playlists_raw:
        try:
            if isinstance(active_playlists_raw, str):
                active_playlists = json.loads(active_playlists_raw)
            else:
                active_playlists = active_playlists_raw
        except Exception:
            pass
    playlist_source = active_playlists[0] if active_playlists else "weekly-exploration"

    client = ListenBrainzClient(
        username=cfg["lb_username"],
        playlist_source=playlist_source,
        token=cfg.get("lb_token", ""),
    )
    try:
        mbid = await client.resolve_playlist_mbid()
        return {"status": "ok", "message": f"Connected! Found playlist MBID: {mbid}"}
    except ValueError as ve:
        # Connection succeeded but playlist not found. Report success with warning note.
        return {"status": "ok", "message": f"Connected successfully! (Note: {str(ve)} This is normal if ListenBrainz hasn't generated your playlist yet.)"}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ListenBrainz test failed: {e}")

@app.post("/api/test/slskd")
async def test_slskd():
    """Test connectivity to the configured slskd instance."""
    if not config_manager.is_configured or not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured yet.")
    cfg = config_manager.config.slskd
    from backend.app.clients.slskd import SlskdClient
    client = SlskdClient(
        base_url=cfg.base_url,
        api_key=cfg.api_key,
    )
    try:
        # Hit the slskd /api/v0/application endpoint to verify connectivity
        import httpx
        url = f"{cfg.base_url.rstrip('/')}/api/v0/application"
        headers = {"X-API-Key": cfg.api_key} if cfg.api_key else {}
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        version = data.get("version", "unknown")
        return {"status": "ok", "message": f"Connected to slskd v{version}"}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"slskd test failed: {e}")

@app.post("/api/test/navidrome")
async def test_navidrome():
    """Test connectivity to Navidrome server."""
    if not config_manager.is_configured or not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured yet.")
    cfg = config_manager.config.navidrome
    if not cfg.url or not cfg.username or not cfg.password:
        raise HTTPException(status_code=400, detail="Navidrome URL, username, and password must be configured.")
    from backend.app.clients.navidrome import NavidromeClient
    client = NavidromeClient(
        url=cfg.url,
        username=cfg.username,
        password=cfg.password,
    )
    try:
        msg = await client.test_connection()
        return {"status": "ok", "message": msg}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Navidrome test failed: {e}")

@app.get("/api/navidrome/stats")
async def get_navidrome_stats(request: Request):
    """Fetch total songs, albums, and artists from Navidrome for the logged-in user."""
    if not config_manager.is_configured or not config_manager.config:
        return {"songs": 0, "albums": 0, "artists": 0}
        
    cfg = config_manager.config.navidrome
    if not cfg.url:
        return {"songs": 0, "albums": 0, "artists": 0}

    from backend.app.auth import get_current_user
    try:
        user = await get_current_user(request)
    except Exception:
        user = None

    if not user:
        return {"songs": 0, "albums": 0, "artists": 0}

    # Fetch user's credentials from DB
    async with db.get_db() as conn:
        async with conn.execute(
            "SELECT username, subsonic_token, subsonic_salt FROM users WHERE id = ?",
            (user["id"],)
        ) as cursor:
            user_row = await cursor.fetchone()

    sub_token = None
    sub_salt = None
    nd_username = None
    nd_password = None

    if user_row:
        user_row_dict = dict(user_row)
        nd_username = user_row_dict["username"]
        sub_token = user_row_dict["subsonic_token"]
        sub_salt = user_row_dict["subsonic_salt"]

    # Fallback to global config if the user is the admin and subsonic parameters aren't stored yet
    if not sub_token or not sub_salt:
        if nd_username == cfg.username:
            nd_username = cfg.username
            nd_password = cfg.password
        else:
            # Regular user without subsonic credentials yet
            return {"songs": 0, "albums": 0, "artists": 0}

    from backend.app.clients.navidrome import NavidromeClient
    client = NavidromeClient(
        url=cfg.url,
        username=nd_username,
        password=nd_password,
        token=sub_token,
        salt=sub_salt,
    )
    
    try:
        stats = await client.get_server_stats()
        return stats
    except Exception as e:
        logger.error(f"Error fetching Navidrome stats for user '{nd_username}': {e}")
        return {"songs": 0, "albums": 0, "artists": 0}

@app.post("/api/library/fix-singles")
async def fix_legacy_singles(request: Request):
    """Trigger background migration to re-tag explore tracks and fix legacy 'Singles' albums in library."""
    if not config_manager.is_configured or not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured yet.")
    
    cfg = config_manager.config
    music_dir = Path(getattr(cfg.paths, "music_dir", "/music"))
    
    from backend.app.scripts.fix_existing_singles import process_music_directory
    from backend.app.clients.navidrome import NavidromeClient
    
    async def _run_migration():
        await process_music_directory(music_dir)
        if cfg.navidrome.url and cfg.navidrome.username:
            nd_client = NavidromeClient(
                url=cfg.navidrome.url,
                username=cfg.navidrome.username,
                password=cfg.navidrome.password
            )
            await nd_client.trigger_rescan()

    asyncio.create_task(_run_migration())
    return {"status": "ok", "message": "Migration task started in background. Library is being scanned and fixed."}

@app.post("/api/library/fix-multidisc")
async def fix_multidisc_albums_endpoint(request: Request):
    """Trigger background migration to re-organize multi-disc albums into Disc 01/Disc 02 subdirectories."""
    if not config_manager.is_configured or not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured yet.")
    
    cfg = config_manager.config
    music_dir = Path(getattr(cfg.paths, "music_dir", "/music"))
    
    from backend.app.scripts.fix_multidisc_albums import fix_multidisc_library
    from backend.app.clients.navidrome import NavidromeClient
    
    async def _run_multidisc_fix():
        await fix_multidisc_library(music_dir)
        if cfg.navidrome.url and cfg.navidrome.username:
            nd_client = NavidromeClient(
                url=cfg.navidrome.url,
                username=cfg.navidrome.username,
                password=cfg.navidrome.password
            )
            await nd_client.trigger_rescan(full_scan=True)

    asyncio.create_task(_run_multidisc_fix())
    return {"status": "ok", "message": "Multi-disc migration task started in background. Albums are being scanned and organized."}

@app.get("/api/musicbrainz/inspect")
async def inspect_musicbrainz_release_endpoint(artist: str, album: str, request: Request):
    """Query MusicBrainz for candidate releases, score them, and return winner details."""
    from backend.app.clients.musicbrainz import inspect_album_releases
    if not artist or not album:
        raise HTTPException(status_code=400, detail="Artist and Album query parameters are required.")
    res = await inspect_album_releases(artist, album)
    return res

@app.post("/api/navidrome/sync_starred")
async def trigger_navidrome_starred_sync(request: Request):
    """Manual trigger to sync starred tracks from Navidrome to ListenBrainz and download albums."""
    if not config_manager.is_configured or not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured yet.")
    
    cfg = config_manager.config
    if not cfg.navidrome.url or not cfg.navidrome.username or not cfg.navidrome.password:
        raise HTTPException(status_code=400, detail="Navidrome credentials are not configured.")
        
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    user_db = await db.get_user_by_id(user_id)
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found in database.")

    # Non-admin users must have subsonic_token and subsonic_salt populated (via login) to fetch starred tracks.
    is_admin_user = (user_db["username"] == cfg.navidrome.username)
    if not is_admin_user:
        if not user_db.get("subsonic_token") or not user_db.get("subsonic_salt"):
            raise HTTPException(
                status_code=400,
                detail="Subsonic connection parameters not found for your session. Please log out of VeryDisco and log back in to activate your Navidrome sync."
            )
        
    logger.info(f"Manual synchronization of Navidrome Starred tracks triggered via WebUI by user '{user['username']}'.")
    _create_tracked_task(
        scheduler_manager.sync_navidrome_starred(cfg, user_id=user_id),
        task_id="sync:navidrome_starred",
        task_type="sync",
        metadata={"source": "navidrome_starred", "user_id": user_id}
    )
    return {"status": "success", "message": "Navidrome Starred tracks synchronization triggered in the background."}


@app.get("/api/logs/stream")
async def stream_logs(request: Request):
    """Server-Sent Events endpoint to stream live logs from the backend directly to the UI."""
    async def log_generator():
        q = asyncio.Queue()
        app_logger.log_subscribers.add(q)
        
        try:
            # Catch up with last 50 logs from SQLite database
            past_logs = await db.get_logs(limit=50)
            for log in past_logs:
                yield f"data: {json.dumps(log)}\n\n"

            # Stream newly emitted logs
            while True:
                # Check connection status
                if await request.is_disconnected():
                    break
                
                log_entry = await q.get()
                yield f"data: {json.dumps(log_entry)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            app_logger.log_subscribers.remove(q)

    return StreamingResponse(log_generator(), media_type="text/event-stream")

# ==============================================================================
# LIBRARY MANAGER & MAINTENANCE ENDPOINTS
# ==============================================================================

def get_primary_artist(artist: str) -> str:
    if not artist:
        return "Unknown Artist"
    
    raw_artist = artist.strip()
    lower_artist = raw_artist.lower()
    
    # AC/DC Exception
    if lower_artist in ["ac/dc", "ac_dc"]:
        return "AC/DC"
        
    import re
    # Split on joint separators (feat, ft, and, &, with, vs, comma, semicolon, slash)
    parts = re.split(r'(?i)\s+(?:feat\.?|ft\.?|and|&|with|vs\.?)\s+|[,;/]', raw_artist)
    if parts:
        primary = parts[0].strip()
        primary = re.sub(r'^["\'\'\s]+|["\'\'\s]+$', '', primary)
        if primary:
            raw_artist = primary

    # Apply artist alias map from config (e.g. Kanye West -> Ye)
    try:
        if config_manager.config and config_manager.config.artist_aliases:
            aliases = config_manager.config.artist_aliases.aliases
            # Case-insensitive match
            raw_lower = raw_artist.lower()
            for alias, canonical in aliases.items():
                if alias.lower() == raw_lower:
                    return canonical
    except Exception:
        pass
    return raw_artist

class DeleteAlbumRequest(BaseModel):
    folder_path: str

def read_file_metadata_with_cache(f_path: Path, metadata_cache: dict, new_cache_entries: list) -> dict:
    import os, re
    f_path_str = str(str(f_path))
    ext = f_path.suffix.lower().strip(".")
    quality_desc = ext.upper()
    artist = "Unknown Artist"
    album = "Unknown Album"
    title = f_path.name
    year = "0000"
    bitrate = 0
    bit_depth = 0
    sample_rate = 0
    duration = 0
    duration = 0
    t_num = 0
    t_total = 0
    disc_num = 1
    disc_total = 1

    try:
        mtime = os.path.getmtime(f_path)
    except Exception:
        mtime = 0.0

    # Folder fallback check for disc number
    parent_name = f_path.parent.name.lower()
    m_disc = re.search(r'^(?:cd|disc|disk)\s*(\d+)$', parent_name)
    if m_disc:
        disc_num = int(m_disc.group(1))

    if metadata_cache and f_path_str in metadata_cache:
        entry = metadata_cache[f_path_str]
        if abs(entry.get("mtime", 0.0) - mtime) < 0.01:
            cached_disc = entry.get("disc_num") or disc_num
            return {
                "artist": entry.get("artist") or "Unknown Artist",
                "album": entry.get("album") or "Unknown Album",
                "title": entry.get("title") or f_path.name,
                "year": entry.get("year") or "0000",
                "track_num": entry.get("track_num") or 0,
                "total_tracks": entry.get("total_tracks") or 0,
                "disc_num": cached_disc,
                "disc_total": entry.get("disc_total") or 1,
                "quality_desc": entry.get("quality_desc") or "",
                "bitrate": entry.get("bitrate") or 0,
                "bit_depth": entry.get("bit_depth") or 0,
                "sample_rate": entry.get("sample_rate") or 0,
                "duration": entry.get("duration") or 0
            }

    # Cache miss or mismatch -> parse with mutagen
    try:
        if ext == "mp3":
            from mutagen.mp3 import MP3
            audio = MP3(f_path)
            duration = int(audio.info.length)
            bitrate = int(audio.info.bitrate / 1000) if audio.info.bitrate else 0
            sample_rate = audio.info.sample_rate
            
            # Read EasyID3 tags
            try:
                from mutagen.easyid3 import EasyID3
                easy_audio = EasyID3(f_path)
                artist = easy_audio.get("artist", [""])[0]
                album = easy_audio.get("album", [""])[0]
                title = easy_audio.get("title", [""])[0]
                year = easy_audio.get("date", [""])[0] or "0000"
                tr = easy_audio.get("tracknumber", [""])[0]
                if "/" in tr:
                    t_num = int(tr.split("/")[0])
                    t_total = int(tr.split("/")[1])
                elif tr:
                    t_num = int(tr)
                if easy_audio.get("tracktotal"):
                    t_total = int(easy_audio.get("tracktotal")[0])
                elif easy_audio.get("totaltracks"):
                    t_total = int(easy_audio.get("totaltracks")[0])

                disc_str = easy_audio.get("discnumber", [""])[0]
                if "/" in disc_str:
                    try:
                        disc_num = int(disc_str.split("/")[0])
                        disc_total = int(disc_str.split("/")[1])
                    except Exception:
                        pass
                elif disc_str.isdigit():
                    disc_num = int(disc_str)
            except Exception:
                pass
                
        elif ext == "flac":
            from mutagen.flac import FLAC
            audio = FLAC(f_path)
            duration = int(audio.info.length)
            bit_depth = audio.info.bits_per_sample
            sample_rate = audio.info.sample_rate
            bitrate = int(audio.info.bitrate / 1000) if audio.info.bitrate else 0
            
            artist = audio.get("artist", [""])[0]
            album = audio.get("album", [""])[0]
            title = audio.get("title", [""])[0]
            year = audio.get("date", [""])[0] or "0000"
            tr = audio.get("tracknumber", [""])[0]
            if "/" in tr:
                t_num = int(tr.split("/")[0])
                t_total = int(tr.split("/")[1])
            elif tr:
                t_num = int(tr)
            if audio.get("tracktotal"):
                t_total = int(audio.get("tracktotal")[0])
            elif audio.get("totaltracks"):
                t_total = int(audio.get("totaltracks")[0])

            disc_str = audio.get("discnumber", [""])[0]
            if "/" in disc_str:
                try:
                    disc_num = int(disc_str.split("/")[0])
                    disc_total = int(disc_str.split("/")[1])
                except Exception:
                    pass
            elif disc_str.isdigit():
                disc_num = int(disc_str)
                
        elif ext in ["m4a", "mp4"]:
            from mutagen.mp4 import MP4
            audio = MP4(f_path)
            duration = int(audio.info.length)
            bitrate = int(audio.info.bitrate / 1000) if audio.info.bitrate else 0
            sample_rate = audio.info.sample_rate
            
            try:
                from mutagen.easymp4 import EasyMP4
                easy_audio = EasyMP4(f_path)
                artist = easy_audio.get("artist", [""])[0]
                album = easy_audio.get("album", [""])[0]
                title = easy_audio.get("title", [""])[0]
                year = easy_audio.get("date", [""])[0] or "0000"
            except Exception:
                pass
            if "trkn" in audio:
                t_num = audio["trkn"][0][0]
                t_total = audio["trkn"][0][1]
            if "disk" in audio and audio["disk"]:
                disc_num = audio["disk"][0][0] or disc_num
                disc_total = audio["disk"][0][1] or disc_total
    except Exception:
        pass

    # Build quality description
    quality_desc = f"{ext.upper()}"
    if ext == "flac":
        quality_desc += f" ({bit_depth}bit/{sample_rate/1000:.1f}kHz)"
    elif bitrate > 0:
        quality_desc += f" ({bitrate}kbps)"

    # Fallbacks if tags missing
    if not title or not artist:
        basename = f_path.stem
        clean_title = re.sub(r'^\d+\s*[-_.]?\s*', '', basename).strip(' -_')
        title = title or clean_title
        artist = artist or (f_path.parent.parent.name if f_path.parent.parent else "Unknown Artist")
        album = album or f_path.parent.name
        
    if t_num == 0:
        try:
            match = re.match(r'^(\d+)', f_path.name)
            if match:
                t_num = int(match.group(1))
        except Exception:
            pass

    res = {
        "artist": artist.strip() or "Unknown Artist",
        "album": album.strip() or "Unknown Album",
        "title": title.strip() or f_path.name,
        "year": year.strip()[:4] if year.strip() else "0000",
        "track_num": t_num,
        "total_tracks": t_total,
        "disc_num": disc_num,
        "disc_total": disc_total,
        "quality_desc": quality_desc,
        "bitrate": bitrate,
        "bit_depth": bit_depth,
        "sample_rate": sample_rate,
        "duration": duration
    }

    if new_cache_entries is not None:
        new_cache_entries.append((
            f_path_str,
            mtime,
            res["artist"],
            res["album"],
            res["title"],
            res["track_num"],
            res["total_tracks"],
            res["quality_desc"],
            res["bitrate"],
            res["bit_depth"],
            res["sample_rate"],
            res["duration"],
            res["year"]
        ))
    return res

def check_file_has_embedded_artwork(f_path: Path) -> bool:
    ext = f_path.suffix.lower().strip(".")
    try:
        if ext == "mp3":
            from mutagen.id3 import ID3
            tags = ID3(f_path)
            return any(k.startswith("APIC") for k in tags.keys())
        elif ext == "flac":
            from mutagen.flac import FLAC
            audio = FLAC(f_path)
            return len(audio.pictures) > 0
        elif ext in ["m4a", "mp4"]:
            from mutagen.mp4 import MP4
            audio = MP4(f_path)
            return "covr" in audio and bool(audio["covr"])
    except Exception:
        pass
    return False

def get_all_album_folders(music_dir: Path, metadata_cache: dict = None, new_cache_entries: list = None) -> list:
    """Finds all album folders in the music directory recursively, grouping multi-disc sets."""
    import re, os
    playlists_dir = Path(config_manager.config.paths.navidrome_playlists_dir).resolve()
    raw_folders = {}
    for root, dirs, files in os.walk(str(music_dir)):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        root_path = Path(root).resolve()
        if root_path == playlists_dir or playlists_dir in root_path.parents or "playlists" in root_path.parts:
            continue
        audio_files = []
        has_cover = False
        for f in files:
            f_lower = f.lower()
            if f_lower.endswith((".mp3", ".flac", ".m4a")):
                audio_files.append(Path(root) / f)
            elif f_lower.endswith((".jpg", ".jpeg", ".png")):
                if any(x in f_lower for x in ["cover", "folder", "album", "front", "art"]):
                    has_cover = True
        if not has_cover and audio_files:
            if check_file_has_embedded_artwork(audio_files[0]):
                has_cover = True

        if audio_files:
            raw_folders[root] = {
                "files": audio_files,
                "has_cover": has_cover
            }

    collapsed = {}
    for folder_path, info in raw_folders.items():
        p = Path(folder_path)
        if re.match(r'^(cd|disc|disk|vol|volume)\s*\d+$', p.name.lower()):
            parent_path = str(p.parent)
            if parent_path not in collapsed:
                collapsed[parent_path] = {
                    "files": [],
                    "has_cover": raw_folders.get(parent_path, {}).get("has_cover", False)
                }
            collapsed[parent_path]["files"].extend(info["files"])
            if info["has_cover"]:
                collapsed[parent_path]["has_cover"] = True
        else:
            if folder_path not in collapsed:
                collapsed[folder_path] = {
                    "files": [],
                    "has_cover": info["has_cover"]
                }
            collapsed[folder_path]["files"].extend(info["files"])

    merged_albums = {}
    import json

    for folder_path_str, info in collapsed.items():
        folder_path = Path(folder_path_str)
        audio_files = info["files"]
        if not audio_files:
            continue

        sample_file = audio_files[0]
        artist_name = folder_path.parent.name if folder_path.parent != music_dir else "Unknown Artist"
        album_name = folder_path.name
        
        try:
            meta = read_file_metadata_with_cache(sample_file, metadata_cache, new_cache_entries)
            artist_name = meta["artist"]
            album_name = meta["album"]
        except Exception:
            pass

        primary_artist = get_primary_artist(artist_name)
        key = f"{primary_artist.lower()}|{album_name.lower()}"
        if key not in merged_albums:
            merged_albums[key] = {
                "artist_name": primary_artist,
                "album_name": album_name,
                "folders": [],
                "audio_files": [],
                "has_cover": False
            }
        
        merged_albums[key]["folders"].append(folder_path_str)
        merged_albums[key]["audio_files"].extend(audio_files)
        if info["has_cover"]:
            merged_albums[key]["has_cover"] = True

    albums_list = []
    for key, data in merged_albums.items():
        artist_name = data["artist_name"]
        album_name = data["album_name"]
        audio_files = data["audio_files"]
        folder_path_str = json.dumps(data["folders"])
        
        sample_file = audio_files[0]
        meta = read_file_metadata_with_cache(sample_file, metadata_cache, new_cache_entries)
        quality_desc = meta["quality_desc"]

        total_tracks = 0
        track_nums = set()
        disc_tracks = {}
        for f_path in audio_files:
            try:
                t_meta = read_file_metadata_with_cache(f_path, metadata_cache, new_cache_entries)
                t_num = t_meta["track_num"]
                d_num = t_meta.get("disc_num", 1)
                t_total = t_meta["total_tracks"]
                if t_num > 0:
                    track_nums.add(t_num)
                    if d_num not in disc_tracks:
                        disc_tracks[d_num] = set()
                    disc_tracks[d_num].add(t_num)
                if t_total > 0:
                    total_tracks = max(total_tracks, t_total)
            except Exception:
                pass

        track_count = len(audio_files)
        total_size = sum(f.stat().st_size for f in audio_files)

        if len(disc_tracks) > 1:
            total_tracks = sum(max(t_set) for t_set in disc_tracks.values() if t_set)
        elif total_tracks == 0 and track_nums:
            total_tracks = max(track_nums)

        if track_count > total_tracks:
            total_tracks = track_count

        status = "fully"
        if total_tracks > 0:
            if track_count < total_tracks:
                status = "partially"
        else:
            if track_nums:
                max_num = max(track_nums)
                expected_set = set(range(1, max_num + 1))
                if track_nums != expected_set:
                    status = "partially"

        albums_list.append({
            "artist": artist_name,
            "album": album_name,
            "track_count": track_count,
            "total_size": total_size,
            "quality": quality_desc,
            "folder_path": folder_path_str,
            "has_cover": data["has_cover"],
            "total_tracks": total_tracks,
            "status": status
        })
    return albums_list

def _perform_library_scan_sync(music_dir: Path, metadata_cache: dict = None, new_cache_entries: list = None):
    import os
    if new_cache_entries is None:
        new_cache_entries = []
    albums_list = get_all_album_folders(music_dir, metadata_cache, new_cache_entries)
    albums_list.sort(key=lambda x: (x["artist"].lower(), x["album"].lower()))
    
    missing_lyrics = []
    for alb in albums_list:
        import json
        try:
            folders = json.loads(alb["folder_path"])
        except Exception:
            folders = [alb["folder_path"]]
            
        audio_files = []
        for folder_path_str in folders:
            for root, _, files in os.walk(folder_path_str):
                for f in files:
                    if f.lower().endswith((".mp3", ".flac", ".m4a")):
                        audio_files.append(Path(root) / f)
                    
        for f_path in audio_files:
            lrc_path = f_path.with_suffix(".lrc")
            if not lrc_path.is_file():
                try:
                    meta = read_file_metadata_with_cache(f_path, metadata_cache, new_cache_entries)
                    missing_lyrics.append({
                        "artist": meta["artist"],
                        "title": meta["title"],
                        "album": meta["album"],
                        "filepath": str(f_path),
                        "duration": meta["duration"]
                    })
                except Exception:
                    pass
    return albums_list, missing_lyrics

def _perform_combined_scan_sync(music_dir: Path, silenced: set, playlists_dir: Path, metadata_cache: dict = None, new_cache_entries: list = None):
    import os
    if new_cache_entries is None:
        new_cache_entries = []
    albums_list = get_all_album_folders(music_dir, metadata_cache, new_cache_entries)
    albums_list.sort(key=lambda x: (x["artist"].lower(), x["album"].lower()))
    
    missing_lyrics = []
    for alb in albums_list:
        import json
        try:
            folders = json.loads(alb["folder_path"])
        except Exception:
            folders = [alb["folder_path"]]
            
        audio_files = []
        for folder_path_str in folders:
            for root, _, files in os.walk(folder_path_str):
                for f in files:
                    if f.lower().endswith((".mp3", ".flac", ".m4a")):
                        audio_files.append(Path(root) / f)
                    
        for f_path in audio_files:
            lrc_path = f_path.with_suffix(".lrc")
            if not lrc_path.is_file():
                try:
                    meta = read_file_metadata_with_cache(f_path, metadata_cache, new_cache_entries)
                    missing_lyrics.append({
                        "artist": meta["artist"],
                        "title": meta["title"],
                        "album": meta["album"],
                        "filepath": str(f_path),
                        "duration": meta["duration"]
                    })
                except Exception:
                    pass

    maintenance_issues = run_maintenance_scan_internal_sync(
        music_dir, silenced, playlists_dir, metadata_cache, new_cache_entries, albums_list
    )
    return albums_list, missing_lyrics, maintenance_issues

async def run_full_library_audit():
    """Perform a full sync scan of albums, maintenance issues, and missing lyrics, then write to cache."""
    if not config_manager.config:
        return
        
    music_dir = Path(config_manager.config.paths.music_dir)
    playlists_dir = Path(config_manager.config.paths.navidrome_playlists_dir).resolve()
    if not music_dir.exists():
        return
        
    silenced = await db.get_silenced_issues()
    
    # Get metadata cache from database
    metadata_cache = await db.get_all_file_metadata()
    new_cache_entries = []
    
    albums_list, missing_lyrics, maintenance_issues = await asyncio.to_thread(
        _perform_combined_scan_sync, music_dir, silenced, playlists_dir, metadata_cache, new_cache_entries
    )
    
    # Save any new metadata cache entries to database
    if new_cache_entries:
        await db.save_file_metadata_batch(new_cache_entries)
        
    await db.set_cache("albums", albums_list)
    await db.set_cache("missing_lyrics", missing_lyrics)
    await db.set_cache("maintenance", maintenance_issues)
    
    users = await db.list_users()
    for u in users:
        u_id = u["id"]
        if not u.get("music_dir"):
            await db.set_cache(f"albums_{u_id}", albums_list)
            await db.set_cache(f"missing_lyrics_{u_id}", missing_lyrics)
            await db.set_cache(f"maintenance_{u_id}", maintenance_issues)

async def _append_acoustid_failures(issues: list, silenced: set):
    acoustid_results = await db.get_acoustid_results()
    
    # Only include true mismatches — NOT "not found in DB" results.
    # A track not in the AcoustID database is not a library problem.
    _SKIP_REASONS = [
        "no match found in acoustid database",
        "no match found",
        "acoustid api key is not configured",
        "failed to generate audio fingerprint",
    ]

    def _read_tags_sync(f_path):
        try:
            return read_basic_tags(f_path)
        except Exception:
            return {}
            
    for r in acoustid_results:
        if r["status"] != "failed":
            continue
        # Skip results that are simply "not found" — only flag true mismatches
        reason_lower = (r.get("reason") or "").lower()
        if any(skip in reason_lower for skip in _SKIP_REASONS):
            continue
        f_path = Path(r["file_path"])
        if str(f_path) in silenced:
            continue
        if not f_path.exists():
            continue
        try:
            meta = await asyncio.to_thread(_read_tags_sync, f_path)
            artist = meta.get("artist") or "Unknown Artist"
            title = meta.get("title") or f_path.name
            album = meta.get("album") or ""
        except Exception:
            artist = "Unknown Artist"
            title = f_path.name
            album = ""
            
        issues.append({
            "id": f"acoustid_mismatch_{hash(str(f_path))}",
            "type": "acoustid_mismatch",
            "severity": "high",
            "title": f"AcoustID Mismatch: '{artist} - {title}'",
            "description": f"Fingerprint verification failed.\nReason: {r['reason']}\nFile: {f_path}",
            "target_path": str(f_path),
            "actions": [
                {
                    "name": "Redownload Track",
                    "action": "redownload_acoustid_track",
                    "label": "Delete and redownload via Slskd",
                    "params": {
                        "artist": artist,
                        "title": title,
                        "album": album
                    }
                },
                {
                    "name": "Delete File",
                    "action": "delete_file",
                    "label": "Delete file from disk"
                }
            ]
        })

async def run_maintenance_scan_internal_user(user_id: str):
    if not config_manager.config:
        return []
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(config_manager.config.paths.music_dir)
    playlists_dir = Path(config_manager.config.paths.navidrome_playlists_dir).resolve()
    if user_row:
        if user_row.get("music_dir"):
            music_dir = Path(user_row["music_dir"])
        if user_row.get("playlist_dir"):
            playlists_dir = Path(user_row["playlist_dir"]).resolve()
            
    if not music_dir.exists():
        return []
    silenced = await db.get_silenced_issues()
    
    metadata_cache = await db.get_all_file_metadata()
    new_cache_entries = []
    
    issues = await asyncio.to_thread(
        run_maintenance_scan_internal_sync, music_dir, silenced, playlists_dir, metadata_cache, new_cache_entries
    )
    
    if new_cache_entries:
        await db.save_file_metadata_batch(new_cache_entries)
        
    await _append_acoustid_failures(issues, silenced)
    return issues

def _scan_and_warmup_metadata_sync(music_dir: Path, playlists_dir: Path, metadata_cache: dict, progress_callback):
    import os
    audio_files = []
    for root, dirs, files in os.walk(str(music_dir)):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        root_path = Path(root).resolve()
        if root_path == playlists_dir or playlists_dir in root_path.parents or "playlists" in root_path.parts:
            continue
        for f in files:
            if f.lower().endswith((".mp3", ".flac", ".m4a")):
                audio_files.append(Path(root) / f)
    
    total = len(audio_files)
    if total == 0:
        progress_callback(0, 0, [])
        return
        
    new_entries = []
    for idx, f_path in enumerate(audio_files, 1):
        try:
            read_file_metadata_with_cache(f_path, metadata_cache, new_entries)
        except Exception:
            pass
        if idx % 20 == 0 or idx == total:
            progress_callback(idx, total, list(new_entries))
            new_entries.clear()

library_scan_progress = {}

async def run_library_scan_task(user_id: str, music_dir: Path):
    global library_scan_progress
    library_scan_progress[user_id] = {
        "status": "scanning",
        "processed": 0,
        "total": 0,
        "percentage": 0
    }
    try:
        playlists_dir = Path(config_manager.config.paths.navidrome_playlists_dir).resolve()
        user_row = await db.get_user_by_id(user_id)
        if user_row and user_row.get("playlist_dir"):
            playlists_dir = Path(user_row["playlist_dir"]).resolve()
            
        silenced = await db.get_silenced_issues()
        metadata_cache = await db.get_all_file_metadata()
        
        loop = asyncio.get_running_loop()
        def progress_callback(idx, total, new_entries):
            def update():
                library_scan_progress[user_id]["processed"] = idx
                library_scan_progress[user_id]["total"] = total
                library_scan_progress[user_id]["percentage"] = int((idx / total) * 100) if total > 0 else 100
                if new_entries:
                    asyncio.create_task(db.save_file_metadata_batch(new_entries))
            loop.call_soon_threadsafe(update)
            
        await asyncio.to_thread(
            _scan_and_warmup_metadata_sync, music_dir, playlists_dir, metadata_cache, progress_callback
        )
        
        # Wait a small delay to let any last DB batch writes flush
        await asyncio.sleep(0.5)

        albums_list, missing_lyrics, maintenance_issues = await asyncio.to_thread(
            _perform_combined_scan_sync, music_dir, silenced, playlists_dir, metadata_cache, None
        )
        
        await db.set_cache(f"albums_{user_id}", albums_list)
        await db.set_cache(f"missing_lyrics_{user_id}", missing_lyrics)
        await db.set_cache(f"maintenance_{user_id}", maintenance_issues)
        
        library_scan_progress[user_id]["status"] = "completed"
        library_scan_progress[user_id]["percentage"] = 100
    except Exception as e:
        logger.error(f"Library scan failed for user {user_id}: {e}")
        library_scan_progress[user_id]["status"] = "failed"
        library_scan_progress[user_id]["error"] = str(e)

@app.post("/api/library/scan")
async def trigger_full_library_scan(request: Request):
    """Trigger a full audit scan in the background for current user."""
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(config_manager.config.paths.music_dir)
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"])
        
    if not music_dir.exists():
        return {"status": "success", "message": "Library path does not exist."}
        
    if library_scan_progress.get(user_id, {}).get("status") == "scanning":
        return {"status": "success", "message": "Scan already in progress."}
        
    _create_tracked_task(
        run_library_scan_task(user_id, music_dir),
        task_id=f"library_scan:{user_id}",
        task_type="library_scan",
        metadata={"user_id": user_id}
    )
    return {"status": "success", "message": "Library scan triggered successfully."}

@app.get("/api/library/scan/progress")
async def get_library_scan_progress(request: Request):
    """Retrieve progress of ongoing library scan for current user."""
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    return library_scan_progress.get(user_id, {"status": "idle", "processed": 0, "total": 0, "percentage": 0})

_album_list_lock = asyncio.Lock()

@app.get("/api/library/albums")
async def get_library_albums(request: Request):
    """List all albums/singles folders in the current user's music directory."""
    if not config_manager.config:
        return []
        
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    cached = await db.get_cache(f"albums_{user_id}")
    if cached is not None:
        return cached
        
    async with _album_list_lock:
        # Double check cache
        cached = await db.get_cache(f"albums_{user_id}")
        if cached is not None:
            return cached
            
        user_row = await db.get_user_by_id(user_id)
        music_dir = Path(config_manager.config.paths.music_dir)
        if user_row and user_row.get("music_dir"):
            music_dir = Path(user_row["music_dir"])
            
        if not music_dir.exists():
            return []
    
        # Get metadata cache from database
        metadata_cache = await db.get_all_file_metadata()
        new_cache_entries = []
        
        albums_list = await asyncio.to_thread(get_all_album_folders, music_dir, metadata_cache, new_cache_entries)
        albums_list.sort(key=lambda x: (x["artist"].lower(), x["album"].lower()))
        
        # Save any new metadata cache entries to database
        if new_cache_entries:
            await db.save_file_metadata_batch(new_cache_entries)
            
        await db.set_cache(f"albums_{user_id}", albums_list)
        return albums_list

from functools import lru_cache

@lru_cache(maxsize=512)
def _extract_cover_sync(folder_path_str: str) -> tuple[bytes | None, str | None, str | None]:
    try:
        target = Path(folder_path_str)
        if not target.exists():
            return None, None, None
            
        # Search local images
        for f in target.iterdir():
            if f.is_file() and f.suffix.lower() in [".jpg", ".jpeg", ".png"]:
                if any(x in f.name.lower() for x in ["cover", "folder", "album", "front", "art"]):
                    return None, None, str(f)
                    
        for f in target.iterdir():
            if f.is_file() and f.suffix.lower() in [".jpg", ".jpeg", ".png"]:
                return None, None, str(f)
                
        # Try embedded tags
        audio_files = [f for f in target.iterdir() if f.is_file() and f.suffix.lower() in [".mp3", ".flac", ".m4a"]]
        if audio_files:
            sample_file = audio_files[0]
            ext = sample_file.suffix.lower().strip(".")
            if ext == "mp3":
                from mutagen.mp3 import MP3
                audio = MP3(sample_file)
                for key in audio.keys():
                    if key.startswith("APIC:"):
                        return audio[key].data, audio[key].mime, None
            elif ext == "flac":
                from mutagen.flac import FLAC
                audio = FLAC(sample_file)
                if audio.pictures:
                    return audio.pictures[0].data, audio.pictures[0].mime, None
            elif ext in ["m4a", "mp4"]:
                from mutagen.mp4 import MP4
                audio = MP4(sample_file)
                if "covr" in audio and audio["covr"]:
                    return bytes(audio["covr"][0]), "image/jpeg", None
    except Exception:
        pass
    return None, None, None

@app.get("/api/library/albums/cover")
async def get_album_cover(folder_path: str, request: Request):
    """Retrieve local or embedded album artwork for current user."""
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    import json
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(config_manager.config.paths.music_dir).resolve()
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"]).resolve()
        
    try:
        targets = [Path(p).resolve() for p in json.loads(folder_path)]
    except Exception:
        targets = [Path(folder_path).resolve()]
        
    target = targets[0] if targets else Path(folder_path).resolve()
    if not str(target).startswith(str(music_dir)):
        logger.error(f"Access denied in get_album_cover: target {target} does not start with music_dir {music_dir} (user: {user_id})")
        raise HTTPException(status_code=403, detail="Access denied")
        
    if not target.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
        
    data, mime, file_path = await asyncio.to_thread(_extract_cover_sync, str(target))
    if file_path:
        return FileResponse(file_path)
    if data:
        return Response(content=data, media_type=mime)
        
    raise HTTPException(status_code=404, detail="No cover art found")

def _get_local_tracks_for_album_sync(target: Path, metadata_cache: dict, new_cache_entries: list) -> list:
    import os, re
    audio_files = []
    for root, _, files in os.walk(str(target)):
        for f in files:
            if f.lower().endswith((".mp3", ".flac", ".m4a")):
                audio_files.append(Path(root) / f)
                
    local_tracks = []
    for f_path in audio_files:
        meta = read_file_metadata_with_cache(f_path, metadata_cache, new_cache_entries)
        local_tracks.append({
            "title": meta["title"],
            "track_num": meta["track_num"],
            "disc_num": meta.get("disc_num", 1),
            "filepath": str(f_path),
            "normalized_title": re.sub(r'[^\w]', '', meta["title"]).lower()
        })
    return local_tracks

@app.get("/api/library/albums/tracks")
async def get_library_album_tracks(folder_path: str, request: Request):
    """Fetch complete list of album tracks matching Deezer schema against on-disk files for current user."""
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    import json
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(config_manager.config.paths.music_dir).resolve()
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"]).resolve()
        
    try:
        targets = [Path(p).resolve() for p in json.loads(folder_path)]
    except Exception:
        targets = [Path(folder_path).resolve()]
        
    target = targets[0] if targets else Path(folder_path).resolve()
    if not str(target).startswith(str(music_dir)):
        logger.error(f"Access denied in get_library_album_tracks: target {target} does not start with music_dir {music_dir} (user: {user_id})")
        raise HTTPException(status_code=403, detail="Access denied")
        
    if not target.exists():
        raise HTTPException(status_code=404, detail="Folder not found")

    metadata_cache = await db.get_all_file_metadata()
    new_cache_entries = []
    
    local_tracks = await asyncio.to_thread(
        _get_local_tracks_for_album_sync, target, metadata_cache, new_cache_entries
    )
    
    if new_cache_entries:
        await db.save_file_metadata_batch(new_cache_entries)
        
    album_name = target.name
    artist_name = target.parent.name if target.parent != music_dir else ""
    
    official_tracks = []
    try:
        from backend.app.clients.musicbrainz import musicbrainz_client
        mb_res = await musicbrainz_client.get_album_tracklist(artist_name, album_name)
        if mb_res:
            official_tracks = mb_res.get("tracks", [])
    except Exception as e:
        logger.warning(f"Failed to fetch tracklist from MusicBrainz for {artist_name} - {album_name}: {e}")

    results = []
    if official_tracks:
        from backend.app.clients.musicbrainz import _normalize, _fuzzy_match
        used_local_indices = set()
        matches = [None] * len(official_tracks)

        # Pass 1: Title matching (exact normalized, substring, or fuzzy)
        for idx, t in enumerate(official_tracks):
            d_title = t.get("title", "")
            d_norm = _normalize(d_title)
            for l_idx, lt in enumerate(local_tracks):
                if l_idx in used_local_indices:
                    continue
                lt_norm = _normalize(lt.get("title", ""))
                if d_norm and lt_norm and (d_norm == lt_norm or d_norm in lt_norm or lt_norm in d_norm or _fuzzy_match(d_title, lt.get("title", ""))):
                    matches[idx] = lt
                    used_local_indices.add(l_idx)
                    break

        # Pass 2: Track number + Disc number matching for remaining un-matched
        for idx, t in enumerate(official_tracks):
            if matches[idx] is not None:
                continue
            d_num = t.get("track_position", 0)
            disc_num = t.get("disk_number", 1)
            for l_idx, lt in enumerate(local_tracks):
                if l_idx in used_local_indices:
                    continue
                lt_disc = lt.get("disc_num", 1)
                if lt.get("track_num") == d_num and lt_disc == disc_num:
                    matches[idx] = lt
                    used_local_indices.add(l_idx)
                    break

        for idx, t in enumerate(official_tracks):
            d_title = t.get("title", "")
            d_num = t.get("track_position", 0)
            disc_num = t.get("disk_number", 1)
            matched_lt = matches[idx]
            results.append({
                "title": d_title,
                "track_num": d_num,
                "disc_num": disc_num,
                "exists": matched_lt is not None,
                "filepath": matched_lt["filepath"] if matched_lt else None
            })
    else:
        local_tracks.sort(key=lambda x: (x.get("disc_num", 1), x["track_num"]))
        for lt in local_tracks:
            results.append({
                "title": lt["title"],
                "track_num": lt["track_num"],
                "disc_num": lt.get("disc_num", 1),
                "exists": True,
                "filepath": lt["filepath"]
            })
            
    return results

@app.delete("/api/library/albums")
async def delete_library_album(req: DeleteAlbumRequest, request: Request):
    """Delete an entire album folder from disk and trigger Navidrome scan."""
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")

    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    
    music_dir_path = config_manager.config.paths.music_dir
    user_row = await db.get_user_by_id(user["id"])
    if user_row and user_row.get("music_dir"):
        music_dir_path = user_row["music_dir"]

    import json
    try:
        folders = json.loads(req.folder_path)
        if not isinstance(folders, list):
            folders = [req.folder_path]
    except Exception:
        folders = [req.folder_path]

    music_dir = Path(music_dir_path).resolve()
    valid_paths = []
    for fp in folders:
        target = Path(fp)
        resolved_target = target.resolve()
        if not str(resolved_target).startswith(str(music_dir)):
            raise HTTPException(status_code=403, detail="Access denied. Path must be inside the music directory.")
        valid_paths.append(target)

    try:
        for target in valid_paths:
            if not target.exists():
                continue
            if target.is_dir():
                shutil.rmtree(str(target))
            else:
                target.unlink()

            parent = target.parent
            if parent != music_dir and parent.is_dir() and not os.listdir(str(parent)):
                shutil.rmtree(str(parent))

        await trigger_navidrome_scan_debounced()

        return {"status": "success", "message": f"Successfully deleted '{target.name}' and triggered Navidrome rescan."}
    except Exception as e:
        logger.error(f"Failed to delete library folder: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class FixIssueRequest(BaseModel):
    issue_type: str
    target_path: str
    action: str
    params: Optional[dict] = None

class IgnoreIssueRequest(BaseModel):
    issue_type: str
    target_path: str

def read_basic_tags(file_path: Path) -> dict:
    ext = file_path.suffix.lower().strip(".")
    title = ""
    artist = ""
    album = ""
    year = ""
    track = ""
    genre = ""
    try:
        if ext == "mp3":
            from mutagen.easyid3 import EasyID3
            audio = EasyID3(file_path)
            title = audio.get("title", [""])[0]
            artist = audio.get("artist", [""])[0]
            album = audio.get("album", [""])[0]
            year = audio.get("date", [""])[0]
            track = audio.get("tracknumber", [""])[0]
            genre = audio.get("genre", [""])[0]
        elif ext == "flac":
            from mutagen.flac import FLAC
            audio = FLAC(file_path)
            title = audio.get("title", [""])[0]
            artist = audio.get("artist", [""])[0]
            album = audio.get("album", [""])[0]
            year = audio.get("date", [""])[0]
            track = audio.get("tracknumber", [""])[0]
            genre = audio.get("genre", [""])[0]
        elif ext in ["m4a", "mp4"]:
            from mutagen.easymp4 import EasyMP4
            audio = EasyMP4(file_path)
            title = audio.get("title", [""])[0]
            artist = audio.get("artist", [""])[0]
            album = audio.get("album", [""])[0]
            year = audio.get("date", [""])[0]
            track = audio.get("tracknumber", [""])[0]
            genre = audio.get("genre", [""])[0]
    except Exception:
        pass
    
    if not title or not artist:
        basename = file_path.stem
        clean_title = re.sub(r'^\d+\s*[-_.]?\s*', '', basename).strip(' -_')
        title = title or clean_title
        artist = artist or file_path.parent.parent.name
        album = album or file_path.parent.name
        
    return {
        "title": title.strip(),
        "artist": artist.strip(),
        "album": album.strip(),
        "year": year.strip(),
        "track": track.strip(),
        "genre": genre.strip()
    }

@app.get("/api/maintenance/scan")
async def scan_maintenance(refresh: bool = False):
    if not config_manager.config:
        return []
    if refresh:
        await db.delete_cache("maintenance")
    else:
        cached = await db.get_cache("maintenance")
        if cached is not None:
            return cached
    issues = await run_maintenance_scan_internal()
    await db.set_cache("maintenance", issues)
    return issues

async def run_maintenance_scan_internal():
    if not config_manager.config:
        return []
    music_dir = Path(config_manager.config.paths.music_dir)
    playlists_dir = Path(config_manager.config.paths.navidrome_playlists_dir).resolve()
    if not music_dir.exists():
        return []

    silenced = await db.get_silenced_issues()
    
    metadata_cache = await db.get_all_file_metadata()
    new_cache_entries = []
    
    issues = await asyncio.to_thread(
        run_maintenance_scan_internal_sync, music_dir, silenced, playlists_dir, metadata_cache, new_cache_entries
    )
    
    if new_cache_entries:
        await db.save_file_metadata_batch(new_cache_entries)
        
    await _append_acoustid_failures(issues, silenced)
    return issues

def is_valid_album_artist(album_artist: str, track_artist: str) -> bool:
    if not album_artist:
        return False
    a_art = album_artist.strip().lower()
    t_art = track_artist.strip().lower()
    if a_art == t_art:
        return True
    
    # Strip common feature suffixes
    t_art_clean = re.split(r'\bfeat\b|\bft\b', t_art, flags=re.IGNORECASE)[0].strip()
    if a_art == t_art_clean:
        return True
        
    # Support joint artists (e.g. Drake & 21 Savage vs Drake)
    a_art_parts = [p.strip() for p in re.split(r'[&,]', a_art) if p.strip()]
    t_art_parts = [p.strip() for p in re.split(r'[&,]', t_art_clean) if p.strip()]
    if any(p in t_art_parts for p in a_art_parts) or any(p in a_art_parts for p in t_art_parts):
        return True
        
    return False

def run_maintenance_scan_internal_sync(music_dir: Path, silenced: set, playlists_dir: Path, metadata_cache: dict = None, new_cache_entries: list = None, album_folders_list: list = None):
    issues = []

    # Get all actual library albums recursively (excludes playlists folder!)
    if album_folders_list is None:
        album_folders_list = get_all_album_folders(music_dir, metadata_cache, new_cache_entries)
    
    # Complete list of all audio files across the entire /music folder (including playlists)
    all_tracks = []
    
    for root, dirs, files in os.walk(str(music_dir)):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in ["playlists", "navidrome_playlists", "explore"]]
        if ".staging" in root:
            continue
            
        for f in files:
            if f.lower().endswith((".mp3", ".flac", ".m4a")):
                f_path = Path(root) / f
                if str(f_path) in silenced:
                    continue
                    
                meta = read_file_metadata_with_cache(f_path, metadata_cache, new_cache_entries)
                bitrate = meta["bitrate"]
                bit_depth = meta["bit_depth"]
                sample_rate = meta["sample_rate"]
                
                # Check for dirty metadata
                has_dirty = False
                dirty_reason = ""
                album_artist_tag = ""
                date_tag = ""
                try:
                    ext_lower = f_path.suffix.lower().strip(".")
                    if ext_lower == "mp3":
                        from mutagen.id3 import ID3
                        tags = ID3(f_path)
                        has_comment = any(k.startswith("COMM") for k in tags.keys())
                        tpe2_list = tags.getall("TPE2")
                        tpe2 = tpe2_list[0].text[0] if tpe2_list and tpe2_list[0].text else ""
                        album_artist_tag = tpe2
                        
                        # Date tags: check TDRC, TDOR, TYER
                        tdrc = tags.getall("TDRC")
                        tdor = tags.getall("TDOR")
                        tyer = tags.getall("TYER")
                        date_tag = (tdrc[0].text[0] if tdrc and tdrc[0].text else 
                                    tdor[0].text[0] if tdor and tdor[0].text else 
                                    tyer[0].text[0] if tyer and tyer[0].text else "")
                        
                        tpe1 = meta["artist"]
                        
                        if has_comment:
                            comments = []
                            for k in tags.keys():
                                if k.startswith("COMM"):
                                    for text in tags[k].text:
                                        if text.strip():
                                            comments.append(text.strip())
                            comment_str = ", ".join(f'"{c}"' for c in comments) if comments else "present"
                            has_dirty = True
                            dirty_reason = f"Contains comment/promo tags (Comment value: {comment_str})."
                        elif not is_valid_album_artist(tpe2, tpe1):
                            has_dirty = True
                            dirty_reason = f"Album Artist tag ('{tpe2}') is missing or doesn't match Track Artist ('{tpe1}')."
                    elif ext_lower == "flac":
                        from mutagen.flac import FLAC
                        audio = FLAC(f_path)
                        has_comment = "comment" in audio
                        album_artist = audio.get("albumartist", [""])[0] or audio.get("album artist", [""])[0]
                        album_artist_tag = album_artist
                        date_tag = audio.get("date", [""])[0] or audio.get("year", [""])[0]
                        artist = meta["artist"]
                        
                        if has_comment:
                            comments = audio.get("comment", [])
                            comment_str = ", ".join(f'"{c}"' for c in comments) if comments else "present"
                            has_dirty = True
                            dirty_reason = f"Contains comment/promo tags (Comment value: {comment_str})."
                        elif not is_valid_album_artist(album_artist, artist):
                            has_dirty = True
                            dirty_reason = f"Album Artist tag ('{album_artist}') is missing or doesn't match Track Artist ('{artist}')."
                    elif ext_lower in ["m4a", "mp4"]:
                        from mutagen.mp4 import MP4
                        audio = MP4(f_path)
                        has_comment = "\xa9cmt" in audio
                        a_art = audio.get("aART", [""])[0] if audio.get("aART") else ""
                        album_artist_tag = a_art
                        date_tag = audio.get("\xa9day", [""])[0]
                        art = meta["artist"]
                        
                        if has_comment:
                            comments = audio.get("\xa9cmt", [])
                            comment_str = ", ".join(f'"{c}"' for c in comments) if comments else "present"
                            has_dirty = True
                            dirty_reason = f"Contains comment/promo tags (Comment value: {comment_str})."
                        elif not is_valid_album_artist(a_art, art):
                            has_dirty = True
                            dirty_reason = f"Album Artist tag ('{a_art}') is missing or doesn't match Track Artist ('{art}')."
                except Exception:
                    pass

                track_info = {
                    "path": str(f_path),
                    "artist": meta["artist"],
                    "title": meta["title"],
                    "album": meta["album"],
                    "size": f_path.stat().st_size,
                    "bitrate": bitrate,
                    "bit_depth": bit_depth,
                    "sample_rate": sample_rate,
                    "ext": ext_lower,
                    "has_dirty_meta": has_dirty,
                    "dirty_meta_reason": dirty_reason,
                    "album_artist_tag": album_artist_tag,
                    "date_tag": date_tag
                }
                all_tracks.append(track_info)

    # Folder-level Tag Consistency checks (Release Dates, Album Artists, Album Titles)
    from collections import defaultdict
    folder_groups = defaultdict(list)
    for track in all_tracks:
        folder_groups[str(Path(track["path"]).parent)].append(track)
        
    for folder, tracks in folder_groups.items():
        if len(tracks) > 1:
            albums = set(str(t["album"]).strip() for t in tracks if t["album"])
            album_artists = set(str(t["album_artist_tag"]).strip() for t in tracks if t["album_artist_tag"])
            dates = set(str(t["date_tag"]).strip() for t in tracks if t["date_tag"])
            
            reasons = []
            if len(albums) > 1:
                reasons.append(f"Inconsistent Album titles: {', '.join(f'\"{a}\"' for a in albums)}")
            if len(album_artists) > 1:
                reasons.append(f"Inconsistent Album Artists: {', '.join(f'\"{aa}\"' for aa in album_artists)}")
            if len(dates) > 1:
                reasons.append(f"Inconsistent Release Dates: {', '.join(f'\"{d}\"' for d in dates)}")
                
            if reasons:
                for track in tracks:
                    track["has_dirty_meta"] = True
                    if track["dirty_meta_reason"]:
                        track["dirty_meta_reason"] += " | " + " & ".join(reasons)
                    else:
                        track["dirty_meta_reason"] = " & ".join(reasons)

    # Missing Cover Art check (limited to library albums only)
    for alb in album_folders_list:
        folder_path = alb["folder_path"]
        if folder_path in silenced:
            continue
            
        audio_files = []
        for root, _, files in os.walk(folder_path):
            for f in files:
                if f.lower().endswith((".mp3", ".flac", ".m4a")):
                    audio_files.append(Path(root) / f)
                    
        if not alb["has_cover"]:
            embedded = False
            if audio_files:
                sample_track = audio_files[0]
                try:
                    ext = sample_track.suffix.lower().strip(".")
                    if ext == "mp3":
                        from mutagen.mp3 import MP3
                        audio = MP3(sample_track)
                        embedded = any(k.startswith("APIC:") for k in audio.keys())
                    elif ext == "flac":
                        from mutagen.flac import FLAC
                        audio = FLAC(sample_track)
                        embedded = len(audio.pictures) > 0
                    elif ext in ["m4a", "mp4"]:
                        from mutagen.mp4 import MP4
                        audio = MP4(sample_track)
                        embedded = "covr" in audio
                except Exception:
                    pass
                    
            if not embedded:
                issues.append({
                    "id": f"missing_cover_{hash(folder_path)}",
                    "type": "missing_cover",
                    "severity": "low",
                    "title": f"Missing Cover Art: '{alb['artist']} - {alb['album']}'",
                    "description": f"No cover.jpg or embedded artwork found in folder: {folder_path}",
                    "target_path": folder_path,
                    "actions": [
                        {"name": "Fetch Cover Art", "action": "fetch_cover", "label": "Download cover from Deezer"}
                    ]
                })

        # Misfiled Tracks check: check if tracks inside album folder are tagged with a different album name
        wrong_album_groups = {}
        for f_path in audio_files:
            meta = read_basic_tags(f_path)
            tagged_album = meta["album"]
            if not tagged_album:
                continue
            
            def norm(s: str) -> str:
                return re.sub(r'[^\w]', '', s).lower()
                
            norm_tagged = norm(tagged_album)
            norm_folder = norm(alb["album"])
            
            if norm_tagged == norm_folder:
                continue
                
            # Ignore differences due to artist prefix/suffix (e.g. "Drake - ICEMAN" folder vs "ICEMAN" tag)
            clean_folder = alb["album"]
            artist_name = alb["artist"]
            if clean_folder.lower().startswith(artist_name.lower()):
                clean_folder = clean_folder[len(artist_name):].strip(" -_")
            elif clean_folder.lower().endswith(artist_name.lower()):
                clean_folder = clean_folder[:-len(artist_name)].strip(" -_")
                
            if norm(clean_folder) == norm_tagged:
                continue
                
            if norm_folder.startswith(norm_tagged) or norm_folder.endswith(norm_tagged):
                continue
                
            if tagged_album not in wrong_album_groups:
                wrong_album_groups[tagged_album] = []
            wrong_album_groups[tagged_album].append(str(f_path))
                
        for tagged_alb_name, file_paths in wrong_album_groups.items():
            valid_paths = [p for p in file_paths if p not in silenced]
            if not valid_paths:
                continue
            issues.append({
                "id": f"misfiled_{hash(folder_path + tagged_alb_name)}",
                "type": "misfiled_tracks",
                "severity": "high",
                "title": f"Misfiled Tracks: '{alb['artist']} - {tagged_alb_name}' in '{alb['album']}' folder",
                "description": f"Found {len(valid_paths)} tracks inside '{folder_path}' tagged as '{tagged_alb_name}' instead of '{alb['album']}'.",
                "target_path": folder_path,
                "actions": [
                    {
                        "name": "Move to Correct Folder",
                        "action": "move_misfiled_tracks",
                        "label": f"Move to '{tagged_alb_name}' folder",
                        "params": {
                            "file_paths": json.dumps(valid_paths),
                            "correct_album": tagged_alb_name,
                            "artist": alb["artist"]
                        }
                    }
                ]
            })

    # 1. Duplicate Tracks (incorporating library-vs-playlist link matching)
    track_groups = {}
    for track in all_tracks:
        norm_key = (
            re.sub(r'[^\w]', '', track["artist"]).lower(),
            re.sub(r'[^\w]', '', track["title"]).lower()
        )
        if norm_key not in track_groups:
            track_groups[norm_key] = []
        track_groups[norm_key].append(track)
        
    for norm_key, group in track_groups.items():
        if len(group) > 1:
            library_files = []
            playlist_files = []
            for t in group:
                t_path = Path(t["path"]).resolve()
                if t_path == playlists_dir or playlists_dir in t_path.parents:
                    playlist_files.append(t)
                else:
                    library_files.append(t)
                    
            if library_files:
                # Case A: Master track exists in the main library
                sorted_lib = sorted(
                    library_files,
                    key=lambda x: (
                        1 if x["ext"] == "flac" else 0,
                        x["bitrate"],
                        x["size"]
                    ),
                    reverse=True
                )
                primary = sorted_lib[0]
                
                # Check library-level duplicates
                for dup in sorted_lib[1:]:
                    try:
                        if os.path.samefile(dup["path"], primary["path"]):
                            continue
                    except Exception:
                        pass
                    issues.append({
                        "id": f"dup_track_{hash(dup['path'])}",
                        "type": "duplicate_track",
                        "severity": "medium",
                        "title": f"Duplicate Song (Library): '{primary['artist']} - {primary['title']}'",
                        "description": f"Duplicate file found in library.\nPrimary: {primary['path']} ({primary['ext'].upper()})\nDuplicate: {dup['path']} ({dup['ext'].upper()})",
                        "target_path": dup["path"],
                        "actions": [
                            {"name": "Delete Duplicate File", "action": "delete_file", "label": "Keep Best Quality & Delete this file"}
                        ]
                    })
                    
                # Check playlist duplicates to replace with hardlink
                for dup in playlist_files:
                    try:
                        if os.path.samefile(dup["path"], primary["path"]):
                            continue
                    except Exception:
                        pass
                    issues.append({
                        "id": f"dup_playlist_{hash(dup['path'])}",
                        "type": "duplicate_track",
                        "severity": "medium",
                        "title": f"Duplicate Song (Playlist): '{primary['artist']} - {primary['title']}'",
                        "description": f"Playlist file is a duplicate of library master track.\nLibrary Master: {primary['path']} ({primary['ext'].upper()})\nPlaylist track: {dup['path']} ({dup['ext'].upper()})",
                        "target_path": dup["path"],
                        "actions": [
                            {
                                "name": "Replace with Hardlink", 
                                "action": "replace_with_hardlink", 
                                "label": "Replace playlist file with hardlink to library track",
                                "params": {"master_path": primary["path"]}
                            }
                        ]
                    })
            else:
                # Case B: Tracks only exist in playlists (no main library track)
                sorted_play = sorted(
                    playlist_files,
                    key=lambda x: (
                        1 if x["ext"] == "flac" else 0,
                        x["bitrate"],
                        x["size"]
                    ),
                    reverse=True
                )
                primary = sorted_play[0]
                
                dup_play_targets = []
                for dup in sorted_play[1:]:
                    try:
                        if os.path.samefile(dup["path"], primary["path"]):
                            continue
                    except Exception:
                        pass
                    dup_play_targets.append(dup)
                    
                if dup_play_targets:
                    dup_paths = [d["path"] for d in dup_play_targets]
                    issues.append({
                        "id": f"dup_playlist_only_{hash(primary['path'])}",
                        "type": "duplicate_track",
                        "severity": "medium",
                        "title": f"Duplicate Playlist Track: '{primary['artist']} - {primary['title']}'",
                        "description": f"Duplicate track exists across multiple playlists.\nMaster: {primary['path']} ({primary['ext'].upper()})\nDuplicates:\n" + "\n".join(f"- {d['path']}" for d in dup_play_targets),
                        "target_path": primary["path"],
                        "actions": [
                            {
                                "name": "Deduplicate Playlists", 
                                "action": "deduplicate_playlists", 
                                "label": "Store master in explore and hardlink in all playlists",
                                "params": {"dup_paths": dup_paths}
                            }
                        ]
                    })

    # 2. Split Albums (checking word similarity to prevent Guns N' Roses false positives)
    artist_albums = {}
    for alb in album_folders_list:
        folder_path = alb["folder_path"]
        art = alb["artist"].lower()
        if art not in artist_albums:
            artist_albums[art] = []
            
        audio_files = []
        for root, _, files in os.walk(folder_path):
            for f in files:
                if f.lower().endswith((".mp3", ".flac", ".m4a")):
                    audio_files.append(Path(root) / f)
                    
        track_titles = []
        for f_path in audio_files:
            meta = read_basic_tags(f_path)
            track_titles.append(meta["title"])
            
        artist_albums[art].append({
            "folder_path": folder_path,
            "album": alb["album"],
            "artist": alb["artist"],
            "track_titles": track_titles
        })
        
    for art, folders in artist_albums.items():
        if len(folders) > 1:
            for i in range(len(folders)):
                for j in range(i + 1, len(folders)):
                    info_a = folders[i]
                    info_b = folders[j]
                    path_a = info_a["folder_path"]
                    path_b = info_b["folder_path"]
                    
                    if path_a in silenced or path_b in silenced:
                        continue
                        
                    titles_a = set(re.sub(r'[^\w]', '', t).lower() for t in info_a["track_titles"])
                    titles_b = set(re.sub(r'[^\w]', '', t).lower() for t in info_b["track_titles"])
                    
                    overlap = titles_a.intersection(titles_b)
                    
                    # Filter: Significant word overlap in album titles (e.g. Culture vs Culture II matches, Appetite vs Lies does not)
                    words_a = set(w for w in re.findall(r'[a-zA-Z0-9]+', info_a["album"].lower()) if len(w) > 2)
                    words_b = set(w for w in re.findall(r'[a-zA-Z0-9]+', info_b["album"].lower()) if len(w) > 2)
                    word_overlap = words_a.intersection(words_b)
                    
                    if word_overlap and len(overlap) >= 2:
                        issues.append({
                            "id": f"split_album_{hash(path_a + path_b)}",
                            "type": "split_album",
                            "severity": "high",
                            "title": f"Split/Duplicate Album: '{info_a['artist']} - {info_a['album']}'",
                            "description": f"Overlapping tracks detected between folders:\n- {path_a} ({len(info_a['track_titles'])} tracks)\n- {path_b} ({len(info_b['track_titles'])} tracks)",
                            "target_path": path_b,
                            "actions": [
                                {
                                    "name": "Merge Folders", 
                                    "action": "merge_albums", 
                                    "label": f"Merge into {info_a['album']} folder",
                                    "params": {"source_folder": path_b, "dest_folder": path_a}
                                }
                            ]
                        })

    # 4. Missing Metadata
    for track in all_tracks:
        if track["path"] in silenced:
            continue
            
        if not track["artist"] or not track["title"] or track["artist"] == "Unknown Artist" or track["title"] == "Unknown Title":
            issues.append({
                "id": f"missing_meta_{hash(track['path'])}",
                "type": "missing_metadata",
                "severity": "low",
                "title": f"Missing/Incomplete Metadata: '{os.path.basename(track['path'])}'",
                "description": f"Audio file is missing complete Title or Artist tags: {track['path']}",
                "target_path": track["path"],
                "actions": [
                    {"name": "Auto-Tag File", "action": "fix_metadata", "label": "Search Deezer & Auto-Tag"}
                ]
            })

    # 4.5. Dirty Metadata Check (Album Artist mismatch or comments present)
    for track in all_tracks:
        if track["path"] in silenced:
            continue
        if track.get("has_dirty_meta"):
            issues.append({
                "id": f"dirty_meta_{hash(track['path'])}",
                "type": "dirty_metadata",
                "severity": "medium",
                "title": f"Dirty/Incorrect Metadata: '{os.path.basename(track['path'])}'",
                "description": f"Track '{track['title']}' has metadata issues:\n- {track['dirty_meta_reason']}",
                "target_path": track["path"],
                "actions": [
                    {"name": "Clean Tags", "action": "clean_metadata", "label": "Clean & Fix Tags"}
                ]
            })

    # 5. Orphaned Lyrics Check
    audio_stems = {}
    for track in all_tracks:
        p_track = Path(track["path"])
        stem_norm = re.sub(r'[^\w]', '', p_track.stem).lower()
        audio_stems[stem_norm] = p_track

    for root, dirs, files in os.walk(str(music_dir)):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in ["playlists", "navidrome_playlists", "explore"]]
        if ".staging" in root:
            continue
            
        for f in files:
            if f.lower().endswith(".lrc"):
                lrc_path = Path(root) / f
                if str(lrc_path) in silenced:
                    continue
                    
                has_local_audio = False
                for ext in [".mp3", ".flac", ".m4a"]:
                    if (lrc_path.with_suffix(ext)).is_file():
                        has_local_audio = True
                        break
                        
                if not has_local_audio:
                    stem_norm = re.sub(r'[^\w]', '', lrc_path.stem).lower()
                    if stem_norm in audio_stems:
                        target_audio = audio_stems[stem_norm]
                        issues.append({
                            "id": f"orphaned_lrc_{hash(str(lrc_path))}",
                            "type": "orphaned_lyrics",
                            "severity": "medium",
                            "title": f"Orphaned Lyrics File: '{lrc_path.name}'",
                            "description": f"Lyrics file was left behind at:\n- {lrc_path}\nMatching audio track is located at:\n- {target_audio}",
                            "target_path": str(lrc_path),
                            "actions": [
                                {
                                    "name": "Move Lyrics File",
                                    "action": "move_orphaned_lyrics",
                                    "label": "Move lyrics to audio folder",
                                    "params": {"dest_folder": str(target_audio.parent)}
                                }
                            ]
                        })

    return issues

@app.post("/api/maintenance/fix")
async def fix_maintenance(req: FixIssueRequest, request: Request):
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    await db.delete_cache(f"maintenance_{user_id}")
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")
        
    cfg = config_manager.config
    
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(cfg.paths.music_dir).resolve()
    playlists_dir = Path(cfg.paths.navidrome_playlists_dir).resolve()
    if user_row:
        if user_row.get("music_dir"):
            music_dir = Path(user_row["music_dir"]).resolve()
        if user_row.get("playlist_dir"):
            playlists_dir = Path(user_row["playlist_dir"]).resolve()
            
    target_path = Path(req.target_path)
    if not str(target_path.resolve()).startswith(str(music_dir)) and not str(target_path.resolve()).startswith(str(playlists_dir)):
        raise HTTPException(status_code=403, detail="Access denied")
        
    if req.action != "merge_albums" and not os.path.exists(req.target_path):
        raise HTTPException(status_code=404, detail="Target path does not exist.")
        
    if req.action == "delete_file":
        try:
            os.remove(req.target_path)
            parent = target_path.parent
            if parent != music_dir and parent.is_dir() and not os.listdir(str(parent)):
                shutil.rmtree(str(parent))
                
            return {"status": "success", "message": "File deleted successfully."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")
            
    elif req.action == "redownload_acoustid_track":
        try:
            # Delete bad file
            file_path = Path(req.target_path)
            if file_path.exists():
                file_path.unlink()
                # Clean up empty parent folder
                parent = file_path.parent
                if parent != music_dir and parent.is_dir() and not os.listdir(str(parent)):
                    shutil.rmtree(str(parent))
                logger.info(f"Deleted mismatched AcoustID track: {file_path}")
            
            # Clear result from DB
            await db.clear_acoustid_result(str(file_path))
            
            # Trigger download
            params = req.params or {}
            artist = params.get("artist")
            title = params.get("title")
            album = params.get("album")
            if artist and title:
                from backend.app.album_sync import download_single_track_task
                _create_tracked_task(
                    download_single_track_task(
                        artist=artist,
                        title=title,
                        album=album or "",
                        config=cfg,
                        db=db,
                        force=True,
                        user_id=user_id
                    ),
                    task_id=f"track:{artist}:{title}",
                    task_type="track",
                    metadata={"artist": artist, "title": title, "album": album}
                )
                return {"status": "success", "message": f"Deleted mismatched file and queued redownload for '{artist} - {title}'."}
            else:
                return {"status": "success", "message": "Deleted mismatched file."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to process redownload: {e}")
            
    elif req.action == "move_orphaned_lyrics":
        if not req.params or "dest_folder" not in req.params:
            raise HTTPException(status_code=400, detail="Missing dest_folder parameter.")
        try:
            dest_folder = Path(req.params["dest_folder"])
            dest_folder.mkdir(parents=True, exist_ok=True)
            
            dest_file = dest_folder / target_path.name
            if dest_file.exists():
                os.remove(str(dest_file))
            shutil.move(str(target_path), str(dest_file))
            
            parent = target_path.parent
            if parent != music_dir and parent.is_dir() and not os.listdir(str(parent)):
                shutil.rmtree(str(parent))
                
            return {"status": "success", "message": "Lyrics file moved to matching audio folder successfully."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to move orphaned lyrics file: {e}")
            
    elif req.action == "move_misfiled_tracks":
        if not req.params or "file_paths" not in req.params or "correct_album" not in req.params or "artist" not in req.params:
            raise HTTPException(status_code=400, detail="Missing parameters for moving misfiled tracks.")
        try:
            files_to_move = json.loads(req.params["file_paths"])
            correct_album = req.params["correct_album"]
            artist = req.params["artist"]
            
            from backend.app.sync import sanitize_filename
            dest_folder = music_dir / sanitize_filename(artist) / sanitize_filename(correct_album)
            dest_folder.mkdir(parents=True, exist_ok=True)
            
            moved_count = 0
            for f_str in files_to_move:
                f_path = Path(f_str)
                if f_path.exists():
                    dest_file = dest_folder / f_path.name
                    lrc_path = f_path.with_suffix(".lrc")
                    dest_lrc = dest_folder / lrc_path.name
                    
                    if dest_file.exists():
                        from backend.app.sync import get_file_audio_info
                        _, src_bitrate, _, _ = get_file_audio_info(f_path)
                        _, dst_bitrate, _, _ = get_file_audio_info(dest_file)
                        if src_bitrate > dst_bitrate:
                            os.remove(str(dest_file))
                            shutil.move(str(f_path), str(dest_file))
                            if lrc_path.exists():
                                if dest_lrc.exists():
                                    os.remove(str(dest_lrc))
                                shutil.move(str(lrc_path), str(dest_lrc))
                        else:
                            os.remove(str(f_path))
                            if lrc_path.exists():
                                os.remove(str(lrc_path))
                    else:
                        shutil.move(str(f_path), str(dest_file))
                        if lrc_path.exists():
                            if dest_lrc.exists():
                                os.remove(str(dest_lrc))
                            shutil.move(str(lrc_path), str(dest_lrc))
                    moved_count += 1
                    
            if target_path.is_dir() and not os.listdir(str(target_path)):
                shutil.rmtree(str(target_path))
                parent = target_path.parent
                if parent != music_dir and parent.is_dir() and not os.listdir(str(parent)):
                    shutil.rmtree(str(parent))
                    
            # Trigger Navidrome rescan
            await trigger_navidrome_scan_debounced()
                    
            return {"status": "success", "message": f"Successfully moved {moved_count} tracks to '{correct_album}' folder."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to move misfiled tracks: {e}")
            
    elif req.action == "replace_with_hardlink":
        if not req.params or "master_path" not in req.params:
            raise HTTPException(status_code=400, detail="Missing master_path parameter.")
        master = Path(req.params["master_path"])
        if not master.exists():
            raise HTTPException(status_code=404, detail="Master file does not exist.")
        try:
            if target_path.exists():
                os.remove(str(target_path))
            try:
                os.link(str(master), str(target_path))
            except OSError:
                shutil.copy2(str(master), str(target_path))
            return {"status": "success", "message": "Playlist file replaced with hardlink to master track."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to replace with hardlink: {e}")
            
    elif req.action == "deduplicate_playlists":
        if not req.params or "dup_paths" not in req.params:
            raise HTTPException(status_code=400, detail="Missing dup_paths parameter.")
        try:
            master_dir = playlists_dir / "explore"
            master_dir.mkdir(parents=True, exist_ok=True)
            
            meta = read_basic_tags(target_path)
            from backend.app.sync import get_safe_filename
            ext = target_path.suffix.lower().strip(".")
            safe_name = get_safe_filename(meta["artist"], meta["title"], f".{ext}")
            master_file = master_dir / safe_name
            
            if not master_file.exists():
                shutil.move(str(target_path), str(master_file))
            else:
                if target_path.exists():
                    os.remove(str(target_path))
                    
            try:
                os.link(str(master_file), str(target_path))
            except OSError:
                shutil.copy2(str(master_file), str(target_path))
                
            for dp_str in req.params["dup_paths"]:
                dp = Path(dp_str)
                if dp.exists():
                    try:
                        if dp.resolve() != master_file.resolve() and not os.path.samefile(str(dp), str(master_file)):
                            os.remove(str(dp))
                            os.link(str(master_file), str(dp))
                    except OSError:
                        shutil.copy2(str(master_file), str(dp))
                        
            return {"status": "success", "message": "Playlists deduplicated and hardlinked successfully."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to deduplicate playlists: {e}")
            
    elif req.action == "merge_albums":
        if not req.params or "source_folder" not in req.params or "dest_folder" not in req.params:
            raise HTTPException(status_code=400, detail="Missing source_folder or dest_folder parameters.")
            
        src = Path(req.params["source_folder"])
        dst = Path(req.params["dest_folder"])
        
        if not str(src.resolve()).startswith(str(music_dir)) or not str(dst.resolve()).startswith(str(music_dir)):
            raise HTTPException(status_code=403, detail="Access denied. Path must be inside the music directory.")
            
        try:
            for f in os.listdir(str(src)):
                src_file = src / f
                if not src_file.is_file():
                    continue
                dst_file = dst / f
                if dst_file.exists():
                    from backend.app.sync import get_file_audio_info
                    _, src_bitrate, _, _ = get_file_audio_info(src_file)
                    _, dst_bitrate, _, _ = get_file_audio_info(dst_file)
                    if src_bitrate > dst_bitrate:
                        os.remove(str(dst_file))
                        shutil.move(str(src_file), str(dst_file))
                    else:
                        os.remove(str(src_file))
                else:
                    shutil.move(str(src_file), str(dst_file))
                    
            if src.is_dir():
                shutil.rmtree(str(src))
                
            parent = src.parent
            if parent != music_dir and parent.is_dir() and not os.listdir(str(parent)):
                shutil.rmtree(str(parent))
                
            return {"status": "success", "message": "Folders merged successfully."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to merge folders: {e}")
            
    elif req.action == "fix_metadata":
        try:
            from backend.app.clients.deezer import DeezerClient
            dz = DeezerClient(timeout=10)
            
            basename = target_path.stem
            clean_title = re.sub(r'^\d+\s*[-_.]?\s*', '', basename).strip(' -_')
            artist_guess = target_path.parent.parent.name
            
            dz_meta = await dz.get_track_metadata(artist_guess, clean_title)
            if not dz_meta:
                raise HTTPException(status_code=404, detail="No matching metadata found on Deezer.")
                
            artist = dz_meta.get("artist", {}).get("name")
            title = dz_meta.get("title")
            album = dz_meta.get("album", {}).get("title")
            track_num = dz_meta.get("track_position")
            cover_url = dz_meta.get("album", {}).get("cover_xl")
            
            dz_date = None
            dz_album_artist = None
            
            # Resolve joint track artists
            track_id = dz_meta.get("id")
            track_details = await dz.get_track_details(track_id) if track_id else None
            if track_details:
                artist, dz_album_artist = dz.resolve_joint_artists(track_details)
            else:
                artist, dz_album_artist = dz.resolve_joint_artists(dz_meta)
            
            album_id = dz_meta.get("album", {}).get("id")
            if album_id:
                album_meta = await dz.get_album_metadata(album_id)
                if album_meta:
                    dz_date = album_meta.get("release_date")
                    # Unify joint Album Artist (e.g. Drake & 21 Savage)
                    _, dz_album_artist = dz.resolve_joint_artists(album_meta)
            
            cover_bytes = None
            if cover_url:
                cover_bytes = await dz.download_cover_art(cover_url)
                
            from backend.app.sync import embed_metadata
            embed_metadata(
                file_path=str(target_path),
                artist=artist,
                title=title,
                album=album,
                track_num=track_num,
                cover_bytes=cover_bytes,
                album_artist=dz_album_artist,
                date=dz_date
            )
            return {"status": "success", "message": "Metadata fixed successfully using Deezer."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fix metadata: {e}")
            
    elif req.action == "clean_metadata":
        try:
            meta = read_basic_tags(target_path)
            ext = target_path.suffix.lower().strip(".")
            track_num = None
            try:
                if ext == "mp3":
                    from mutagen.easyid3 import EasyID3
                    easy = EasyID3(target_path)
                    tr = easy.get("tracknumber", [""])[0]
                    if "/" in tr:
                        track_num = int(tr.split("/")[0])
                    elif tr:
                        track_num = int(tr)
                elif ext == "flac":
                    from mutagen.flac import FLAC
                    audio = FLAC(target_path)
                    tr = audio.get("tracknumber", [""])[0]
                    if tr:
                        track_num = int(tr)
                elif ext in ["m4a", "mp4"]:
                    from mutagen.mp4 import MP4
                    audio = MP4(target_path)
                    if "trkn" in audio:
                        track_num = audio["trkn"][0][0]
            except Exception:
                pass
                
            cover_bytes = None
            try:
                if ext == "mp3":
                    from mutagen.id3 import ID3
                    tags = ID3(target_path)
                    for k in tags.keys():
                        if k.startswith("APIC:"):
                            cover_bytes = tags[k].data
                            break
                elif ext == "flac":
                    from mutagen.flac import FLAC
                    audio = FLAC(target_path)
                    if audio.pictures:
                        cover_bytes = audio.pictures[0].data
                elif ext in ["m4a", "mp4"]:
                    from mutagen.mp4 import MP4
                    audio = MP4(target_path)
                    if "covr" in audio:
                        cover_bytes = audio["covr"][0]
            except Exception:
                pass
                
            # Get parent folder fallback metadata
            parent_dir = target_path.parent
            sibling_dates = []
            sibling_album_artists = []
            sibling_albums = []
            sibling_discs = []
            for sibling in parent_dir.iterdir():
                if sibling.is_file() and sibling.suffix.lower() in [".mp3", ".flac", ".m4a"]:
                    try:
                        sib_meta = read_basic_tags(sibling)
                        if sib_meta.get("album"):
                            sibling_albums.append(sib_meta["album"])
                        sib_ext = sibling.suffix.lower().strip(".")
                        if sib_ext == "mp3":
                            from mutagen.id3 import ID3
                            sib_tags = ID3(sibling)
                            tpe2 = sib_tags.getall("TPE2")
                            if tpe2 and tpe2[0].text:
                                sibling_album_artists.append(str(tpe2[0].text[0]).strip())
                            tdrc = sib_tags.getall("TDRC")
                            tdor = sib_tags.getall("TDOR")
                            tyer = sib_tags.getall("TYER")
                            sib_date = (tdrc[0].text[0] if tdrc and tdrc[0].text else 
                                        tdor[0].text[0] if tdor and tdor[0].text else 
                                        tyer[0].text[0] if tyer and tyer[0].text else "")
                            if sib_date:
                                sibling_dates.append(str(sib_date).strip())
                            tpos_list = sib_tags.getall("TPOS")
                            if tpos_list and tpos_list[0].text:
                                try:
                                    tpos_val = int(str(tpos_list[0].text[0]).split("/")[0])
                                    sibling_discs.append(tpos_val)
                                except Exception:
                                    pass
                        elif sib_ext == "flac":
                            from mutagen.flac import FLAC
                            sib_audio = FLAC(sibling)
                            sib_aa = sib_audio.get("albumartist", [""])[0] or sib_audio.get("album artist", [""])[0]
                            if sib_aa:
                                sibling_album_artists.append(str(sib_aa).strip())
                            sib_d = sib_audio.get("date", [""])[0] or sib_audio.get("year", [""])[0]
                            if sib_d:
                                sibling_dates.append(str(sib_d).strip())
                            sib_disc = sib_audio.get("discnumber", [""])[0]
                            if sib_disc:
                                sibling_discs.append(int(sib_disc))
                        elif sib_ext in ["m4a", "mp4"]:
                            from mutagen.mp4 import MP4
                            sib_audio = MP4(sibling)
                            sib_aa = sib_audio.get("aART", [""])[0] if sib_audio.get("aART") else ""
                            if sib_aa:
                                sibling_album_artists.append(str(sib_aa).strip())
                            sib_d = sib_audio.get("\xa9day", [""])[0]
                            if sib_d:
                                sibling_dates.append(str(sib_d).strip())
                            if "disk" in sib_audio:
                                sibling_discs.append(sib_audio["disk"][0][0])
                    except Exception:
                        pass
            
            from collections import Counter
            fallback_date = Counter(sibling_dates).most_common(1)[0][0] if sibling_dates else None
            fallback_album_artist = Counter(sibling_album_artists).most_common(1)[0][0] if sibling_album_artists else None
            fallback_album = Counter(sibling_albums).most_common(1)[0][0] if sibling_albums else None
            fallback_disc = Counter(sibling_discs).most_common(1)[0][0] if sibling_discs else 1

            # Retrieve date and joint artist from Deezer to unify album releases
            dz_date = None
            dz_album_artist = None
            dz_artist = meta["artist"]
            dz_track_num = None
            dz_disc_num = 1
            dz_title = None

            clean_search_title = meta["title"]
            if clean_search_title:
                clean_search_title = re.sub(r'[-_][a-f0-9]{6,8}$', '', clean_search_title)
                clean_search_title = clean_search_title.replace("_", " ").strip()

            try:
                from backend.app.clients.deezer import DeezerClient
                dz = DeezerClient(timeout=10)
                dz_meta = await dz.get_track_metadata(meta["artist"], clean_search_title)
                if dz_meta:
                    dz_title = dz_meta.get("title")
                    dz_track_num = dz_meta.get("track_position")
                    dz_disc_num = dz_meta.get("disk_number", 1)
                    track_id = dz_meta.get("id")
                    track_details = await dz.get_track_details(track_id) if track_id else None
                    if track_details:
                        dz_artist, dz_album_artist = dz.resolve_joint_artists(track_details)
                    else:
                        dz_artist, dz_album_artist = dz.resolve_joint_artists(dz_meta)
                        
                    album_id = dz_meta.get("album", {}).get("id")
                    if album_id:
                        album_meta = await dz.get_album_metadata(album_id)
                        if album_meta:
                            dz_date = album_meta.get("release_date")
                            _, dz_album_artist = dz.resolve_joint_artists(album_meta)
            except Exception:
                pass

            final_album = fallback_album or meta["album"]
            final_album_artist = fallback_album_artist or dz_album_artist or target_path.parent.parent.name
            final_date = fallback_date or dz_date
            final_track_num = dz_track_num or track_num
            final_disc_num = dz_disc_num or fallback_disc or 1
            final_title = dz_title or clean_search_title or meta["title"]

            from backend.app.sync import embed_metadata
            embed_metadata(
                file_path=str(target_path),
                artist=dz_artist,
                title=final_title,
                album=final_album,
                track_num=final_track_num,
                cover_bytes=cover_bytes,
                album_artist=final_album_artist,
                date=final_date,
                disc_num=final_disc_num,
                disc_total=1
            )
            
            # Trigger Navidrome rescan
            await trigger_navidrome_scan_debounced()
                    
            return {"status": "success", "message": "Metadata fixed and comments cleaned successfully."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to clean metadata: {e}")
            
    elif req.action == "fetch_cover":
        try:
            from backend.app.clients.deezer import DeezerClient
            dz = DeezerClient(timeout=10)
            
            album_name = target_path.name
            artist_name = target_path.parent.name
            
            url = f"https://api.deezer.com/search/album?q={urllib.parse.quote(f'{artist_name} {album_name}')}&limit=1"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json().get("data", [])
                
            if not data:
                raise HTTPException(status_code=404, detail="Album not found on Deezer.")
                
            cover_url = data[0].get("cover_xl")
            if not cover_url:
                raise HTTPException(status_code=404, detail="Cover art not found on Deezer.")
                
            cover_bytes = await dz.download_cover_art(cover_url)
            if not cover_bytes:
                raise HTTPException(status_code=500, detail="Failed to download cover art.")
                
            cover_file = target_path / "cover.jpg"
            with open(cover_file, "wb") as f:
                f.write(cover_bytes)
                
            from backend.app.sync import embed_metadata
            for root, _, files in os.walk(str(target_path)):
                for fl in files:
                    if fl.lower().endswith((".mp3", ".flac", ".m4a")):
                        f_path = Path(root) / fl
                        meta = read_basic_tags(f_path)
                        embed_metadata(
                            file_path=str(f_path),
                            artist=meta["artist"],
                            title=meta["title"],
                            album=meta["album"],
                            cover_bytes=cover_bytes
                        )
                        
            return {"status": "success", "message": "Cover art fetched and embedded successfully."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch cover art: {e}")
            
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported fix action: {req.action}")

async def verify_user_access_to_path(user_id: str, path_str: str):
    user_row = await db.get_user_by_id(user_id)
    cfg = config_manager.config
    if not cfg:
        raise HTTPException(status_code=503, detail="App not configured.")
    music_dir = Path(cfg.paths.music_dir).resolve()
    playlists_dir = Path(cfg.paths.navidrome_playlists_dir).resolve()
    if user_row:
        if user_row.get("music_dir"):
            music_dir = Path(user_row["music_dir"]).resolve()
        if user_row.get("playlist_dir"):
            playlists_dir = Path(user_row["playlist_dir"]).resolve()
            
    resolved = Path(path_str).resolve()
    if not str(resolved).startswith(str(music_dir)) and not str(resolved).startswith(str(playlists_dir)):
        raise HTTPException(status_code=403, detail="Access denied")

@app.post("/api/maintenance/ignore")
async def ignore_maintenance(req: IgnoreIssueRequest, request: Request):
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    await verify_user_access_to_path(user_id, req.target_path)
    await db.delete_cache(f"maintenance_{user_id}")
    try:
        await db.add_silenced_issue(req.issue_type, req.target_path)
        return {"status": "success", "message": "Issue ignored successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/maintenance/unignore")
async def unignore_maintenance(req: IgnoreIssueRequest, request: Request):
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    await verify_user_access_to_path(user_id, req.target_path)
    await db.delete_cache(f"maintenance_{user_id}")
    try:
        await db.delete_silenced_issue(req.target_path)
        return {"status": "success", "message": "Issue unignored successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class PreviewIssueRequest(BaseModel):
    issue_type: str
    target_path: str

@app.post("/api/maintenance/preview")
async def preview_maintenance_fix(req: PreviewIssueRequest, request: Request):
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    await verify_user_access_to_path(user_id, req.target_path)
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")
        
    target_path = Path(req.target_path)
    if not os.path.exists(req.target_path):
        raise HTTPException(status_code=404, detail="Target path does not exist.")
        
    try:
        from backend.app.clients.deezer import DeezerClient
        dz = DeezerClient(timeout=10)
        
        # Read current basic metadata
        meta = read_basic_tags(target_path)
        
        # Get parent folder fallback metadata
        parent_dir = target_path.parent
        sibling_dates = []
        sibling_album_artists = []
        sibling_albums = []
        for sibling in parent_dir.iterdir():
            if sibling.is_file() and sibling.suffix.lower() in [".mp3", ".flac", ".m4a"]:
                try:
                    sib_meta = read_basic_tags(sibling)
                    if sib_meta.get("album"):
                        sibling_albums.append(sib_meta["album"])
                    sib_ext = sibling.suffix.lower().strip(".")
                    if sib_ext == "mp3":
                        from mutagen.id3 import ID3
                        sib_tags = ID3(sibling)
                        tpe2 = sib_tags.getall("TPE2")
                        if tpe2 and tpe2[0].text:
                            sibling_album_artists.append(str(tpe2[0].text[0]).strip())
                        tdrc = sib_tags.getall("TDRC")
                        tdor = sib_tags.getall("TDOR")
                        tyer = sib_tags.getall("TYER")
                        sib_date = (tdrc[0].text[0] if tdrc and tdrc[0].text else 
                                    tdor[0].text[0] if tdor and tdor[0].text else 
                                    tyer[0].text[0] if tyer and tyer[0].text else "")
                        if sib_date:
                            sibling_dates.append(str(sib_date).strip())
                    elif sib_ext == "flac":
                        from mutagen.flac import FLAC
                        sib_audio = FLAC(sibling)
                        sib_aa = sib_audio.get("albumartist", [""])[0] or sib_audio.get("album artist", [""])[0]
                        if sib_aa:
                            sibling_album_artists.append(str(sib_aa).strip())
                        sib_d = sib_audio.get("date", [""])[0] or sib_audio.get("year", [""])[0]
                        if sib_d:
                            sibling_dates.append(str(sib_d).strip())
                    elif sib_ext in ["m4a", "mp4"]:
                        from mutagen.mp4 import MP4
                        sib_audio = MP4(sibling)
                        sib_aa = sib_audio.get("aART", [""])[0] if sib_audio.get("aART") else ""
                        if sib_aa:
                            sibling_album_artists.append(str(sib_aa).strip())
                        sib_d = sib_audio.get("\xa9day", [""])[0]
                        if sib_d:
                            sibling_dates.append(str(sib_d).strip())
                except Exception:
                    pass
        
        from collections import Counter
        fallback_date = Counter(sibling_dates).most_common(1)[0][0] if sibling_dates else None
        fallback_album_artist = Counter(sibling_album_artists).most_common(1)[0][0] if sibling_album_artists else None
        fallback_album = Counter(sibling_albums).most_common(1)[0][0] if sibling_albums else None

        # Build guess title/artist
        basename = target_path.stem
        clean_title = re.sub(r'^\d+\s*[-_.]?\s*', '', basename).strip(' -_')
        clean_title = re.sub(r'[-_][a-f0-9]{6,8}$', '', clean_title)
        clean_title = clean_title.replace("_", " ").strip()
        
        artist_guess = target_path.parent.parent.name
        if artist_guess.lower() in ["music", "staging", "explore", "playlists"]:
            artist_guess = meta["artist"] or "Unknown"
            
        clean_search_title = meta["title"]
        if clean_search_title:
            clean_search_title = re.sub(r'[-_][a-f0-9]{6,8}$', '', clean_search_title)
            clean_search_title = clean_search_title.replace("_", " ").strip()
            
        dz_meta = await dz.get_track_metadata(artist_guess, clean_title)
        if not dz_meta:
            dz_meta = await dz.get_track_metadata(meta["artist"], clean_search_title or clean_title)
            
        dz_artist = meta["artist"]
        dz_album_artist = None
        dz_album = meta["album"]
        dz_date = None
        dz_title = None
        
        if dz_meta:
            dz_title = dz_meta.get("title")
            dz_artist = dz_meta.get("artist", {}).get("name")
            dz_album = dz_meta.get("album", {}).get("title")
            
            track_id = dz_meta.get("id")
            track_details = await dz.get_track_details(track_id) if track_id else None
            if track_details:
                dz_artist, dz_album_artist = dz.resolve_joint_artists(track_details)
            else:
                dz_artist, dz_album_artist = dz.resolve_joint_artists(dz_meta)
                
            album_id = dz_meta.get("album", {}).get("id")
            if album_id:
                album_meta = await dz.get_album_metadata(album_id)
                if album_meta:
                    dz_date = album_meta.get("release_date")
                    _, dz_album_artist = dz.resolve_joint_artists(album_meta)

        final_album = fallback_album or dz_album or meta["album"]
        final_album_artist = fallback_album_artist or dz_album_artist or artist_guess
        final_date = fallback_date or dz_date
        final_title = dz_title or clean_search_title or clean_title or meta["title"]

        return {
            "proposed": {
                "Artist": dz_artist,
                "Album Artist": final_album_artist,
                "Album": final_album,
                "Title": final_title,
                "Release Date": final_date or "Unknown"
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load preview: {str(e)}")

class SaveLyricsRequest(BaseModel):
    filepath: str
    lyrics_text: str

@app.get("/api/lyrics/missing")
async def get_missing_lyrics(request: Request):
    if not config_manager.config:
        return []
        
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    cached = await db.get_cache(f"missing_lyrics_{user_id}")
    if cached is not None:
        return cached
        
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(config_manager.config.paths.music_dir)
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"])
        
    if not music_dir.exists():
        return []
        
    # Get metadata cache from database
    metadata_cache = await db.get_all_file_metadata()
    new_cache_entries = []
    
    _, missing = await asyncio.to_thread(
        _perform_library_scan_sync, music_dir, metadata_cache, new_cache_entries
    )
    
    # Save any new metadata cache entries to database
    if new_cache_entries:
        await db.save_file_metadata_batch(new_cache_entries)
        
    await db.set_cache(f"missing_lyrics_{user_id}", missing)
    return missing

@app.get("/api/lyrics/file")
async def get_lyrics_file_endpoint(filepath: str, request: Request):
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")
        
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(config_manager.config.paths.music_dir).resolve()
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"]).resolve()
        
    target_path = Path(filepath)
    if not str(target_path.resolve()).startswith(str(music_dir)):
        raise HTTPException(status_code=403, detail="Access denied")
        
    lrc_path = target_path.with_suffix(".lrc")
    txt_path = target_path.with_suffix(".txt")
    
    lyrics_text = ""
    lyrics_type = "missing"
    
    if lrc_path.exists():
        try:
            with open(lrc_path, "r", encoding="utf-8") as f:
                lyrics_text = f.read()
            lyrics_type = "synced"
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read synced lyrics: {e}")
    elif txt_path.exists():
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                lyrics_text = f.read()
            lyrics_type = "plain"
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read plain lyrics: {e}")
            
    return {
        "filepath": filepath,
        "lyrics_text": lyrics_text,
        "type": lyrics_type
    }

@app.get("/api/lyrics/search")
async def search_lyrics_endpoint(artist: str, title: str):
    from backend.app.clients.lrclib import LrcLibClient
    lrclib_client = LrcLibClient()
    results = await lrclib_client.search_lyrics(artist, title)
    return results

@app.post("/api/lyrics/save")
async def save_lyrics_endpoint(req: SaveLyricsRequest, request: Request):
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")
        
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(config_manager.config.paths.music_dir).resolve()
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"]).resolve()
        
    target_path = Path(req.filepath)
    if not str(target_path.resolve()).startswith(str(music_dir)):
        raise HTTPException(status_code=403, detail="Access denied")
        
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
        
    try:
        lrc_path = target_path.with_suffix(".lrc")
        with open(lrc_path, "w", encoding="utf-8") as f:
            f.write(req.lyrics_text)
            
        await trigger_navidrome_scan_debounced()
        await db.delete_cache(f"missing_lyrics_{user_id}")
                
        return {"status": "success", "message": f"Lyrics saved to {lrc_path.name}."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class StageLyricsRequest(BaseModel):
    artist: str
    title: str
    lyrics_text: str

@app.post("/api/lyrics/stage")
async def stage_lyrics_endpoint(req: StageLyricsRequest):
    import json
    data_dir = Path("/data")
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_file = data_dir / "staged_lyrics.json"
    data = {}
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
            
    key = f"{re.sub(r'[^\w]', '', req.artist).lower()}_{re.sub(r'[^\w]', '', req.title).lower()}"
    data[key] = req.lyrics_text
    
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {"status": "success", "message": "Lyrics staged successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Album Art Manager API ───────────────────────────────────────────────────

def get_file_checks_cache_path() -> Path:
    db_dir = os.path.dirname(db.db_path) if getattr(db, 'db_path', None) else "."
    return Path(db_dir) / "file_checks_cache.json"

@app.get("/api/library/missing-art")
async def get_missing_art_endpoint(request: Request):
    if not config_manager.config:
        return []
    
    cache_path = get_file_checks_cache_path()
    if cache_path.exists():
        try:
            import json
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("missing_art", [])
        except Exception as e:
            logger.warning(f"Failed to read missing art cache: {e}")
    return []

@app.post("/api/library/missing-art/scan")
async def scan_missing_art_endpoint(request: Request):
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")
        
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(config_manager.config.paths.music_dir)
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"])
        
    if not music_dir.exists():
        return []
        
    import json
    albums = await asyncio.to_thread(get_all_album_folders, music_dir)
    missing = []
    for alb in albums:
        if not alb.get("has_cover"):
            folders_raw = alb.get("folder_path")
            try:
                folders = json.loads(folders_raw)
            except Exception:
                folders = [folders_raw]
            
            missing.append({
                "artist_name": alb.get("artist_name"),
                "album_name": alb.get("album_name"),
                "folder_path": folders[0] if folders else folders_raw,
                "folders": folders,
                "bitrate": alb.get("bitrate"),
                "format": alb.get("format")
            })
            
    cache_path = get_file_checks_cache_path()
    data = {}
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    from datetime import datetime
    data["missing_art"] = missing
    data["missing_art_last_scan"] = datetime.utcnow().isoformat()
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"Failed to write missing art cache: {e}")
        
    return missing

@app.get("/api/library/art/search")
async def search_art_endpoint(artist: str, album: str):
    from backend.app.clients.itunes import ITunesClient
    from backend.app.clients.deezer import DeezerClient
    
    itunes = ITunesClient()
    deezer = DeezerClient()
    
    itunes_results = await itunes.search_album_artwork(artist, album)
    
    deezer_results = []
    try:
        track_meta = await deezer.get_track_metadata(artist, album)
        if track_meta:
            alb_meta = track_meta.get("album", {})
            cover_url = alb_meta.get("cover_xl") or alb_meta.get("cover_big") or alb_meta.get("cover")
            if cover_url:
                deezer_results.append({
                    "artist": artist,
                    "album": album,
                    "url": cover_url,
                    "thumbnail": alb_meta.get("cover_medium") or cover_url,
                    "resolution": "1000x1000" if "cover_xl" in alb_meta else "Unknown",
                    "source": "Deezer",
                    "release_date": ""
                })
    except Exception:
        pass
            
    return deezer_results + itunes_results

class SaveArtRequest(BaseModel):
    folder_path: str
    url: str
    embed: bool = False

@app.post("/api/library/art/save")
async def save_art_endpoint(req: SaveArtRequest, request: Request):
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")
        
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(config_manager.config.paths.music_dir).resolve()
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"]).resolve()
        
    target_path = Path(req.folder_path).resolve()
    if not str(target_path).startswith(str(music_dir)):
        raise HTTPException(status_code=403, detail="Access denied")
        
    if not target_path.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")
        
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(req.url)
            resp.raise_for_status()
            img_data = resp.content
            
        cover_jpg = target_path / "cover.jpg"
        folder_jpg = target_path / "folder.jpg"
        
        with open(cover_jpg, "wb") as f:
            f.write(img_data)
        with open(folder_jpg, "wb") as f:
            f.write(img_data)
            
        if req.embed:
            import glob
            audio_files = []
            for ext in ["*.mp3", "*.flac", "*.m4a"]:
                audio_files.extend(glob.glob(str(target_path / ext)))
                audio_files.extend(glob.glob(str(target_path / "**" / ext), recursive=True))
                
            for filepath in audio_files:
                f_path = Path(filepath)
                ext = f_path.suffix.lower().strip(".")
                try:
                    if ext == "mp3":
                        from mutagen.mp3 import MP3
                        from mutagen.id3 import ID3, APIC, error
                        audio = MP3(f_path, ID3=ID3)
                        try:
                            audio.add_tags()
                        except error:
                            pass
                        audio.tags.add(
                            APIC(
                                encoding=3,
                                mime='image/jpeg',
                                type=3,
                                desc=u'Cover',
                                data=img_data
                            )
                        )
                        audio.save()
                    elif ext == "flac":
                        from mutagen.flac import FLAC, Picture
                        audio = FLAC(f_path)
                        picture = Picture()
                        picture.data = img_data
                        picture.type = 3
                        picture.mime = "image/jpeg"
                        picture.desc = "Front Cover"
                        audio.clear_pictures()
                        audio.add_picture(picture)
                        audio.save()
                    elif ext in ["m4a", "mp4"]:
                        from mutagen.mp4 import MP4, MP4Cover
                        audio = MP4(f_path)
                        audio.tags["covr"] = [MP4Cover(img_data, imageformat=MP4Cover.FORMAT_JPEG)]
                        audio.save()
                except Exception as e:
                    logger.warning(f"Failed to embed art in {filepath}: {e}")
                    
        await trigger_navidrome_scan_debounced()
        return {"status": "success", "message": "Cover art applied successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Metadata Tag Editor API ─────────────────────────────────────────────────

class EditTagsRequest(BaseModel):
    filepath: str
    artist: Optional[str] = None
    album: Optional[str] = None
    title: Optional[str] = None
    track: Optional[str] = None
    year: Optional[str] = None
    genre: Optional[str] = None

@app.get("/api/library/track/tags")
async def get_track_tags_endpoint(filepath: str, request: Request):
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")
        
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(config_manager.config.paths.music_dir).resolve()
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"]).resolve()
        
    target_path = Path(filepath).resolve()
    if not str(target_path).startswith(str(music_dir)):
        raise HTTPException(status_code=403, detail="Access denied")
        
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
        
    try:
        meta = read_basic_tags(target_path)
        return {
            "filepath": filepath,
            "artist": meta.get("artist", ""),
            "album": meta.get("album", ""),
            "title": meta.get("title", ""),
            "track": meta.get("track", ""),
            "year": meta.get("year", ""),
            "genre": meta.get("genre", "")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/library/track/tags")
async def edit_track_tags_endpoint(req: EditTagsRequest, request: Request):
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")
        
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(config_manager.config.paths.music_dir).resolve()
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"]).resolve()
        
    target_path = Path(req.filepath).resolve()
    if not str(target_path).startswith(str(music_dir)):
        raise HTTPException(status_code=403, detail="Access denied")
        
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
        
    try:
        ext = target_path.suffix.lower().strip(".")
        if ext == "mp3":
            from mutagen.id3 import ID3, TIT2, TPE1, TALB, TRCK, TDRC, TCON, error
            try:
                audio = ID3(target_path)
            except error:
                audio = ID3()
            if req.title is not None:
                audio["TIT2"] = TIT2(encoding=3, text=req.title)
            if req.artist is not None:
                audio["TPE1"] = TPE1(encoding=3, text=req.artist)
            if req.album is not None:
                audio["TALB"] = TALB(encoding=3, text=req.album)
            if req.track is not None:
                audio["TRCK"] = TRCK(encoding=3, text=req.track)
            if req.year is not None:
                audio["TDRC"] = TDRC(encoding=3, text=req.year)
            if req.genre is not None:
                audio["TCON"] = TCON(encoding=3, text=req.genre)
            audio.save(target_path)
        elif ext == "flac":
            from mutagen.flac import FLAC
            audio = FLAC(target_path)
            if req.title is not None:
                audio["title"] = req.title
            if req.artist is not None:
                audio["artist"] = req.artist
            if req.album is not None:
                audio["album"] = req.album
            if req.track is not None:
                audio["tracknumber"] = req.track
            if req.year is not None:
                audio["date"] = req.year
            if req.genre is not None:
                audio["genre"] = req.genre
            audio.save()
        elif ext in ["m4a", "mp4"]:
            from mutagen.mp4 import MP4
            audio = MP4(target_path)
            if req.title is not None:
                audio["\xa9nam"] = req.title
            if req.artist is not None:
                audio["\xa9ART"] = req.artist
            if req.album is not None:
                audio["\xa9alb"] = req.album
            if req.track is not None:
                try:
                    t_num = int(req.track.split("/")[0])
                    audio["trkn"] = [(t_num, 0)]
                except Exception:
                    pass
            if req.year is not None:
                audio["\xa9day"] = req.year
            if req.genre is not None:
                audio["\xa9gen"] = req.genre
            audio.save()
            
        await trigger_navidrome_scan_debounced()
        return {"status": "success", "message": "Tags updated successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Custom Renaming & Organize API ──────────────────────────────────────────

def format_rename_pattern(pattern: str, meta: dict, ext: str) -> str:
    import re
    artist = sanitize_filename(meta.get("artist") or "Unknown Artist")
    album = sanitize_filename(meta.get("album") or "Unknown Album")
    title = sanitize_filename(meta.get("title") or "Unknown Title")
    genre = sanitize_filename(meta.get("genre") or "Unknown Genre")
    
    year_raw = str(meta.get("year") or "")
    year_match = re.search(r'\d{4}', year_raw)
    year = year_match.group(0) if year_match else "0000"
    
    track_val = meta.get("track")
    if track_val is None:
        track_val = meta.get("track_num")
    track_raw = str(track_val) if track_val is not None else "0"
    track_str = track_raw.split("/")[0].strip()
    try:
        track_num = int(track_str)
    except Exception:
        track_num = 0
        
    formatted = pattern
    formatted = formatted.replace("{Artist}", artist)
    formatted = formatted.replace("{Album}", album)
    formatted = formatted.replace("{Title}", title)
    formatted = formatted.replace("{Genre}", genre)
    formatted = formatted.replace("{Year}", year)
    
    formatted = formatted.replace("{Track:2}", f"{track_num:02d}")
    formatted = formatted.replace("{Track}", str(track_num))
    
    return formatted.strip("/") + ext

def _organize_preview_sync(music_dir: Path, pattern: str) -> list:
    preview = []
    for root, dirs, files in os.walk(str(music_dir)):
        for f in files:
            if f.lower().endswith((".mp3", ".flac", ".m4a")):
                src_path = Path(root) / f
                try:
                    meta = read_basic_tags(src_path)
                    ext = src_path.suffix.lower()
                    new_rel = format_rename_pattern(pattern, meta, ext)
                    dst_path = music_dir / new_rel
                    
                    if src_path.resolve() != dst_path.resolve():
                        preview.append({
                            "src": str(src_path),
                            "dst": str(dst_path),
                            "rel_dst": new_rel
                        })
                except Exception:
                    pass
    return preview

@app.get("/api/library/organize/preview")
async def organize_preview_endpoint(request: Request):
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")
        
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(config_manager.config.paths.music_dir).resolve()
    pattern = "{Artist}/{Year} - {Album}/{Track:2} - {Title}"
    if user_row:
        if user_row.get("music_dir"):
            music_dir = Path(user_row["music_dir"]).resolve()
        if user_row.get("renaming_pattern"):
            pattern = user_row["renaming_pattern"]
            
    if not music_dir.exists():
        return []
        
    preview = await asyncio.to_thread(_organize_preview_sync, music_dir, pattern)
    return preview

def _organize_execute_sync(music_dir: Path, pattern: str) -> tuple[int, list]:
    import shutil
    moved_count = 0
    errors = []
    for root, dirs, files in os.walk(str(music_dir)):
        for f in files:
            if f.lower().endswith((".mp3", ".flac", ".m4a")):
                src_path = Path(root) / f
                try:
                    meta = read_basic_tags(src_path)
                    ext = src_path.suffix.lower()
                    new_rel = format_rename_pattern(pattern, meta, ext)
                    dst_path = music_dir / new_rel
                    
                    if src_path.resolve() != dst_path.resolve():
                        dst_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src_path), str(dst_path))
                        moved_count += 1
                        
                        parent = src_path.parent
                        while parent != music_dir and parent.is_dir() and not os.listdir(str(parent)):
                            os.rmdir(str(parent))
                            parent = parent.parent
                except Exception as e:
                    errors.append({"file": str(src_path), "error": str(e)})
    return moved_count, errors

@app.post("/api/library/organize")
async def organize_execute_endpoint(request: Request):
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")
        
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(config_manager.config.paths.music_dir).resolve()
    pattern = "{Artist}/{Year} - {Album}/{Track:2} - {Title}"
    if user_row:
        if user_row.get("music_dir"):
            music_dir = Path(user_row["music_dir"]).resolve()
        if user_row.get("renaming_pattern"):
            pattern = user_row["renaming_pattern"]
            
    if not music_dir.exists():
        raise HTTPException(status_code=404, detail="Library directory does not exist")
        
    moved_count, errors = await asyncio.to_thread(_organize_execute_sync, music_dir, pattern)
                    
    await trigger_navidrome_scan_debounced()
    return {"status": "success", "moved_count": moved_count, "errors": errors}

# ── Duplicates Cleaner API ──────────────────────────────────────────────────

def _get_duplicates_sync(music_dir: Path) -> list:
    tracks_map = {}
    for root, dirs, files in os.walk(str(music_dir)):
        for f in files:
            if f.lower().endswith((".mp3", ".flac", ".m4a")):
                filepath = Path(root) / f
                try:
                    meta = read_basic_tags(filepath)
                    artist = meta.get("artist") or "Unknown"
                    title = meta.get("title") or "Unknown"
                    album = meta.get("album") or "Unknown"
                    
                    import re
                    norm_artist = re.sub(r'[^\w]', '', artist).lower()
                    norm_title = re.sub(r'[^\w]', '', title).lower()
                    key = f"{norm_artist}|{norm_title}"
                    
                    if not key or key == "|":
                        continue
                        
                    stat = filepath.stat()
                    size = stat.st_size
                    
                    bitrate = 0
                    ext = filepath.suffix.lower().strip(".")
                    if ext == "mp3":
                        from mutagen.mp3 import MP3
                        audio = MP3(filepath)
                        bitrate = int(audio.info.bitrate / 1000) if audio.info.bitrate else 0
                    elif ext == "flac":
                        from mutagen.flac import FLAC
                        audio = FLAC(filepath)
                        bitrate = int(audio.info.bitrate / 1000) if audio.info.bitrate else 0
                    elif ext in ["m4a", "mp4"]:
                        from mutagen.mp4 import MP4
                        audio = MP4(filepath)
                        bitrate = int(audio.info.bitrate / 1000) if audio.info.bitrate else 0
                        
                    track_info = {
                        "path": str(filepath),
                        "artist": artist,
                        "title": title,
                        "album": album,
                        "size": size,
                        "bitrate": bitrate,
                        "format": ext.upper()
                    }
                    
                    if key not in tracks_map:
                        tracks_map[key] = []
                    tracks_map[key].append(track_info)
                except Exception:
                    pass
                    
    duplicates_groups = []
    for key, tracks in tracks_map.items():
        if len(tracks) > 1:
            tracks.sort(key=lambda t: (1 if t["format"] == "FLAC" else 0, t["bitrate"], t["size"]), reverse=True)
            duplicates_groups.append({
                "key": key,
                "artist": tracks[0]["artist"],
                "title": tracks[0]["title"],
                "best_track": tracks[0],
                "tracks": tracks
            })
            
    return duplicates_groups

@app.get("/api/library/duplicates")
async def get_duplicates_endpoint(request: Request):
    if not config_manager.config:
        return []
    
    cache_path = get_file_checks_cache_path()
    if cache_path.exists():
        try:
            import json
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("duplicates", [])
        except Exception as e:
            logger.warning(f"Failed to read duplicates cache: {e}")
    return []

@app.post("/api/library/duplicates/scan")
async def scan_duplicates_endpoint(request: Request):
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")
        
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(config_manager.config.paths.music_dir).resolve()
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"]).resolve()
        
    if not music_dir.exists():
        return []
        
    duplicates_groups = await asyncio.to_thread(_get_duplicates_sync, music_dir)
    
    cache_path = get_file_checks_cache_path()
    data = {}
    if cache_path.exists():
        try:
            import json
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data["duplicates"] = duplicates_groups
    from datetime import datetime
    data["duplicates_last_scan"] = datetime.utcnow().isoformat()
    try:
        import json
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"Failed to write duplicates cache: {e}")
        
    return duplicates_groups

class ResolveDuplicatesRequest(BaseModel):
    paths_to_delete: List[str]

def _resolve_duplicates_sync(paths_to_delete: list[str], music_dir: Path) -> tuple[int, list]:
    deleted_count = 0
    errors = []
    for filepath_str in paths_to_delete:
        filepath = Path(filepath_str).resolve()
        if not str(filepath).startswith(str(music_dir)):
            errors.append({"path": filepath_str, "error": "Access denied"})
            continue
            
        if not filepath.exists():
            errors.append({"path": filepath_str, "error": "File not found"})
            continue
            
        try:
            filepath.unlink()
            deleted_count += 1
            
            parent = filepath.parent
            while parent != music_dir and parent.is_dir() and not os.listdir(str(parent)):
                os.rmdir(str(parent))
                parent = parent.parent
        except Exception as e:
            errors.append({"path": filepath_str, "error": str(e)})
    return deleted_count, errors

@app.post("/api/library/duplicates/resolve")
async def resolve_duplicates_endpoint(req: ResolveDuplicatesRequest, request: Request):
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")
        
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(config_manager.config.paths.music_dir).resolve()
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"]).resolve()
        
    deleted_count, errors = await asyncio.to_thread(_resolve_duplicates_sync, req.paths_to_delete, music_dir)
            
    await trigger_navidrome_scan_debounced()
    return {"status": "success", "deleted_count": deleted_count, "errors": errors}

# ── Listening Statistics & Analytics API ────────────────────────────────────

def _count_library_tracks_sync(music_dir: Path) -> int:
    total_tracks_count = 0
    if music_dir.exists():
        for root, dirs, files in os.walk(str(music_dir)):
            for f in files:
                if f.lower().endswith((".mp3", ".flac", ".m4a")):
                    total_tracks_count += 1
    return total_tracks_count

@app.get("/api/stats/summary")
async def get_stats_summary_endpoint(request: Request):
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")
        
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    user_cfg = await db.get_user_config(user_id)
    lb_username = user_cfg.get("lb_username") if user_cfg else ""
    
    from backend.app.clients.navidrome import NavidromeClient
    nav_client = NavidromeClient(
        url=config_manager.config.navidrome.url,
        username=config_manager.config.navidrome.username,
        password=config_manager.config.navidrome.password,
        timeout=15
    )
    
    history = await nav_client.get_history()
    top_albums = await nav_client.get_top_albums()
    
    top_artists = {}
    top_tracks = {}
    heatmap = {}
    weekday_heatmap = {}
    
    for entry in history:
        artist = entry.get("artist") or "Unknown"
        title = entry.get("title") or "Unknown"
        top_artists[artist] = top_artists.get(artist, 0) + 1
        
        track_key = f"{artist} - {title}"
        top_tracks[track_key] = top_tracks.get(track_key, 0) + 1
        
        play_time = entry.get("playTime")
        if play_time:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(play_time.replace("Z", "+00:00"))
                hour_str = str(dt.hour)
                heatmap[hour_str] = heatmap.get(hour_str, 0) + 1
                
                day_str = str(dt.weekday())
                weekday_heatmap[day_str] = weekday_heatmap.get(day_str, 0) + 1
            except Exception:
                pass
                
    sorted_artists = [{"artist": k, "count": v} for k, v in sorted(top_artists.items(), key=lambda x: x[1], reverse=True)[:10]]
    sorted_tracks = [{"track": k, "count": v} for k, v in sorted(top_tracks.items(), key=lambda x: x[1], reverse=True)[:10]]
    
    lb_stats = {}
    if lb_username:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"https://api.listenbrainz.org/1/stats/user/{lb_username}/artists?count=10")
                if resp.status_code == 200:
                    lb_stats["artists"] = resp.json().get("payload", {}).get("artists", [])
                
                resp2 = await client.get(f"https://api.listenbrainz.org/1/stats/user/{lb_username}/releases?count=10")
                if resp2.status_code == 200:
                    lb_stats["releases"] = resp2.json().get("payload", {}).get("releases", [])
        except Exception as e:
            logger.warning(f"Failed to fetch ListenBrainz stats: {e}")
    total_tracks_count = 0
    listened_count = 0
    try:
        user_row = await db.get_user_by_id(user_id)
        music_dir = Path(config_manager.config.paths.music_dir).resolve()
        if user_row and user_row.get("music_dir"):
            music_dir = Path(user_row["music_dir"]).resolve()
        
        total_tracks_count = await asyncio.to_thread(_count_library_tracks_sync, music_dir)
                        
        unique_played = len(top_tracks)
        listened_count = min(unique_played, total_tracks_count)
    except Exception:
        pass
        
    return {
        "navidrome_history": {
            "top_artists": sorted_artists,
            "top_tracks": sorted_tracks,
            "top_albums": top_albums,
            "heatmap": heatmap,
            "weekday_heatmap": weekday_heatmap,
        },
        "listenbrainz": lb_stats,
        "library_stats": {
            "total_tracks": total_tracks_count,
            "listened_tracks": listened_count,
            "discovery_rate": round(listened_count / total_tracks_count * 100, 2) if total_tracks_count > 0 else 0.0
        }
    }

# ── Local Audio Track Streaming API ─────────────────────────────────────────

@app.get("/api/library/tracks/stream")
async def stream_track_endpoint(filepath: str, request: Request):
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")
        
    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]
    
    user_row = await db.get_user_by_id(user_id)
    music_dir = Path(config_manager.config.paths.music_dir).resolve()
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"]).resolve()
        
    target_path = Path(filepath).resolve()
    if not str(target_path).startswith(str(music_dir)):
        raise HTTPException(status_code=403, detail="Access denied")
        
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
        
    from fastapi.responses import FileResponse
    return FileResponse(target_path)

@app.get("/healthz")
async def healthz():
    """Basic health check query."""
    try:
        # Check database read accessibility
        async with db.get_db() as conn:
            await conn.execute("SELECT 1")
        return {"status": "healthy"}
    except Exception as e:
        logger.error(f"Healthcheck failed: {e}")
        raise HTTPException(status_code=500, detail=f"Unhealthy: {e}")

# ==============================================================================
# NAMING CONVENTION CHECK & MASS RENAME
# ==============================================================================

def _build_expected_filename(
    artist: str, album: str, year: str, track: int, title: str,
    ext: str, file_pattern: str, folder_pattern: str
) -> tuple:
    """
    Build the expected (relative_folder, filename) for a track given the
    configured naming schema.
    Supported tokens: {artist}, {album}, {year}, {track:02d}, {title}
    """
    import re

    def _safe(s: str) -> str:
        """Sanitize a string for use in a filename/folder name."""
        s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', str(s)).strip(' .')
        return s or "Unknown"

    safe_artist = _safe(artist)
    safe_album = _safe(album)
    safe_year = _safe(year) if year else "0000"
    safe_title = _safe(title)
    track_int = int(track) if track else 0

    folder = folder_pattern.format(
        artist=safe_artist, album=safe_album, year=safe_year,
        track=track_int, title=safe_title
    )
    filename = file_pattern.format(
        artist=safe_artist, album=safe_album, year=safe_year,
        track=track_int, title=safe_title
    )
    return folder, f"{filename}{ext}"


@app.get("/api/library/naming/scan")
async def scan_naming_conventions(request: Request):
    """
    Scan library files and return a list of files that don't match the
    configured naming schema.
    """
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")

    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]

    cfg = config_manager.config
    music_dir = Path(cfg.paths.music_dir)
    user_row = await db.get_user_by_id(user_id)
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"])

    if not music_dir.exists():
        return {"mismatches": [], "total_scanned": 0}

    fn_cfg = cfg.filename
    if not fn_cfg.enabled:
        return {"mismatches": [], "total_scanned": 0, "message": "Naming convention checks are disabled."}

    pattern = ""
    if user_row and user_row.get("renaming_pattern"):
        pattern = user_row["renaming_pattern"]
    if not pattern:
        pattern = f"{fn_cfg.folder_pattern}/{fn_cfg.file_pattern}"

    metadata_cache = await db.get_all_file_metadata()

    def _scan_sync():
        mismatches = []
        total = 0
        import os as _os
        for root, dirs, files in _os.walk(str(music_dir)):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if not f.lower().endswith((".mp3", ".flac", ".m4a")):
                    continue
                total += 1
                f_path = Path(root) / f
                ext = f_path.suffix.lower()
                try:
                    meta = read_file_metadata_with_cache(f_path, metadata_cache, [])
                    new_rel = format_rename_pattern(pattern, meta, ext)
                    
                    try:
                        rel = f_path.relative_to(music_dir)
                    except ValueError:
                        rel = f_path
                        
                    expected_rel = Path(new_rel)
                    exp_folder = str(expected_rel.parent) if str(expected_rel.parent) != "." else ""
                    exp_filename = expected_rel.name
                    
                    if str(rel) != str(expected_rel):
                        mismatches.append({
                            "current_path": str(f_path),
                            "current_relative": str(rel),
                            "expected_folder": exp_folder,
                            "expected_filename": exp_filename,
                            "expected_relative": str(expected_rel),
                            "artist": meta.get("artist", ""),
                            "album": meta.get("album", ""),
                            "title": meta.get("title", ""),
                            "track_num": meta.get("track_num", 0),
                        })
                except Exception as e:
                    logger.debug(f"Naming scan error for {f_path}: {e}")
        return mismatches, total

    mismatches, total = await asyncio.to_thread(_scan_sync)
    return {"mismatches": mismatches, "total_scanned": total}


class MassRenameRequest(BaseModel):
    paths: Optional[list] = None  # If None, rename all mismatched files
    dry_run: bool = False


@app.post("/api/library/naming/rename")
async def mass_rename_files(req: MassRenameRequest, request: Request):
    """
    Rename files to match the configured naming schema.
    If req.paths is provided, only rename those files. Otherwise rename all mismatches.
    Pass dry_run=true to preview changes without applying them.
    """
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")

    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]

    cfg = config_manager.config
    music_dir = Path(cfg.paths.music_dir)
    user_row = await db.get_user_by_id(user_id)
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"])

    fn_cfg = cfg.filename
    pattern = ""
    if user_row and user_row.get("renaming_pattern"):
        pattern = user_row["renaming_pattern"]
    if not pattern:
        pattern = f"{fn_cfg.folder_pattern}/{fn_cfg.file_pattern}"

    metadata_cache = await db.get_all_file_metadata()

    def _rename_sync():
        import os as _os
        import shutil as _shutil
        renamed = []
        errors = []

        if req.paths:
            targets = [Path(p) for p in req.paths]
        else:
            all_files = []
            for root, dirs, files in _os.walk(str(music_dir)):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    if f.lower().endswith((".mp3", ".flac", ".m4a")):
                        all_files.append(Path(root) / f)
            targets = all_files

        for f_path in targets:
            ext = f_path.suffix.lower()
            try:
                meta = read_file_metadata_with_cache(f_path, metadata_cache, [])
                new_rel = format_rename_pattern(pattern, meta, ext)
                dest = music_dir / new_rel
                if dest.resolve() == f_path.resolve():
                    continue  # Already correct
                renamed.append({
                    "from": str(f_path),
                    "to": str(dest),
                    "applied": False
                })
                if not req.dry_run:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    _shutil.move(str(f_path), str(dest))
                    renamed[-1]["applied"] = True
                    # Remove empty parent directories
                    try:
                        for parent in f_path.parents:
                            if parent == music_dir or parent == music_dir.parent:
                                break
                            if not any(parent.iterdir()):
                                parent.rmdir()
                    except Exception:
                        pass
            except Exception as e:
                errors.append({"path": str(f_path), "error": str(e)})

        return renamed, errors

    renamed, errors = await asyncio.to_thread(_rename_sync)
    return {
        "renamed": renamed,
        "errors": errors,
        "dry_run": req.dry_run,
        "message": f"{'Preview' if req.dry_run else 'Renamed'} {len(renamed)} files."
    }


# ==============================================================================
# FEATURE ARTIST FIXER  (ft. / feat. in ARTIST tag)
# ==============================================================================

@app.get("/api/library/feat/scan")
async def scan_feat_artists(request: Request):
    """
    Scan library and return tracks where the ARTIST tag contains feat./ft./featuring
    that should be moved to the TITLE tag instead.
    """
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")

    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]

    cfg = config_manager.config
    music_dir = Path(cfg.paths.music_dir)
    user_row = await db.get_user_by_id(user_id)
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"])

    _FEAT_RE = re.compile(
        r'[\(\[]?\s*(?:\b(?:feat|ft|featuring)\.?\s+)(.+?)[\)\]]?\s*$',
        re.IGNORECASE
    )

    def _scan_sync():
        import os as _os
        results = []
        for root, dirs, files in _os.walk(str(music_dir)):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if not f.lower().endswith((".mp3", ".flac", ".m4a")):
                    continue
                f_path = Path(root) / f
                try:
                    meta = read_basic_tags(f_path)
                    artist = meta.get("artist", "")
                    title = meta.get("title", "")
                    if not artist:
                        continue
                    m = _FEAT_RE.search(artist)
                    if not m:
                        continue
                    primary = artist[:m.start()].strip()
                    featured = m.group(1).strip()
                    # Suggest new title if feat not already in title
                    if "feat" not in title.lower() and "ft." not in title.lower():
                        new_title = f"{title} (feat. {featured})"
                    else:
                        new_title = title
                    results.append({
                        "path": str(f_path),
                        "file_path": str(f_path),
                        "current_artist": artist,
                        "current_title": title,
                        "proposed_artist": primary,
                        "proposed_title": new_title,
                        "featured_artist": featured,
                    })
                except Exception:
                    pass
        return results

    affected = await asyncio.to_thread(_scan_sync)
    return {"affected": affected, "count": len(affected)}


class FeatFixRequest(BaseModel):
    paths: Optional[list] = None  # None = fix all affected
    dry_run: bool = False


@app.post("/api/library/feat/fix")
async def fix_feat_artists(req: FeatFixRequest, request: Request):
    """
    Fix feature artist tags: move feat./ft. from ARTIST to TITLE.
    """
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")

    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]

    cfg = config_manager.config
    music_dir = Path(cfg.paths.music_dir)
    user_row = await db.get_user_by_id(user_id)
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"])

    _FEAT_RE = re.compile(
        r'[\(\[]?\s*(?:\b(?:feat|ft|featuring)\.?\s+)(.+?)[\)\]]?\s*$',
        re.IGNORECASE
    )

    def _fix_sync():
        import os as _os
        fixed = []
        errors = []

        if req.paths:
            targets = [Path(p) for p in req.paths if p]
        else:
            targets = []
            for root, dirs, files in _os.walk(str(music_dir)):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    if f.lower().endswith((".mp3", ".flac", ".m4a")):
                        targets.append(Path(root) / f)

        for f_path in targets:
            ext = f_path.suffix.lower()
            try:
                meta = read_basic_tags(f_path)
                artist = meta.get("artist", "")
                title = meta.get("title", "")
                if not artist:
                    continue
                m = _FEAT_RE.search(artist)
                if not m:
                    continue
                primary = artist[:m.start()].strip()
                featured = m.group(1).strip()
                if "feat" not in title.lower() and "ft." not in title.lower():
                    new_title = f"{title} (feat. {featured})"
                else:
                    new_title = title

                change = {
                    "path": str(f_path),
                    "old_artist": artist,
                    "new_artist": primary,
                    "old_title": title,
                    "new_title": new_title,
                    "applied": False
                }
                fixed.append(change)

                if not req.dry_run:
                    if ext == ".mp3":
                        from mutagen.id3 import ID3, TPE1, TPE2, TIT2
                        tags = ID3(f_path)
                        tags.setall("TPE1", [TPE1(encoding=3, text=primary)])
                        tags.setall("TPE2", [TPE2(encoding=3, text=primary)])
                        tags.setall("TIT2", [TIT2(encoding=3, text=new_title)])
                        tags.save(str(f_path), v2_version=3)
                    elif ext in (".flac", ".ogg"):
                        from mutagen.flac import FLAC
                        audio = FLAC(f_path)
                        audio["artist"] = primary
                        audio["albumartist"] = primary
                        audio["title"] = new_title
                        audio.save()
                    elif ext in (".m4a", ".mp4"):
                        from mutagen.mp4 import MP4
                        audio = MP4(f_path)
                        audio["\xa9ART"] = primary
                        audio["aART"] = primary
                        audio["\xa9nam"] = new_title
                        audio.save()
                    change["applied"] = True
            except Exception as e:
                errors.append({"path": str(f_path), "error": str(e)})

        return fixed, errors

    fixed, errors = await asyncio.to_thread(_fix_sync)
    
    if fixed:
        await db.clear_file_metadata_cache()

    return {
        "fixed": fixed,
        "errors": errors,
        "dry_run": req.dry_run,
        "message": f"{'Preview' if req.dry_run else 'Fixed'} {len(fixed)} tracks."
    }


class FixFolderTagsRequest(BaseModel):
    folder_path: str
    target_artist: Optional[str] = None
    target_album: Optional[str] = None

@app.post("/api/library/fix-folder-tags")
async def fix_folder_tags_endpoint(req: FixFolderTagsRequest, request: Request):
    """
    Scans a folder, updates embedded tags (Artist, Album Artist, Album) to match the folder structure
    and triggers a Navidrome scan so Navidrome updates its database.
    """
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")

    from backend.app.auth import get_current_user
    user = await get_current_user(request)

    folder = Path(req.folder_path)
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")

    from backend.app.sync import fix_directory_tags_and_rescan
    count = await fix_directory_tags_and_rescan(
        dir_path=folder,
        target_artist=req.target_artist,
        target_album=req.target_album,
        config=config_manager.config
    )

    return {"status": "success", "updated_files": count}


# ==============================================================================
# MUSICBRAINZ METADATA RE-TAG  (existing library)
# ==============================================================================

class RetageRequest(BaseModel):
    paths: Optional[list] = None   # None = re-tag entire library
    dry_run: bool = False
    update_cover: bool = True


@app.post("/api/library/retag/musicbrainz")
async def retag_library_musicbrainz(req: RetageRequest, request: Request):
    """
    Re-tag audio files using MusicBrainz as the primary metadata source.
    Falls back to Deezer for cover art if MusicBrainz Cover Art Archive has nothing.
    """
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")

    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    user_id = user["id"]

    if "retag_scan" in _active_tasks:
        raise HTTPException(status_code=400, detail="A re-tag operation is already running.")

    cfg = config_manager.config
    music_dir = Path(cfg.paths.music_dir)
    user_row = await db.get_user_by_id(user_id)
    if user_row and user_row.get("music_dir"):
        music_dir = Path(user_row["music_dir"])

    paths = [Path(p) for p in req.paths] if req.paths else None

    async def _retag_task():
        from backend.app.clients.musicbrainz import musicbrainz_client
        from backend.app.clients.deezer import DeezerClient
        from backend.app.sync import embed_metadata

        deezer_client = DeezerClient(timeout=cfg.timeouts.http_seconds)

        if paths:
            targets = paths
        else:
            targets = []
            import os as _os
            for root, dirs, files in _os.walk(str(music_dir)):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    if f.lower().endswith((".mp3", ".flac", ".m4a")):
                        targets.append(Path(root) / f)

        logger.info(f"MusicBrainz re-tag: processing {len(targets)} files (dry_run={req.dry_run})")
        results = []
        errors = []

        for f_path in targets:
            try:
                meta = read_basic_tags(f_path)
                artist = meta.get("artist", "")
                title = meta.get("title", "")
                album = meta.get("album", "")
                if not artist or not title:
                    continue

                # Query MusicBrainz
                mb_meta = await musicbrainz_client.get_track_metadata(artist, title, album)
                if not mb_meta:
                    logger.debug(f"No MusicBrainz match for: {artist} - {title}")
                    results.append({"path": str(f_path), "status": "no_match"})
                    continue

                # Fetch cover art: try MB Cover Art Archive, fall back to Deezer
                cover_bytes = None
                if req.update_cover:
                    if mb_meta.get("release_mbid"):
                        cover_bytes = await musicbrainz_client.get_cover_art(mb_meta["release_mbid"])
                    if not cover_bytes:
                        try:
                            dz_meta = await deezer_client.get_track_metadata(
                                mb_meta.get("artist", artist), mb_meta.get("title", title)
                            )
                            if dz_meta:
                                cover_url = dz_meta.get("album", {}).get("cover_xl")
                                if cover_url:
                                    cover_bytes = await deezer_client.download_cover_art(cover_url)
                        except Exception:
                            pass

                change = {
                    "path": str(f_path),
                    "old_artist": artist,
                    "new_artist": mb_meta["artist"],
                    "old_album_artist": meta.get("album_artist", ""),
                    "new_album_artist": mb_meta["album_artist"],
                    "old_title": title,
                    "new_title": mb_meta["title"],
                    "old_album": album,
                    "new_album": mb_meta["album"],
                    "date": mb_meta.get("date", ""),
                    "applied": False,
                    "status": "pending"
                }
                results.append(change)

                if not req.dry_run:
                    embed_metadata(
                        file_path=str(f_path),
                        artist=mb_meta["artist"],
                        title=mb_meta["title"],
                        album=mb_meta["album"],
                        track_num=mb_meta.get("track_num"),
                        cover_bytes=cover_bytes,
                        album_artist=mb_meta["album_artist"],
                        date=mb_meta.get("date"),
                    )
                    change["applied"] = True
                    change["status"] = "updated"
                    logger.info(f"Re-tagged: {f_path.name}")

            except Exception as e:
                logger.error(f"Error re-tagging {f_path}: {e}")
                errors.append({"path": str(f_path), "error": str(e)})

        # Persist summary to cache
        summary = {
            "updated": len([r for r in results if r.get("status") == "updated"]),
            "no_match": len([r for r in results if r.get("status") == "no_match"]),
            "errors": len(errors),
            "dry_run": req.dry_run,
        }
        await db.set_cache("retag_last_summary", summary)
        logger.info(f"MusicBrainz re-tag complete: {summary}")

    if req.dry_run or req.paths:
        # Run synchronously for small batches / dry runs and return results directly
        # For full library async, use tracked task
        await _retag_task()
        cached_summary = await db.get_cache("retag_last_summary")
        return {"status": "success", "summary": cached_summary}
    else:
        _create_tracked_task(
            _retag_task(),
            task_id="retag_scan",
            task_type="retag_scan",
            metadata={"update_cover": req.update_cover}
        )
        return {"status": "started", "message": "MusicBrainz re-tag task started for entire library."}


@app.get("/api/library/retag/status")
async def get_retag_status():
    """Get the status of the last / running re-tag operation."""
    is_running = "retag_scan" in _active_tasks
    summary = await db.get_cache("retag_last_summary")
    return {"running": is_running, "last_summary": summary}


# ==============================================================================
# ARTIST ALIAS MANAGEMENT
# ==============================================================================

@app.get("/api/admin/artist-aliases")
async def get_artist_aliases(request: Request):
    """Get the current artist alias map from config."""
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")
    aliases = config_manager.config.artist_aliases.aliases
    return {"aliases": aliases}


class UpdateAliasesRequest(BaseModel):
    aliases: Dict[str, str]


@app.post("/api/admin/artist-aliases")
async def update_artist_aliases(req: UpdateAliasesRequest, request: Request):
    """Update the artist alias map in the config file."""
    if not config_manager.config:
        raise HTTPException(status_code=400, detail="App is not configured.")

    from backend.app.auth import get_current_user
    user = await get_current_user(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")

    ok, err = config_manager.save({"artist_aliases": {"aliases": req.aliases}})
    if not ok:
        raise HTTPException(status_code=500, detail=f"Failed to save config: {err}")
    return {"status": "ok", "aliases": req.aliases}


@app.post("/api/admin/artist-aliases/resolve-musicbrainz")
async def resolve_artist_from_musicbrainz(artist_name: str, request: Request):
    """
    Look up an artist name in MusicBrainz and return the canonical name + all aliases.
    Use this to populate the alias map with MusicBrainz canonical names.
    """
    from backend.app.auth import get_current_user
    await get_current_user(request)

    from backend.app.clients.musicbrainz import musicbrainz_client
    canonical = await musicbrainz_client.get_canonical_artist_name(artist_name)
    all_names = await musicbrainz_client.get_artist_all_names(artist_name)
    return {
        "input": artist_name,
        "canonical": canonical,
        "all_names": all_names,
        "suggested_alias": {artist_name: canonical} if canonical != artist_name else {}
    }


# Frontend static serving
# Try absolute /app/frontend/dist (docker path) first, fallback to relative path (local dev)
frontend_dir = "/app/frontend/dist"
if not os.path.exists(frontend_dir):
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend", "dist")


assets_dir = os.path.join(frontend_dir, "assets")
if os.path.exists(assets_dir):
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

@app.get("/{catchall:path}")
async def serve_frontend(catchall: str):
    # Prevent capturing API calls that 404
    if catchall.startswith("api/") or catchall == "healthz":
        return HTMLResponse(content="API Route Not Found", status_code=404)
        
    # Serve static files dynamically (e.g. manifest.webmanifest, sw.js, icon-192.png) if they exist
    clean_path = catchall.lstrip("/")
    file_path = os.path.join(frontend_dir, clean_path)
    if clean_path and os.path.isfile(file_path):
        return FileResponse(file_path)
        
    # SPA fallback
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse("VeryDisco Front-end dashboard is loading. Please run production Vite build.", status_code=200)
