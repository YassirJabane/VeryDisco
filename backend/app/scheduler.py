import asyncio
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from backend.app.config import AppConfig
from backend.app.database import Database
from backend.app.sync import run_sync
from backend.app.logger import get_logger

logger = get_logger()

def _promote_track_sync(explore_candidate_path, user_music_dir, artist, title, album, playlists_dir, config):
    import shutil
    import asyncio
    from pathlib import Path
    from backend.app.sync import resolve_album_dir, get_library_filename, embed_metadata, update_m3u_references
    from backend.app.clients.deezer import deezer_client

    # 1. Fetch full canonical metadata
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        from backend.app.album_sync import fetch_track_metadata_with_fallback
        meta_result = loop.run_until_complete(fetch_track_metadata_with_fallback(deezer_client, artist, title, album))
    except Exception as e:
        logger.warning(f"Metadata fetch during promotion for '{artist} - {title}' failed: {e}")
        meta_result = {}
    finally:
        loop.close()

    dz_title = meta_result.get("title") or title
    dz_artist = meta_result.get("artist") or artist
    dz_album_artist = meta_result.get("album_artist") or artist
    dz_album = meta_result.get("album") or album or f"{title} - Single"
    track_num = meta_result.get("track_num")
    track_total = meta_result.get("track_total")
    disc_num = meta_result.get("disc_num", 1)
    disc_total = meta_result.get("disc_total", 1)
    mbid_album = meta_result.get("mbid_album")
    mbid_recording = meta_result.get("mbid_recording")
    cover_bytes = meta_result.get("cover_bytes")
    dz_date = meta_result.get("date")

    ext = explore_candidate_path.suffix
    dest_folder, safe_artist, safe_album = resolve_album_dir(
        user_music_dir, dz_artist, dz_album, dz_album_artist, disc_num=disc_num, disc_total=disc_total
    )
    safe_audio_name = get_library_filename(dz_artist, safe_album, track_num, dz_title, ext)
    dest_path = dest_folder / safe_audio_name

    dest_folder.mkdir(parents=True, exist_ok=True)
    shutil.move(str(explore_candidate_path), str(dest_path))
    
    explore_lrc = explore_candidate_path.with_suffix(".lrc")
    if explore_lrc.exists():
        shutil.move(str(explore_lrc), str(dest_path.with_suffix(".lrc")))

    # Read lyrics if present
    lyrics_text = None
    dest_lrc = dest_path.with_suffix(".lrc")
    if dest_lrc.exists():
        try:
            with open(dest_lrc, "r", encoding="utf-8") as lf:
                lyrics_text = lf.read()
        except Exception:
            pass

    # Re-tag file with canonical album metadata (is_explore=False)
    embed_metadata(
        file_path=str(dest_path),
        artist=dz_artist,
        title=dz_title,
        album=dz_album,
        track_num=track_num,
        track_total=track_total,
        cover_bytes=cover_bytes,
        lyrics_text=lyrics_text,
        album_artist=dz_album_artist,
        date=dz_date,
        disc_num=disc_num,
        disc_total=disc_total,
        is_explore=False,
        mbid_album=mbid_album,
        mbid_recording=mbid_recording
    )

    try:
        update_m3u_references(Path(playlists_dir), explore_candidate_path.name, dest_path)
    except Exception as e:
        logger.error(f"Failed to update M3U references on promotion: {e}")

    return dest_path

def _check_album_dir_exists_sync(final_dir) -> bool:
    try:
        return final_dir.exists() and final_dir.is_dir() and any(final_dir.iterdir())
    except Exception:
        return False

class SchedulerManager:
    def __init__(self, db: Database):
        self.db = db
        self.scheduler = AsyncIOScheduler()
        self.job_id = "weekly_sync_job"

    async def check_and_run_sync(self, config: AppConfig, active_sources: Optional[list[str]] = None):
        from backend.app.clients.listenbrainz import ListenBrainzClient
        import os, json
        from datetime import datetime, timedelta
        import backend.app.sync as sync_module
        
        # Resolve state file location based on the database path in self.db
        db_dir = os.path.dirname(self.db.db_path) if self.db.db_path else "/data"
        state_file = os.path.join(db_dir or ".", "app_state.json")
        
        state = {}
        if os.path.exists(state_file):
            try:
                with open(state_file, "r") as f:
                    state = json.load(f)
            except Exception:
                pass
                
        last_mbids = state.get("last_mbids", {})
        last_hashes = state.get("last_hashes", {})
        # Safe migration if old format last_mbid is found
        if "last_mbid" in state and isinstance(state["last_mbid"], str):
            state.pop("last_mbid")

        users = await self.db.list_users()
        all_to_sync = [] # List of tuples: (user_id, username, token, source, new_mbid, track_hash, cache_key)
        
        if not users:
            # Fallback to global config sync
            sources = active_sources if active_sources is not None else config.listenbrainz.active_playlists
            if config.listenbrainz.username:
                for source in sources:
                    try:
                        lb_client = ListenBrainzClient(
                            username=config.listenbrainz.username,
                            playlist_source=source,
                            token=config.listenbrainz.token,
                            timeout=config.timeouts.http_seconds
                        )
                        current_mbid = await lb_client.resolve_playlist_mbid()
                        tracks = await lb_client.get_playlist_tracks(current_mbid)
                        
                        import hashlib
                        track_keys = [f"{t.get('artist', '')}:{t.get('title', '')}:{t.get('album', '')}" for t in tracks]
                        track_hash = hashlib.md5("|".join(track_keys).encode("utf-8")).hexdigest()
                        
                        cache_key = f"global:{source}"
                        old_hash = last_hashes.get(cache_key, "")
                        old_mbid = last_mbids.get(cache_key, "")
                        logger.info(f"Checking global playlist '{source}': MBID={current_mbid}, Tracks={len(tracks)}, Hash={track_hash}, OldHash={old_hash}")
                        if track_hash != old_hash or current_mbid != old_mbid or not old_hash:
                            logger.info(f"New playlist content detected for {source} (global): {current_mbid}")
                            all_to_sync.append((None, config.listenbrainz.username, config.listenbrainz.token, source, current_mbid, track_hash, cache_key))
                    except Exception as e:
                        err_msg = str(e) or type(e).__name__
                        logger.error(f"Error checking playlist for {source} (global): {err_msg}. Running sync anyway.")
                        cache_key = f"global:{source}"
                        all_to_sync.append((None, config.listenbrainz.username, config.listenbrainz.token, source, "", "", cache_key))
        else:
            for user in users:
                user_id = user["id"]
                user_cfg = await self.db.get_user_config(user_id)
                if not user_cfg:
                    continue
                    
                # Check feature toggle
                user_features = user_cfg.get("enabled_features", {})
                if not user_features.get("listenbrainz_sync", True):
                    logger.info(f"ListenBrainz sync is disabled for user '{user['username']}'. Skipping.")
                    continue
                    
                lb_username = user_cfg.get("lb_username")
                lb_token = user_cfg.get("lb_token") or ""
                
                if not lb_username:
                    continue
                    
                user_playlists = user_cfg.get("active_playlists") or []
                if isinstance(user_playlists, str):
                    try:
                        user_playlists = json.loads(user_playlists)
                    except Exception:
                        user_playlists = []
                
                if not user_playlists and config.listenbrainz.active_playlists:
                    user_playlists = config.listenbrainz.active_playlists
                
                sources = [s for s in user_playlists if active_sources is None or s in active_sources]
                for source in sources:
                    try:
                        lb_client = ListenBrainzClient(
                            username=lb_username,
                            playlist_source=source,
                            token=lb_token,
                            timeout=config.timeouts.http_seconds
                        )
                        current_mbid = await lb_client.resolve_playlist_mbid()
                        tracks = await lb_client.get_playlist_tracks(current_mbid)
                        
                        import hashlib
                        track_keys = [f"{t.get('artist', '')}:{t.get('title', '')}:{t.get('album', '')}" for t in tracks]
                        track_hash = hashlib.md5("|".join(track_keys).encode("utf-8")).hexdigest()
                        
                        cache_key = f"{user_id}:{source}"
                        old_hash = last_hashes.get(cache_key, "")
                        old_mbid = last_mbids.get(cache_key, "")
                        logger.info(f"Checking playlist '{source}' (user: {lb_username}): MBID={current_mbid}, Tracks={len(tracks)}, Hash={track_hash}, OldHash={old_hash}")
                        if track_hash != old_hash or current_mbid != old_mbid or not old_hash:
                            logger.info(f"New playlist content detected for {source} (user: {lb_username}): {current_mbid}")
                            all_to_sync.append((user_id, lb_username, lb_token, source, current_mbid, track_hash, cache_key))
                    except ValueError as e:
                        logger.warning(f"Skipping playlist {source} for user {lb_username} because it was not found: {e}")
                    except Exception as e:
                        err_msg = str(e) or type(e).__name__
                        logger.error(f"Error checking playlist for {source} (user: {lb_username}): {err_msg}. Running sync anyway.")
                        cache_key = f"{user_id}:{source}"
                        all_to_sync.append((user_id, lb_username, lb_token, source, "", "", cache_key))

        # Collect if anyone has active playlists
        has_any_active = False
        if config.listenbrainz.active_playlists:
            has_any_active = True
        else:
            for u in users:
                u_cfg = await self.db.get_user_config(u["id"])
                if u_cfg and any(p for p in u_cfg.get("active_playlists", [])):
                    has_any_active = True
                    break

        if not all_to_sync:
            if not has_any_active:
                logger.info("No active playlists configured for sync. Sync skipped.")
                return
            logger.info("No playlists require synchronization (either none configured or contents unchanged). Delaying sync check by 6 hours.")
            job_name = "sync"
            if active_sources:
                job_name = "_".join(active_sources)
            retry_job_id = f"retry_{job_name}"
            
            if self.scheduler.get_job(retry_job_id):
                self.scheduler.remove_job(retry_job_id)
                
            self.scheduler.add_job(
                self.check_and_run_sync,
                'date',
                run_date=datetime.now() + timedelta(hours=6),
                args=[config, active_sources],
                id=retry_job_id,
                replace_existing=True
            )
            return

        # Execute sync for all updated playlists
        for user_id, lb_username, lb_token, source, new_mbid, new_hash, cache_key in all_to_sync:
            if new_mbid:
                last_mbids[cache_key] = new_mbid
            if new_hash:
                last_hashes[cache_key] = new_hash
                
            state["last_mbids"] = last_mbids
            state["last_hashes"] = last_hashes
            try:
                with open(state_file, "w") as f:
                    json.dump(state, f)
            except Exception:
                pass
                
            logger.info(f"Starting sync run for: {source} (user: {lb_username})")
            from backend.app.main import _create_tracked_task
            task = _create_tracked_task(
                sync_module.run_sync(self.db, config, playlist_source=source, user_id=user_id),
                task_id=f"sync:{source}",
                task_type="sync",
                metadata={"source": source, "user_id": user_id}
            )
            try:
                await task
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Sync run failed for {source} (user: {lb_username}): {e}")

    def start(self, config: AppConfig):
        """Starts the scheduler daemon and schedules the sync job."""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("APScheduler async daemon started.")
        
        self.update_schedule(config)
        
        # If run_on_startup is True, run the sync in the background
        if config.schedule.run_on_startup:
            logger.info("schedule.run_on_startup is set to true. Dispatching sync check immediately...")
            asyncio.create_task(self.check_and_run_sync(config))

    def shutdown(self):
        """Clean shutdown of APScheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("APScheduler async daemon stopped.")

    def update_schedule(self, config: AppConfig):
        """Updates the cron schedules for daily and weekly jobs."""
        for job_id in ["daily_sync_job", "weekly_sync_job", "navidrome_starred_sync_job"]:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
                logger.info(f"Cancelled existing scheduled sync job: {job_id}")

        # Parse daily_time
        daily_hour, daily_minute = 4, 0
        if getattr(config.schedule, "daily_time", None):
            try:
                parts = config.schedule.daily_time.split(":")
                daily_hour = int(parts[0])
                daily_minute = int(parts[1])
            except Exception:
                logger.warning(f"Failed to parse daily_time '{config.schedule.daily_time}', using default 04:00")

        # Parse weekly_time and weekly_day
        weekly_hour, weekly_minute = 4, 0
        weekly_day = getattr(config.schedule, "weekly_day", "tue") or "tue"
        if getattr(config.schedule, "weekly_time", None):
            try:
                parts = config.schedule.weekly_time.split(":")
                weekly_hour = int(parts[0])
                weekly_minute = int(parts[1])
            except Exception:
                logger.warning(f"Failed to parse weekly_time '{config.schedule.weekly_time}', using default 04:00")

        # Parse file_checks_time and file_checks_day
        file_checks_hour, file_checks_minute = 4, 0
        file_checks_day = getattr(config.schedule, "file_checks_day", "sun") or "sun"
        if getattr(config.schedule, "file_checks_time", None):
            try:
                parts = config.schedule.file_checks_time.split(":")
                file_checks_hour = int(parts[0])
                file_checks_minute = int(parts[1])
            except Exception:
                logger.warning(f"Failed to parse file_checks_time '{config.schedule.file_checks_time}', using default 04:00")

        # Schedule Daily Job (daily-jams)
        try:
            trigger = CronTrigger(hour=daily_hour, minute=daily_minute)
            self.scheduler.add_job(
                self.check_and_run_sync,
                trigger=trigger,
                args=[config, ["daily-jams"]],
                id="daily_sync_job",
                replace_existing=True
            )
            logger.info(f"Daily sync job (daily-jams) successfully scheduled for {daily_hour:02d}:{daily_minute:02d} every day.")
        except Exception as e:
            logger.error(f"Error scheduling daily sync job: {e}")

        # Schedule Weekly Job (weekly-exploration and weekly-jams)
        try:
            trigger = CronTrigger(day_of_week=weekly_day, hour=weekly_hour, minute=weekly_minute)
            self.scheduler.add_job(
                self.check_and_run_sync,
                trigger=trigger,
                args=[config, ["weekly-exploration", "weekly-jams"]],
                id="weekly_sync_job",
                replace_existing=True
            )
            logger.info(f"Weekly sync job (weekly-exploration, weekly-jams) successfully scheduled for {weekly_hour:02d}:{weekly_minute:02d} on {weekly_day.upper()}s.")
        except Exception as e:
            logger.error(f"Error scheduling weekly sync job: {e}")

        # Schedule Navidrome Starred Sync Job (every 5 minutes)
        if config.navidrome.url and config.navidrome.username and config.navidrome.password:
            try:
                self.scheduler.add_job(
                    self.sync_navidrome_starred,
                    trigger='interval',
                    minutes=5,
                    args=[config],
                    id="navidrome_starred_sync_job",
                    replace_existing=True
                )
                logger.info("Navidrome Starred synchronization job scheduled (every 5 minutes).")
            except Exception as e:
                logger.error(f"Error scheduling Navidrome Starred sync job: {e}")

        # Schedule Automated File Checks Job
        try:
            async def run_file_checks():
                from backend.app.main import scan_missing_art_endpoint, scan_duplicates_endpoint, trigger_acoustid_scan
                logger.info("Running automated file checks (Missing Art, Duplicates, AcoustID)...")
                try:
                    await scan_missing_art_endpoint(None)
                except Exception as e:
                    logger.error(f"Automated missing art scan failed: {e}")
                
                try:
                    await scan_duplicates_endpoint(None)
                except Exception as e:
                    logger.error(f"Automated duplicates scan failed: {e}")
                    
                try:
                    await trigger_acoustid_scan(batch_size=50, request=None)
                except Exception as e:
                    logger.error(f"Automated AcoustID scan failed: {e}")

            self.scheduler.add_job(
                run_file_checks,
                trigger=CronTrigger(day_of_week=file_checks_day, hour=file_checks_hour, minute=file_checks_minute),
                id="file_checks_job",
                replace_existing=True
            )
            logger.info(f"Automated File Checks job scheduled ({file_checks_day.upper()}s at {file_checks_hour:02d}:{file_checks_minute:02d}).")
        except Exception as e:
            logger.error(f"Error scheduling file checks job: {e}")

    def get_next_run_time(self) -> Optional[str]:
        """Returns the next scheduled run timestamp across all active sync jobs in ISO format or None."""
        next_times = []
        for job_id in ["daily_sync_job", "weekly_sync_job"]:
            job = self.scheduler.get_job(job_id)
            if job and job.next_run_time:
                next_times.append(job.next_run_time)
        if next_times:
            return min(next_times).isoformat()
        return None

    async def sync_navidrome_starred(self, config: AppConfig, user_id: Optional[str] = None):
        """Fetches starred tracks from Navidrome, submits Love to ListenBrainz, and triggers album syncs."""
        if not config.navidrome.url:
            return

        import json
        users_to_sync = []
        if user_id:
            user_row = await self.db.get_user_by_id(user_id)
            if user_row:
                users_to_sync.append(user_row)
        else:
            async with self.db.get_db() as conn:
                async with conn.execute("SELECT * FROM users") as cursor:
                    rows = await cursor.fetchall()
                    users_to_sync = [dict(r) for r in rows]

        for u in users_to_sync:
            uid = u["id"]
            username = u["username"]
            sub_token = u.get("subsonic_token")
            sub_salt = u.get("subsonic_salt")

            nd_username = username
            nd_password = None
            if not sub_token or not sub_salt:
                if username == config.navidrome.username:
                    nd_username = config.navidrome.username
                    nd_password = config.navidrome.password
                else:
                    logger.warning(
                        f"Skipping Navidrome Starred sync for user '{username}': "
                        "Subsonic credentials (token/salt) not found. "
                        "Please log out and log back in to regenerate them."
                    )
                    continue

            user_music_dir = config.paths.music_dir
            user_playlists_dir = config.paths.navidrome_playlists_dir
            if u.get("music_dir"):
                user_music_dir = u["music_dir"]
            if u.get("playlist_dir"):
                user_playlists_dir = u["playlist_dir"]

            lb_username = config.listenbrainz.username
            lb_token = config.listenbrainz.token
            active_playlists = config.listenbrainz.active_playlists or ["weekly-exploration"]

            user_cfg = await self.db.get_user_config(uid)
            if user_cfg:
                # Check feature toggle
                user_features = user_cfg.get("enabled_features", {})
                if not user_features.get("starred_sync", True):
                    logger.debug(f"Starred sync is disabled for user '{username}'. Skipping.")
                    continue

                if user_cfg.get("lb_username"):
                    lb_username = user_cfg["lb_username"]
                if user_cfg.get("lb_token"):
                    lb_token = user_cfg["lb_token"]
                active_playlists = user_cfg.get("active_playlists") or []
                if isinstance(active_playlists, str):
                    try:
                        active_playlists = json.loads(active_playlists)
                    except Exception:
                        active_playlists = []

            from backend.app.clients.navidrome import NavidromeClient
            from backend.app.clients.listenbrainz import ListenBrainzClient
            from backend.app.album_sync import download_album_task
            from backend.app.main import _create_tracked_task
            from pathlib import Path

            nd_client = NavidromeClient(
                url=config.navidrome.url,
                username=nd_username,
                password=nd_password,
                token=sub_token,
                salt=sub_salt
            )

            try:
                starred_tracks = await nd_client.get_starred_tracks()
            except Exception as e:
                logger.error(f"Error fetching starred tracks from Navidrome for user '{username}': {e}")
                continue

            if not starred_tracks:
                logger.info(f"No starred tracks found in Navidrome for user '{username}'.")
                continue

            lb_client = None
            if lb_token:
                fallback_source = active_playlists[0] if active_playlists else "weekly-exploration"
                lb_client = ListenBrainzClient(
                    username=lb_username,
                    playlist_source=fallback_source,
                    token=lb_token
                )
            else:
                logger.info(f"ListenBrainz integration disabled or token missing for user '{username}' — skipping LB submit but continuing with promote/download.")

            for track in starred_tracks:
                track_id = track["id"]
                artist = track["artist"]
                title = track["title"]
                album = track["album"]
                track_mbid = track.get("mbid")

                if not track_id or not artist or not title:
                    continue

                processed = await self.db.is_starred_track_processed(track_id, user_id=uid)
                if processed:
                    continue

                logger.info(f"Detected newly starred Navidrome track for user {username}: '{artist} - {title}' (ID: {track_id})")

                # 1. Sync Love to ListenBrainz
                if lb_client:
                    try:
                        success = await lb_client.submit_feedback(artist, title, 1, mbid=track_mbid)
                        if success:
                            logger.info(f"Submitted Love to ListenBrainz for user {username}: '{artist} - {title}'")
                        else:
                            logger.warning(f"LB feedback failed for user {username}: '{artist} - {title}' — continuing with promote+download.")
                    except Exception as e:
                        logger.error(f"Error submitting Love feedback for starred track: {e} — continuing.")

                # 2. Promote from explore folder to main library if needed
                promoted = False
                try:
                    from backend.app.sync import find_existing_track_file, sanitize_filename, get_safe_filename
                    from backend.app.sync import relocate_and_tag_download
                    import shutil

                    explore_dir = Path(user_playlists_dir) / "explore"

                    if explore_dir.exists():
                        for ext in [".mp3", ".flac", ".m4a"]:
                            explore_candidate = explore_dir / get_safe_filename(artist, title, ext)
                            if explore_candidate.exists():
                                logger.info(f"Starred track '{artist} - {title}' found in explore for user {username}. Promoting to library...")
                                safe_artist = sanitize_filename(artist)
                                safe_album = sanitize_filename(album) if album else "Unknown Album"
                                dest_folder = Path(user_music_dir) / safe_artist / safe_album
                                dest_path = dest_folder / explore_candidate.name
                                try:
                                    dest_path = await asyncio.to_thread(_promote_track_sync, explore_candidate, user_music_dir, artist, title, album, user_playlists_dir, config)
                                    logger.info(f"Promoted '{artist} - {title}' from explore to library for user {username}: {dest_path}")
                                    promoted = True
                                    if nd_client:
                                        await nd_client.trigger_rescan()
                                except Exception as move_err:
                                    logger.error(f"Failed to promote '{artist} - {title}' from explore to library for user {username}: {move_err}")
                                break
                except Exception as e:
                    logger.error(f"Error during explore promotion for starred track '{artist} - {title}' for user {username}: {e}")
                    promoted = False

                # 3. Check if the album is already present in the filesystem
                album_exists = False
                try:
                    from backend.app.sync import sanitize_filename
                    safe_artist = sanitize_filename(artist)
                    safe_album = sanitize_filename(album)
                    final_dir = Path(user_music_dir) / safe_artist / safe_album
                    album_exists = await asyncio.to_thread(_check_album_dir_exists_sync, final_dir)
                except Exception:
                    pass

                if not album_exists and album and user_features.get("album_downloads", True):
                    logger.info(f"Album '{album}' for starred track '{artist} - {title}' is not in user {username}'s library. Queuing album download...")
                    try:
                        download_id = await self.db.add_album_download(artist, title, album, user_id=uid)
                        _create_tracked_task(
                            download_album_task(download_id, artist, title, album, config, self.db, user_id=uid),
                            task_id=f"album:{download_id}",
                            task_type="album",
                            metadata={"download_id": download_id, "artist": artist, "album": album}
                        )
                    except Exception as e:
                        logger.error(f"Failed to queue album download for starred track: {e}")

                # 4. Mark as processed
                try:
                    await self.db.mark_starred_track_processed(track_id, artist, title, user_id=uid)
                except Exception as e:
                    logger.error(f"Failed to mark starred track as processed in DB for user {username}: {e}")
