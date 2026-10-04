import os
import json
import re
import aiosqlite
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from contextlib import asynccontextmanager

class Database:
    def __init__(self, db_path: str = "/data/verydisco.db"):
        self.db_path = db_path
        self.mem_cache = {}
        self.metadata_mem_cache = None
        self.metadata_mem_cache_ts = 0.0
        import asyncio
        self.mem_cache_lock = asyncio.Lock()
        self.metadata_cache_lock = asyncio.Lock()
        # Ensure parent directory exists
        db_dir = os.path.dirname(os.path.abspath(self.db_path))
        if db_dir:
            try:
                os.makedirs(db_dir, exist_ok=True)
            except Exception:
                pass

    @asynccontextmanager
    async def get_db(self):
        """Asynchronous context manager returning a configured sqlite connection."""
        # Do not silently create a different, non-persistent database when /data
        # is unavailable. A failed mount must be visible to the operator.
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        try:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA synchronous=NORMAL;")
            await conn.execute("PRAGMA cache_size=-32000;")
            await conn.execute("PRAGMA temp_store=MEMORY;")
            await conn.execute("PRAGMA mmap_size=268435456;")
            await conn.execute("PRAGMA foreign_keys = ON;")
            await conn.execute("PRAGMA busy_timeout = 30000;")  # Wait up to 30s on lock
            yield conn
        finally:
            await conn.close()

    async def initialize(self):
        """Creates SQLite tables if they do not exist."""
        async with self.get_db() as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA synchronous=NORMAL;")
            await db.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                status TEXT NOT NULL,
                tracks_found INTEGER DEFAULT 0,
                tracks_downloaded INTEGER DEFAULT 0,
                tracks_skipped INTEGER DEFAULT 0,
                tracks_failed INTEGER DEFAULT 0,
                error_message TEXT,
                source TEXT,
                ended_at TEXT
            );
            """)
            try:
                await db.execute("SELECT source FROM runs LIMIT 1")
            except Exception:
                try:
                    await db.execute("ALTER TABLE runs ADD COLUMN source TEXT")
                    await db.commit()
                except Exception:
                    pass

            try:
                await db.execute("SELECT ended_at FROM runs LIMIT 1")
            except Exception:
                try:
                    await db.execute("ALTER TABLE runs ADD COLUMN ended_at TEXT")
                    await db.commit()
                except Exception:
                    pass

            await db.execute("""
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    artist TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    filename TEXT,
                    lyrics_status TEXT DEFAULT 'missing',
                    error_reason TEXT,
                    bitrate INTEGER,
                    size INTEGER,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS album_downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artist TEXT NOT NULL,
                    title TEXT,
                    album TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    release_mbid TEXT,
                    record_type TEXT
                )
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                run_id INTEGER
            );
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS pinned_artists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artist_name TEXT UNIQUE NOT NULL,
                deezer_id INTEGER NOT NULL,
                picture_url TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS silenced_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_type TEXT NOT NULL,
                target_path TEXT UNIQUE NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS library_cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS processed_starred_tracks (
                navidrome_track_id TEXT PRIMARY KEY,
                artist TEXT NOT NULL,
                title TEXT NOT NULL,
                user_id TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS acoustid_results (
                file_path TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reason TEXT,
                size INTEGER DEFAULT 0,
                mtime_ns INTEGER DEFAULT 0,
                scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            async with db.execute("PRAGMA table_info(acoustid_results)") as cursor:
                acoustid_columns = {row[1] for row in await cursor.fetchall()}
            if "size" not in acoustid_columns:
                await db.execute("ALTER TABLE acoustid_results ADD COLUMN size INTEGER DEFAULT 0")
            if "mtime_ns" not in acoustid_columns:
                await db.execute("ALTER TABLE acoustid_results ADD COLUMN mtime_ns INTEGER DEFAULT 0")
            await db.execute("""
            CREATE TABLE IF NOT EXISTS file_metadata_cache (
                filepath TEXT PRIMARY KEY,
                mtime REAL NOT NULL,
                artist TEXT,
                album TEXT,
                title TEXT,
                track_num INTEGER,
                total_tracks INTEGER,
                quality_desc TEXT,
                bitrate INTEGER,
                bit_depth INTEGER,
                sample_rate INTEGER,
                duration INTEGER,
                year TEXT,
                size INTEGER DEFAULT 0,
                mtime_ns INTEGER DEFAULT 0,
                ctime_ns INTEGER DEFAULT 0,
                device INTEGER DEFAULT 0,
                inode INTEGER DEFAULT 0,
                album_artist TEXT,
                artists_json TEXT DEFAULT '[]',
                disc_num INTEGER DEFAULT 1,
                total_discs INTEGER DEFAULT 1,
                track_mbid TEXT,
                album_mbid TEXT,
                album_artist_mbid TEXT,
                embedded_cover INTEGER DEFAULT 0,
                has_comment INTEGER DEFAULT 0,
                compilation INTEGER DEFAULT 0,
                cache_version INTEGER DEFAULT 1
            );
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS instrumental_overrides (
                filepath TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                marked_at TEXT DEFAULT (datetime('now'))
            );
            """)
            
            # Create essential indexes for fast status polling and query performance
            await db.execute("CREATE INDEX IF NOT EXISTS idx_tracks_run_id ON tracks(run_id);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_album_downloads_status ON album_downloads(status);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_logs_run_id ON logs(run_id);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_file_metadata_cache_mtime ON file_metadata_cache(mtime);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status);")
            await db.commit()
            
            # Migrate year column to file_metadata_cache
            try:
                await db.execute("SELECT year FROM file_metadata_cache LIMIT 1")
            except Exception:
                try:
                    await db.execute("ALTER TABLE file_metadata_cache ADD COLUMN year TEXT")
                except Exception:
                    pass
            metadata_cache_columns = {
                "size": "INTEGER DEFAULT 0", "mtime_ns": "INTEGER DEFAULT 0",
                "ctime_ns": "INTEGER DEFAULT 0", "device": "INTEGER DEFAULT 0",
                "inode": "INTEGER DEFAULT 0", "album_artist": "TEXT",
                "artists_json": "TEXT DEFAULT '[]'", "disc_num": "INTEGER DEFAULT 1",
                "total_discs": "INTEGER DEFAULT 1", "track_mbid": "TEXT",
                "album_mbid": "TEXT", "album_artist_mbid": "TEXT",
                "embedded_cover": "INTEGER DEFAULT 0", "has_comment": "INTEGER DEFAULT 0",
                "compilation": "INTEGER DEFAULT 0", "cache_version": "INTEGER DEFAULT 1",
            }
            async with db.execute("PRAGMA table_info(file_metadata_cache)") as cursor:
                existing_metadata_columns = {row[1] for row in await cursor.fetchall()}
            for column, definition in metadata_cache_columns.items():
                if column not in existing_metadata_columns:
                    await db.execute(f"ALTER TABLE file_metadata_cache ADD COLUMN {column} {definition}")
            await db.commit()

            # Add user_id column to processed_starred_tracks if upgrading from old schema
            try:
                await db.execute("SELECT user_id FROM processed_starred_tracks LIMIT 1")
            except Exception:
                try:
                    await db.execute("ALTER TABLE processed_starred_tracks ADD COLUMN user_id TEXT")
                    await db.commit()
                except Exception:
                    pass
            # Legacy schema used track_id alone as PK, so one user's status
            # could overwrite another user's status for the same Navidrome ID.
            async with db.execute("PRAGMA table_info(processed_starred_tracks)") as cursor:
                starred_columns = await cursor.fetchall()
            pk_columns = [column[1] for column in starred_columns if column[5]]
            if pk_columns == ["navidrome_track_id"]:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS processed_starred_tracks_new (
                        navidrome_track_id TEXT NOT NULL,
                        artist TEXT NOT NULL,
                        title TEXT NOT NULL,
                        user_id TEXT NOT NULL DEFAULT '',
                        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, navidrome_track_id)
                    )
                """)
                await db.execute("""
                    INSERT OR REPLACE INTO processed_starred_tracks_new
                        (navidrome_track_id, artist, title, user_id, processed_at)
                    SELECT navidrome_track_id, artist, title, COALESCE(user_id, ''), processed_at
                    FROM processed_starred_tracks
                """)
                await db.execute("DROP TABLE processed_starred_tracks")
                await db.execute("ALTER TABLE processed_starred_tracks_new RENAME TO processed_starred_tracks")
                await db.commit()

            # ── Multi-user tables ───────────────────────────────────────────────────
            await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id              TEXT PRIMARY KEY,
                username        TEXT UNIQUE NOT NULL,
                display_name    TEXT DEFAULT '',
                is_admin        INTEGER DEFAULT 0,
                music_dir       TEXT DEFAULT '',
                playlist_dir    TEXT DEFAULT '',
                subsonic_token  TEXT DEFAULT '',
                subsonic_salt   TEXT DEFAULT '',
                created_at      TEXT DEFAULT (datetime('now')),
                last_login      TEXT
            );
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS user_config (
                user_id          TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                lb_username      TEXT DEFAULT '',
                lb_token         TEXT DEFAULT '',
                active_playlists TEXT DEFAULT '[]',
                enabled_features TEXT DEFAULT '{}',
                updated_at       TEXT DEFAULT (datetime('now'))
            );
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS music_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK(kind IN ('track', 'album')),
                artist TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                download_id INTEGER,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_music_requests_user_status ON music_requests(user_id, status);")
            # Add user_id to album_downloads for per-user tracking
            try:
                await db.execute("SELECT user_id FROM album_downloads LIMIT 1")
            except Exception:
                try:
                    await db.execute("ALTER TABLE album_downloads ADD COLUMN user_id TEXT")
                    await db.commit()
                except Exception:
                    pass
            for column in ("release_mbid", "record_type"):
                try:
                    await db.execute(f"SELECT {column} FROM album_downloads LIMIT 1")
                except Exception:
                    try:
                        await db.execute(f"ALTER TABLE album_downloads ADD COLUMN {column} TEXT")
                        await db.commit()
                    except Exception:
                        pass
            # Add user_id to runs for per-user tracking
            try:
                await db.execute("SELECT user_id FROM runs LIMIT 1")
            except Exception:
                try:
                    await db.execute("ALTER TABLE runs ADD COLUMN user_id TEXT")
                    await db.commit()
                except Exception:
                    pass
            # These indexes require columns added by the migrations above.
            await db.execute("CREATE INDEX IF NOT EXISTS idx_runs_user_source ON runs(user_id, source);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_album_downloads_user_status ON album_downloads(user_id, status);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_processed_starred_user ON processed_starred_tracks(user_id);")
            # Migrate pinned_artists to user-specific
            try:
                await db.execute("SELECT user_id FROM pinned_artists LIMIT 1")
            except Exception:
                try:
                    await db.execute("ALTER TABLE pinned_artists RENAME TO pinned_artists_old")
                    await db.execute("""
                        CREATE TABLE pinned_artists (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            artist_name TEXT NOT NULL,
                            deezer_id INTEGER NOT NULL,
                            picture_url TEXT,
                            user_id TEXT,
                            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(user_id, artist_name)
                        );
                    """)
                    await db.execute("""
                        INSERT INTO pinned_artists (id, artist_name, deezer_id, picture_url, user_id, added_at)
                        SELECT id, artist_name, deezer_id, picture_url, NULL, added_at FROM pinned_artists_old
                    """)
                    await db.execute("DROP TABLE pinned_artists_old")
                    await db.commit()
                except Exception:
                    pass
            # Add mbid column to pinned_artists if missing
            try:
                await db.execute("SELECT mbid FROM pinned_artists LIMIT 1")
            except Exception:
                try:
                    await db.execute("ALTER TABLE pinned_artists ADD COLUMN mbid TEXT")
                    await db.commit()
                except Exception:
                    pass
            try:
                await db.execute("SELECT playlist_dir FROM users LIMIT 1")
            except Exception:
                try:
                    await db.execute("ALTER TABLE users ADD COLUMN playlist_dir TEXT DEFAULT ''")
                    await db.commit()
                except Exception:
                    pass
            # Add subsonic_token and subsonic_salt to users table if upgrading from old schema
            try:
                await db.execute("SELECT subsonic_token FROM users LIMIT 1")
            except Exception:
                try:
                    await db.execute("ALTER TABLE users ADD COLUMN subsonic_token TEXT DEFAULT ''")
                    await db.execute("ALTER TABLE users ADD COLUMN subsonic_salt TEXT DEFAULT ''")
                    await db.commit()
                except Exception:
                    pass
            # Add renaming_pattern to users table if upgrading from old schema
            try:
                await db.execute("SELECT renaming_pattern FROM users LIMIT 1")
            except Exception:
                try:
                    await db.execute("ALTER TABLE users ADD COLUMN renaming_pattern TEXT DEFAULT '{Artist}/{Year} - {Album}/{Track:2} - {Title}'")
                    await db.commit()
                except Exception:
                    pass
            # Add enabled_features to user_config table if upgrading from old schema
            try:
                await db.execute("SELECT enabled_features FROM user_config LIMIT 1")
            except Exception:
                try:
                    await db.execute("ALTER TABLE user_config ADD COLUMN enabled_features TEXT DEFAULT '{}'")
                    await db.commit()
                except Exception:
                    pass

            # ── Unified Library Index ──────────────────────────────────────────────
            await db.execute("""
            CREATE TABLE IF NOT EXISTS library_index (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id              TEXT    NOT NULL,
                filepath             TEXT    NOT NULL,
                mtime                REAL    NOT NULL,
                artist               TEXT,
                album                TEXT,
                title                TEXT,
                track_num            INTEGER,
                total_tracks         INTEGER,
                disc_num             INTEGER,
                total_discs          INTEGER,
                year                 TEXT,
                album_artist         TEXT,
                duration             INTEGER,
                ext                  TEXT,
                bitrate              INTEGER,
                bit_depth            INTEGER,
                sample_rate          INTEGER,
                track_mbid           TEXT,
                album_mbid           TEXT,
                lyrics_synced        INTEGER DEFAULT 0,
                lyrics_plain         INTEGER DEFAULT 0,
                has_cover            INTEGER DEFAULT 0,
                issue_missing_meta   INTEGER DEFAULT 0,
                issue_dirty_tags     INTEGER DEFAULT 0,
                issue_dirty_reason   TEXT,
                issue_naming         INTEGER DEFAULT 0,
                issue_naming_expected TEXT,
                issue_duplicate      INTEGER DEFAULT 0,
                issue_duplicate_of   TEXT,
                issue_misfiled       INTEGER DEFAULT 0,
                issue_misfiled_reason TEXT,
                artist_norm          TEXT,
                album_norm           TEXT,
                title_norm           TEXT,
                scanned_at           TEXT DEFAULT (datetime('now')),
                mbid_enriched_at     TEXT,
                size                 INTEGER DEFAULT 0,
                mtime_ns             INTEGER DEFAULT 0,
                ctime_ns             INTEGER DEFAULT 0,
                device               INTEGER DEFAULT 0,
                inode                INTEGER DEFAULT 0,
                artists_json         TEXT DEFAULT '[]',
                embedded_cover       INTEGER DEFAULT 0,
                has_comment          INTEGER DEFAULT 0,
                compilation          INTEGER DEFAULT 0,
                UNIQUE(user_id, filepath)
            );
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_li_user_album    ON library_index(user_id, album_norm);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_li_user_artist   ON library_index(user_id, artist_norm);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_li_track_mbid    ON library_index(track_mbid);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_li_album_mbid    ON library_index(album_mbid);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_li_issues        ON library_index(user_id, issue_dirty_tags, issue_missing_meta, issue_naming, issue_duplicate, issue_misfiled);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_li_lyrics        ON library_index(user_id, lyrics_synced, lyrics_plain);")
            async with db.execute("PRAGMA table_info(library_index)") as cursor:
                existing_library_columns = {row[1] for row in await cursor.fetchall()}
            library_columns = {
                "size": "INTEGER DEFAULT 0", "mtime_ns": "INTEGER DEFAULT 0",
                "ctime_ns": "INTEGER DEFAULT 0", "device": "INTEGER DEFAULT 0",
                "inode": "INTEGER DEFAULT 0", "artists_json": "TEXT DEFAULT '[]'",
                "embedded_cover": "INTEGER DEFAULT 0", "has_comment": "INTEGER DEFAULT 0",
                "compilation": "INTEGER DEFAULT 0",
            }
            for column, definition in library_columns.items():
                if column not in existing_library_columns:
                    await db.execute(f"ALTER TABLE library_index ADD COLUMN {column} {definition}")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS metadata_provider_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
            await db.commit()

    # Runs Operations
    async def create_run(self, status: str = "running", source: Optional[str] = None, user_id: Optional[str] = None) -> int:
        async with self.get_db() as db:
            now = datetime.utcnow().isoformat()
            cursor = await db.execute(
                "INSERT INTO runs (timestamp, status, source, user_id) VALUES (?, ?, ?, ?)",
                (now, status, source, user_id)
            )
            run_id = cursor.lastrowid
            await db.commit()
            return run_id

    async def update_run(self, run_id: int, status: str, tracks_found: int, tracks_downloaded: int, 
                         tracks_skipped: int, tracks_failed: int, error_message: Optional[str] = None):
        async with self.get_db() as db:
            await db.execute(
                """UPDATE runs 
                   SET status = ?, tracks_found = ?, tracks_downloaded = ?, 
                       tracks_skipped = ?, tracks_failed = ?, error_message = ?
                   WHERE id = ?""",
                (status, tracks_found, tracks_downloaded, tracks_skipped, tracks_failed, error_message, run_id)
            )
            await db.commit()

    async def get_latest_run(self, source: Optional[str] = None, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        async with self.get_db() as db:
            if source and user_id:
                query = "SELECT * FROM runs WHERE source = ? AND user_id = ? ORDER BY id DESC LIMIT 1"
                params = (source, user_id)
            elif source:
                query = "SELECT * FROM runs WHERE source = ? ORDER BY id DESC LIMIT 1"
                params = (source,)
            elif user_id:
                query = "SELECT * FROM runs WHERE user_id = ? ORDER BY id DESC LIMIT 1"
                params = (user_id,)
            else:
                query = "SELECT * FROM runs ORDER BY id DESC LIMIT 1"
                params = ()
            async with db.execute(query, params) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_runs(self, limit: int = 20, offset: int = 0, user_id: Optional[str] = None) -> Tuple[List[Dict[str, Any]], int]:
        async with self.get_db() as db:
            if user_id:
                async with db.execute("SELECT COUNT(*) as cnt FROM runs WHERE user_id = ?", (user_id,)) as cursor:
                    row = await cursor.fetchone()
                    total = row["cnt"] if row else 0
                async with db.execute(
                    "SELECT * FROM runs WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?", (user_id, limit, offset)
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows], total
            else:
                async with db.execute("SELECT COUNT(*) as cnt FROM runs") as cursor:
                    row = await cursor.fetchone()
                    total = row["cnt"] if row else 0
                async with db.execute(
                    "SELECT * FROM runs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows], total

    # Tracks Operations
    async def add_track(self, run_id: int, artist: str, title: str, status: str, 
                        filename: Optional[str] = None, lyrics_status: Optional[str] = None, 
                        error_reason: Optional[str] = None, bitrate: Optional[int] = None, 
                        size: Optional[int] = None) -> int:
        async with self.get_db() as db:
            cursor = await db.execute(
                """INSERT INTO tracks (run_id, artist, title, status, filename, lyrics_status, error_reason, bitrate, size) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, artist, title, status, filename, lyrics_status, error_reason, bitrate, size)
            )
            track_id = cursor.lastrowid
            await db.commit()
            return track_id

    async def get_pending_album_downloads(self) -> list:
        async with self.get_db() as conn:
            cursor = await conn.execute("SELECT * FROM album_downloads WHERE status = 'pending' LIMIT 20")
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def update_album_download_status(self, download_id: int, status: str):
        async with self.get_db() as conn:
            await conn.execute(
                "UPDATE album_downloads SET status = ? WHERE id = ?",
                (status, download_id)
            )
            await conn.commit()

    async def update_track(self, track_id: int, status: str, filename: Optional[str] = None, 
                           lyrics_status: Optional[str] = None, error_reason: Optional[str] = None, 
                           bitrate: Optional[int] = None, size: Optional[int] = None):
        async with self.get_db() as db:
            await db.execute(
                """UPDATE tracks 
                   SET status = ?, filename = ?, lyrics_status = ?, error_reason = ?, bitrate = ?, size = ? 
                   WHERE id = ?""",
                (status, filename, lyrics_status, error_reason, bitrate, size, track_id)
            )
            await db.commit()

    async def get_tracks_for_run(self, run_id: int) -> List[Dict[str, Any]]:
        async with self.get_db() as db:
            async with db.execute("SELECT * FROM tracks WHERE run_id = ? ORDER BY id ASC", (run_id,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    # Logs Operations
    async def add_log(self, level: str, message: str, run_id: Optional[int] = None):
        async with self.get_db() as db:
            now = datetime.utcnow().isoformat()
            await db.execute(
                "INSERT INTO logs (timestamp, level, message, run_id) VALUES (?, ?, ?, ?)",
                (now, level, message, run_id)
            )
            await db.commit()

    async def get_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        async with self.get_db() as db:
            async with db.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in reversed(rows)]

    # Pinned Artists Operations
    async def get_pinned_artists(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        async with self.get_db() as db:
            if user_id:
                async with db.execute("SELECT * FROM pinned_artists WHERE user_id = ? ORDER BY artist_name ASC", (user_id,)) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]
            else:
                async with db.execute("SELECT * FROM pinned_artists WHERE user_id IS NULL ORDER BY artist_name ASC") as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]

    async def add_pinned_artist(self, artist_name: str, deezer_id: Optional[int] = 0, picture_url: Optional[str] = None, user_id: Optional[str] = None, mbid: Optional[str] = None) -> int:
        async with self.get_db() as db:
            cursor = await db.execute(
                "INSERT OR REPLACE INTO pinned_artists (artist_name, deezer_id, picture_url, user_id, mbid) VALUES (?, ?, ?, ?, ?)",
                (artist_name, deezer_id or 0, picture_url, user_id, mbid)
            )
            await db.commit()
            return cursor.lastrowid

    async def delete_pinned_artist(self, id: int):
        async with self.get_db() as db:
            await db.execute("DELETE FROM pinned_artists WHERE id = ?", (id,))
            await db.commit()

    async def purge_pinned_artists(self, user_id: Optional[str] = None):
        async with self.get_db() as db:
            if user_id:
                await db.execute("DELETE FROM pinned_artists WHERE user_id = ?", (user_id,))
            else:
                await db.execute("DELETE FROM pinned_artists")
            await db.commit()

    # Silenced Issues Operations
    async def get_silenced_issues(self) -> List[str]:
        async with self.get_db() as db:
            async with db.execute("SELECT target_path FROM silenced_issues") as cursor:
                rows = await cursor.fetchall()
                return [r["target_path"] for r in rows]

    async def add_silenced_issue(self, issue_type: str, target_path: str):
        async with self.get_db() as db:
            await db.execute(
                "INSERT OR IGNORE INTO silenced_issues (issue_type, target_path) VALUES (?, ?)",
                (issue_type, target_path)
            )
            await db.commit()

    async def delete_silenced_issue(self, target_path: str):
        async with self.get_db() as db:
            await db.execute("DELETE FROM silenced_issues WHERE target_path = ?", (target_path,))
            await db.commit()

    # Cache operations
    async def set_cache(self, key: str, value: Any, ttl: int = 3600):
        import time
        async with self.mem_cache_lock:
            self.mem_cache[key] = {
                "value": value,
                "expires": time.time() + ttl
            }
        import json
        val_str = json.dumps(value, ensure_ascii=False)
        async with self.get_db() as db:
            await db.execute(
                "INSERT OR REPLACE INTO library_cache (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (key, val_str)
            )
            await db.commit()

    async def get_cache(self, key: str) -> Optional[Any]:
        import time
        async with self.mem_cache_lock:
            entry = self.mem_cache.get(key)
            if entry:
                if time.time() < entry["expires"]:
                    return entry["value"]
                else:
                    self.mem_cache.pop(key, None)
        import json
        async with self.get_db() as db:
            async with db.execute("SELECT value, updated_at FROM library_cache WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    try:
                        from datetime import datetime, timezone
                        updated = datetime.fromisoformat(row["updated_at"].replace(" ", "T")).replace(tzinfo=timezone.utc)
                        if time.time() - updated.timestamp() >= 3600:
                            return None
                        val = json.loads(row["value"])
                        async with self.mem_cache_lock:
                            self.mem_cache[key] = {
                                "value": val,
                                "expires": time.time() + 3600
                            }
                        return val
                    except Exception:
                        return None
                return None

    async def delete_cache(self, key: str):
        async with self.mem_cache_lock:
            self.mem_cache.pop(key, None)
        async with self.get_db() as db:
            await db.execute("DELETE FROM library_cache WHERE key = ?", (key,))
            await db.commit()

    async def get_all_file_metadata(self) -> dict:
        import time
        if self.metadata_mem_cache is not None and (time.time() - self.metadata_mem_cache_ts) < 60:
            return self.metadata_mem_cache
        async with self.get_db() as db:
            async with db.execute("SELECT * FROM file_metadata_cache") as cursor:
                rows = await cursor.fetchall()
                self.metadata_mem_cache = {r["filepath"]: dict(r) for r in rows}
                self.metadata_mem_cache_ts = time.time()
                return self.metadata_mem_cache

    async def clear_file_metadata_cache(self):
        async with self.metadata_cache_lock:
            self.metadata_mem_cache_ts = 0

    async def save_file_metadata_batch(self, entries: list):
        if not entries:
            return
        async with self.get_db() as db:
            await db.executemany("""
                INSERT OR REPLACE INTO file_metadata_cache 
                (filepath, mtime, artist, album, title, track_num, total_tracks, quality_desc,
                 bitrate, bit_depth, sample_rate, duration, year, size, mtime_ns, ctime_ns,
                 device, inode, album_artist, artists_json, disc_num, total_discs, track_mbid,
                 album_mbid, album_artist_mbid, embedded_cover, has_comment, compilation, cache_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, entries)
            await db.commit()
        self.metadata_mem_cache = None

    async def get_provider_cache(self, cache_key: str) -> Optional[Any]:
        import json, time
        async with self.get_db() as db:
            async with db.execute(
                "SELECT payload FROM metadata_provider_cache WHERE cache_key = ? AND expires_at > ?",
                (cache_key, time.time()),
            ) as cursor:
                row = await cursor.fetchone()
        return json.loads(row["payload"]) if row else None

    async def set_provider_cache(self, cache_key: str, payload: Any, ttl_seconds: int) -> None:
        import json, time
        async with self.get_db() as db:
            await db.execute(
                "INSERT OR REPLACE INTO metadata_provider_cache(cache_key, payload, expires_at, updated_at) "
                "VALUES (?, ?, ?, datetime('now'))",
                (cache_key, json.dumps(payload, ensure_ascii=False), time.time() + ttl_seconds),
            )
            await db.commit()

    async def is_starred_track_processed(self, track_id: str, user_id: Optional[str] = None) -> bool:
        async with self.get_db() as db:
            if user_id:
                async with db.execute(
                    "SELECT 1 FROM processed_starred_tracks WHERE navidrome_track_id = ? AND user_id = ?",
                    (track_id, user_id)
                ) as cursor:
                    row = await cursor.fetchone()
                    return row is not None
            else:
                async with db.execute(
                    "SELECT 1 FROM processed_starred_tracks WHERE navidrome_track_id = ? AND user_id = ''", (track_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    return row is not None

    async def mark_starred_track_processed(self, track_id: str, artist: str, title: str, user_id: Optional[str] = None):
        async with self.get_db() as db:
            await db.execute(
                "INSERT OR REPLACE INTO processed_starred_tracks (navidrome_track_id, artist, title, user_id) VALUES (?, ?, ?, ?)",
                (track_id, artist, title, user_id or "")
            )
            await db.commit()

    # ── User Management ───────────────────────────────────────────────────────

    async def get_or_create_user(
        self,
        user_id: str,
        username: str,
        display_name: str = "",
        is_admin: bool = False,
        music_dir: str = "",
    ) -> Dict[str, Any]:
        """Upsert a user row. Returns the full user dict."""
        import json
        from datetime import datetime
        now = datetime.utcnow().isoformat()
        async with self.get_db() as db:
            await db.execute(
                """
                INSERT INTO users (id, username, display_name, is_admin, music_dir, last_login)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    username=excluded.username,
                    display_name=excluded.display_name,
                    is_admin=excluded.is_admin,
                    last_login=excluded.last_login,
                    music_dir=COALESCE(NULLIF(users.music_dir, ''), excluded.music_dir)
                """,
                (user_id, username, display_name, 1 if is_admin else 0, music_dir, now),
            )
            # Ensure user_config row exists
            await db.execute(
                "INSERT OR IGNORE INTO user_config (user_id, lb_username, lb_token, active_playlists) VALUES (?, '', '', '[]')",
                (user_id,),
            )
            await db.commit()

        return await self.get_user_by_id(user_id)

    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        async with self.get_db() as db:
            async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        async with self.get_db() as db:
            async with db.execute("SELECT * FROM users WHERE username = ?", (username,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def list_users(self) -> List[Dict[str, Any]]:
        async with self.get_db() as db:
            async with db.execute("SELECT * FROM users ORDER BY username ASC") as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_user_config(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get per-user ListenBrainz config."""
        import json
        async with self.get_db() as db:
            async with db.execute(
                "SELECT uc.*, u.username, u.display_name, u.is_admin, u.music_dir, u.playlist_dir, u.renaming_pattern "
                "FROM user_config uc JOIN users u ON u.id = uc.user_id WHERE uc.user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                data = dict(row)
                try:
                    data["active_playlists"] = json.loads(data["active_playlists"] or "[]")
                except Exception:
                    data["active_playlists"] = []
                
                try:
                    data["enabled_features"] = json.loads(data.get("enabled_features") or "{}")
                except Exception:
                    data["enabled_features"] = {}
                
                # Default features values if missing
                defaults = {
                    "starred_sync": True,
                    "listenbrainz_sync": True,
                    "discovery": True,
                    "album_downloads": True
                }
                for k, v in defaults.items():
                    if k not in data["enabled_features"]:
                        data["enabled_features"][k] = v
                        
                return data

    async def save_user_config(
        self,
        user_id: str,
        lb_username: str,
        lb_token: str,
        active_playlists: List[str],
    ) -> None:
        """Persist per-user ListenBrainz config."""
        import json
        from datetime import datetime
        now = datetime.utcnow().isoformat()
        async with self.get_db() as db:
            await db.execute(
                """
                INSERT INTO user_config (user_id, lb_username, lb_token, active_playlists, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    lb_username=excluded.lb_username,
                    lb_token=excluded.lb_token,
                    active_playlists=excluded.active_playlists,
                    updated_at=excluded.updated_at
                """,
                (user_id, lb_username, lb_token, json.dumps(active_playlists), now),
            )
            await db.commit()

    async def save_user_features(
        self,
        user_id: str,
        enabled_features: Dict[str, bool],
    ) -> None:
        """Persist per-user enabled features flags."""
        import json
        from datetime import datetime
        now = datetime.utcnow().isoformat()
        async with self.get_db() as db:
            await db.execute(
                """
                INSERT INTO user_config (user_id, enabled_features, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    enabled_features=excluded.enabled_features,
                    updated_at=excluded.updated_at
                """,
                (user_id, json.dumps(enabled_features), now),
            )
            await db.commit()

    async def update_user_paths(
        self,
        user_id: str,
        music_dir: str,
        playlist_dir: str,
        renaming_pattern: str = ""
    ) -> None:
        """Update a user's personal music and playlist directories and renaming pattern."""
        async with self.get_db() as db:
            await db.execute(
                "UPDATE users SET music_dir = ?, playlist_dir = ?, renaming_pattern = ? WHERE id = ?",
                (music_dir, playlist_dir, renaming_pattern, user_id),
            )
            await db.commit()

    async def add_album_download(
        self, artist: str, title: str, album: str, user_id: Optional[str] = None,
        release_mbid: Optional[str] = None, record_type: Optional[str] = None,
    ) -> int:
        async with self.get_db() as conn:
            cursor = await conn.execute(
                "INSERT INTO album_downloads (artist, title, album, status, user_id, release_mbid, record_type) VALUES (?, ?, ?, 'pending', ?, ?, ?)",
                (artist, title, album, user_id, release_mbid, record_type)
            )
            await conn.commit()
            return cursor.lastrowid

    async def get_album_download_status(self, download_id: int) -> Optional[str]:
        async with self.get_db() as conn:
            cursor = await conn.execute("SELECT status FROM album_downloads WHERE id = ?", (download_id,))
            row = await cursor.fetchone()
            return row["status"] if row else None

    async def add_music_request(self, user_id: str, kind: str, artist: str, title: str = "", album: str = "") -> int:
        async with self.get_db() as conn:
            cursor = await conn.execute(
                "SELECT id FROM music_requests WHERE user_id = ? AND kind = ? AND artist = ? AND title = ? AND album = ? AND status IN ('pending', 'queued') LIMIT 1",
                (user_id, kind, artist, title, album),
            )
            existing = await cursor.fetchone()
            if existing:
                return existing["id"]
            cursor = await conn.execute(
                "INSERT INTO music_requests (user_id, kind, artist, title, album) VALUES (?, ?, ?, ?, ?)",
                (user_id, kind, artist, title, album),
            )
            await conn.commit()
            return cursor.lastrowid

    async def list_music_requests(self, user_id: str, is_admin: bool = False) -> list:
        async with self.get_db() as conn:
            if is_admin:
                cursor = await conn.execute("SELECT * FROM music_requests ORDER BY id DESC LIMIT 200")
            else:
                cursor = await conn.execute("SELECT * FROM music_requests WHERE user_id = ? ORDER BY id DESC LIMIT 200", (user_id,))
            return [dict(row) for row in await cursor.fetchall()]

    async def list_queued_music_requests(self) -> list:
        async with self.get_db() as conn:
            cursor = await conn.execute("SELECT * FROM music_requests WHERE status = 'queued'")
            return [dict(row) for row in await cursor.fetchall()]

    async def get_music_request(self, request_id: int) -> Optional[dict]:
        async with self.get_db() as conn:
            cursor = await conn.execute("SELECT * FROM music_requests WHERE id = ?", (request_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def transition_music_request(self, request_id: int, old_status: str, new_status: str, *, error: Optional[str] = None, download_id: Optional[int] = None) -> bool:
        async with self.get_db() as conn:
            cursor = await conn.execute(
                "UPDATE music_requests SET status = ?, error = ?, download_id = COALESCE(?, download_id), updated_at = datetime('now') WHERE id = ? AND status = ?",
                (new_status, error, download_id, request_id, old_status),
            )
            await conn.commit()
            return cursor.rowcount == 1

    async def save_acoustid_result(
        self, file_path: str, status: str, reason: Optional[str] = None,
        size: int = 0, mtime_ns: int = 0,
    ):
        async with self.get_db() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO acoustid_results "
                "(file_path, status, reason, size, mtime_ns, scanned_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (file_path, status, reason, size, mtime_ns)
            )
            await conn.commit()

    async def get_acoustid_results(self) -> list:
        async with self.get_db() as conn:
            cursor = await conn.execute("SELECT * FROM acoustid_results")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_acoustid_candidates(self, user_id: str, limit: int = 50) -> list:
        sql = """
            SELECT li.filepath, li.size, li.mtime_ns
            FROM library_index li LEFT JOIN acoustid_results ar ON ar.file_path = li.filepath
            WHERE li.user_id = ? AND (
                ar.file_path IS NULL OR ar.size != li.size OR ar.mtime_ns != li.mtime_ns
            ) ORDER BY li.filepath
        """
        params: tuple[Any, ...] = (user_id,)
        if limit > 0:
            sql += " LIMIT ?"
            params = (user_id, limit)
        async with self.get_db() as db:
            async with db.execute(sql, params) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def clear_acoustid_result(self, file_path: str):
        async with self.get_db() as conn:
            await conn.execute("DELETE FROM acoustid_results WHERE file_path = ?", (file_path,))
            await conn.commit()

    # ── Library Index ─────────────────────────────────────────────────────────

    async def clear_library_index(self, user_id: str) -> None:
        """Wipe all library_index rows for a user before rebuilding."""
        async with self.get_db() as db:
            await db.execute("DELETE FROM library_index WHERE user_id = ?", (user_id,))
            await db.commit()

    async def upsert_library_index_batch(self, entries: List[Dict[str, Any]]) -> None:
        """Bulk-insert or replace library_index rows. Each entry is a dict matching column names."""
        if not entries:
            return
        cols = [
            "user_id", "filepath", "mtime",
            "artist", "album", "title", "track_num", "total_tracks",
            "disc_num", "total_discs", "year", "album_artist", "duration",
            "ext", "bitrate", "bit_depth", "sample_rate",
            "track_mbid", "album_mbid",
            "lyrics_synced", "lyrics_plain", "has_cover",
            "issue_missing_meta", "issue_dirty_tags", "issue_dirty_reason",
            "issue_naming", "issue_naming_expected",
            "issue_duplicate", "issue_duplicate_of",
            "issue_misfiled", "issue_misfiled_reason",
            "artist_norm", "album_norm", "title_norm",
            "size", "mtime_ns", "ctime_ns", "device", "inode", "artists_json",
            "embedded_cover", "has_comment", "compilation",
        ]
        placeholders = ", ".join(["?"] * len(cols))
        col_str = ", ".join(cols)
        sql = f"INSERT OR REPLACE INTO library_index ({col_str}) VALUES ({placeholders})"
        rows = [tuple(e.get(c) for c in cols) for e in entries]
        async with self.get_db() as db:
            await db.executemany(sql, rows)
            await db.commit()

    async def replace_library_index(self, user_id: str, entries: List[Dict[str, Any]]) -> None:
        """Atomically publish a complete scan so readers never observe an empty index."""
        cols = [
            "user_id", "filepath", "mtime", "artist", "album", "title", "track_num",
            "total_tracks", "disc_num", "total_discs", "year", "album_artist", "duration",
            "ext", "bitrate", "bit_depth", "sample_rate", "track_mbid", "album_mbid",
            "lyrics_synced", "lyrics_plain", "has_cover", "issue_missing_meta",
            "issue_dirty_tags", "issue_dirty_reason", "issue_naming", "issue_naming_expected",
            "issue_duplicate", "issue_duplicate_of", "issue_misfiled", "issue_misfiled_reason",
            "artist_norm", "album_norm", "title_norm", "size", "mtime_ns", "ctime_ns",
            "device", "inode", "artists_json", "embedded_cover", "has_comment", "compilation",
        ]
        placeholders = ", ".join(["?"] * len(cols))
        sql = f"INSERT INTO library_index ({', '.join(cols)}) VALUES ({placeholders})"
        rows = [tuple(entry.get(column) for column in cols) for entry in entries]
        async with self.get_db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute("DELETE FROM library_index WHERE user_id = ?", (user_id,))
                if rows:
                    await db.executemany(sql, rows)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def query_library_index_complete(self, user_id: str) -> List[Dict[str, Any]]:
        async with self.get_db() as db:
            async with db.execute("SELECT * FROM library_index WHERE user_id = ?", (user_id,)) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def invalidate_library_paths(self, user_id: str, paths: list[str]) -> None:
        """Invalidate only mutated/deleted files; the next incremental scan reparses them."""
        if not paths:
            return
        placeholders = ",".join("?" for _ in paths)
        async with self.get_db() as db:
            await db.execute(
                f"DELETE FROM file_metadata_cache WHERE filepath IN ({placeholders})", tuple(paths)
            )
            await db.execute(
                f"DELETE FROM library_index WHERE user_id = ? AND filepath IN ({placeholders})",
                (user_id, *paths),
            )
            await db.execute(
                f"DELETE FROM acoustid_results WHERE file_path IN ({placeholders})", tuple(paths)
            )
            await db.commit()
        self.metadata_mem_cache = None

    async def refresh_library_paths(self, user_id: str, paths: list[str]) -> None:
        """Re-read changed media and publish their new catalog rows immediately."""
        if not paths:
            return
        from backend.app.library_reader import AUDIO_SUFFIXES, read_library_file

        unique_paths = list(dict.fromkeys(str(Path(value)) for value in paths))
        placeholders = ",".join("?" for _ in unique_paths)
        async with self.get_db() as db:
            async with db.execute(
                f"SELECT * FROM library_index WHERE user_id = ? AND filepath IN ({placeholders})",
                (user_id, *unique_paths),
            ) as cursor:
                old_rows = {row["filepath"]: dict(row) for row in await cursor.fetchall()}

        def norm(value: str) -> str:
            return re.sub(r"[^\w]", "", (value or "").replace("$", "s").casefold())

        entries: list[dict[str, Any]] = []
        deleted: list[str] = []
        for value in unique_paths:
            path = Path(value)
            if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
                deleted.append(value)
                continue
            try:
                meta = read_library_file(path)
            except Exception:
                # A failed point refresh must not destroy the last known-good row.
                continue
            previous = old_rows.get(value, {})
            sidecar = path.with_suffix(".lrc")
            lyrics_text = ""
            if sidecar.is_file():
                try:
                    lyrics_text = sidecar.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    pass
            has_folder_cover = any(
                (path.parent / name).is_file()
                for name in ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.png")
            )
            artist = meta["artist"]
            album = meta["album"]
            title = meta["title"]
            entry = {
                **previous,
                "user_id": user_id, "filepath": value, "mtime": meta["mtime"],
                "artist": artist, "album": album, "title": title,
                "track_num": meta["track_num"], "total_tracks": meta["total_tracks"],
                "disc_num": meta["disc_num"], "total_discs": meta["disc_total"],
                "year": meta["year"], "album_artist": meta["album_artist"],
                "duration": meta["duration"], "ext": path.suffix.lower().lstrip("."),
                "bitrate": meta["bitrate"], "bit_depth": meta["bit_depth"],
                "sample_rate": meta["sample_rate"], "track_mbid": meta["track_mbid"] or None,
                "album_mbid": meta["album_mbid"] or None,
                "lyrics_synced": int(bool(re.search(r"\[\d{1,3}:\d{2}(?:\.\d+)?\]", lyrics_text))),
                "lyrics_plain": int(bool(lyrics_text)),
                "has_cover": int(meta["embedded_cover"] or has_folder_cover),
                "issue_missing_meta": int(not artist or not album or not title),
                "issue_dirty_tags": previous.get("issue_dirty_tags", 0) or 0,
                "issue_dirty_reason": previous.get("issue_dirty_reason"),
                "issue_naming": previous.get("issue_naming", 0) or 0,
                "issue_naming_expected": previous.get("issue_naming_expected"),
                "issue_duplicate": previous.get("issue_duplicate", 0) or 0,
                "issue_duplicate_of": previous.get("issue_duplicate_of"),
                "issue_misfiled": previous.get("issue_misfiled", 0) or 0,
                "issue_misfiled_reason": previous.get("issue_misfiled_reason"),
                "artist_norm": norm(meta["album_artist"] or artist),
                "album_norm": norm(album), "title_norm": norm(title),
                "size": meta["size"], "mtime_ns": meta["mtime_ns"],
                "ctime_ns": meta["ctime_ns"], "device": meta["device"], "inode": meta["inode"],
                "artists_json": meta.get("artists_json") or json.dumps([artist]),
                "embedded_cover": int(meta["embedded_cover"]),
                "has_comment": int(meta["has_comment"]), "compilation": int(meta["compilation"]),
            }
            entries.append(entry)
        if deleted:
            await self.invalidate_library_paths(user_id, deleted)
        if entries:
            await self.upsert_library_index_batch(entries)
        self.metadata_mem_cache = None

    async def query_library_missing_art_albums(self, user_id: str) -> List[Dict[str, Any]]:
        async with self.get_db() as db:
            async with db.execute("""
                SELECT COALESCE(album_artist, artist, 'Unknown Artist') AS artist_name,
                       COALESCE(album, 'Unknown Album') AS album_name,
                       MIN(filepath) AS sample_filepath,
                       MAX(bitrate) AS bitrate, MAX(ext) AS format
                FROM library_index WHERE user_id = ?
                GROUP BY artist_norm, album_norm
                HAVING MAX(has_cover) = 0
                ORDER BY artist_name, album_name
            """, (user_id,)) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def query_library_duplicate_rows(self, user_id: str) -> List[Dict[str, Any]]:
        async with self.get_db() as db:
            async with db.execute("""
                SELECT filepath, artist, title, album, size, bitrate, ext, artist_norm, title_norm
                FROM library_index
                WHERE user_id = ? AND (artist_norm, title_norm) IN (
                    SELECT artist_norm, title_norm FROM library_index WHERE user_id = ?
                    GROUP BY artist_norm, title_norm HAVING COUNT(*) > 1
                ) ORDER BY artist_norm, title_norm
            """, (user_id, user_id)) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def query_library_feat_rows(self, user_id: str) -> List[Dict[str, Any]]:
        async with self.get_db() as db:
            async with db.execute("""
                SELECT filepath, artist, title, album FROM library_index
                WHERE user_id = ? AND (
                    lower(artist) LIKE '% feat.%' OR lower(artist) LIKE '% ft.%'
                    OR lower(artist) LIKE '% featuring %'
                ) ORDER BY artist, album, track_num
            """, (user_id,)) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def mark_track_mbid(self, filepath: str, track_mbid: Optional[str], album_mbid: Optional[str]) -> None:
        """Update MBID columns for a single row after enrichment lookup."""
        from datetime import datetime
        async with self.get_db() as db:
            await db.execute(
                "UPDATE library_index SET track_mbid = ?, album_mbid = ?, mbid_enriched_at = ? WHERE filepath = ?",
                (track_mbid, album_mbid, datetime.utcnow().isoformat(), filepath)
            )
            await db.commit()

    async def query_library_index_for_user(
        self, user_id: str, artist_norm: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Fetch library_index records for fast in-memory batch matching."""
        async with self.get_db() as db:
            if artist_norm:
                async with db.execute(
                    "SELECT filepath, artist, album, title, artist_norm, album_norm, title_norm, "
                    "track_mbid, album_mbid, bitrate, bit_depth, ext "
                    "FROM library_index WHERE user_id = ? AND (artist_norm = ? OR artist_norm LIKE ?)",
                    (user_id, artist_norm, f"%{artist_norm}%")
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]
            else:
                async with db.execute(
                    "SELECT filepath, artist, album, title, artist_norm, album_norm, title_norm, "
                    "track_mbid, album_mbid, bitrate, bit_depth, ext "
                    "FROM library_index WHERE user_id = ?",
                    (user_id,)
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]

    async def get_library_rows_missing_mbid(self, user_id: str) -> List[Dict[str, Any]]:
        """Return rows where track_mbid is null and artist+title are present."""
        async with self.get_db() as db:
            async with db.execute(
                "SELECT filepath, artist, album, title FROM library_index "
                "WHERE user_id = ? AND track_mbid IS NULL AND artist IS NOT NULL AND title IS NOT NULL",
                (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def query_library_album(
        self, user_id: str, artist_norm: str, album_norm: str
    ) -> Optional[Dict[str, Any]]:
        """Return track count + list of tracks for a given artist+album (normalised names)."""
        async with self.get_db() as db:
            async with db.execute(
                "SELECT filepath, title, title_norm, track_mbid, bitrate, bit_depth, ext "
                "FROM library_index WHERE user_id = ? AND artist_norm = ? AND album_norm = ?",
                (user_id, artist_norm, album_norm)
            ) as cursor:
                rows = await cursor.fetchall()
                if not rows:
                    return None
                return {
                    "track_count": len(rows),
                    "tracks": [dict(r) for r in rows]
                }

    async def query_library_album_by_mbid(
        self, user_id: str, album_mbid: str
    ) -> Optional[Dict[str, Any]]:
        """Return track count for a given album MBID."""
        async with self.get_db() as db:
            async with db.execute(
                "SELECT COUNT(*) as cnt FROM library_index WHERE user_id = ? AND album_mbid = ?",
                (user_id, album_mbid)
            ) as cursor:
                row = await cursor.fetchone()
                if not row or row["cnt"] == 0:
                    return None
                return {"track_count": row["cnt"]}

    async def query_library_issues(self, user_id: str) -> List[Dict[str, Any]]:
        """Return all rows that have at least one issue flag set."""
        async with self.get_db() as db:
            async with db.execute(
                "SELECT * FROM library_index WHERE user_id = ? AND ("
                "issue_missing_meta = 1 OR issue_dirty_tags = 1 OR "
                "issue_naming = 1 OR issue_duplicate = 1 OR issue_misfiled = 1)",
                (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def query_library_lyrics_missing(self, user_id: str) -> List[Dict[str, Any]]:
        """Return all rows where no lyrics (synced or plain) exist."""
        async with self.get_db() as db:
            async with db.execute(
                "SELECT filepath, artist, album, title, duration FROM library_index "
                "WHERE user_id = ? AND lyrics_synced = 0 AND lyrics_plain = 0",
                (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def query_library_naming_issues(self, user_id: str) -> List[Dict[str, Any]]:
        """Return all rows where the filename doesn't match the naming convention."""
        async with self.get_db() as db:
            async with db.execute(
                "SELECT filepath, artist, album, title, track_num, year, issue_naming_expected "
                "FROM library_index WHERE user_id = ? AND issue_naming = 1",
                (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def query_library_albums_grouped(
        self, user_id: str
    ) -> List[Dict[str, Any]]:
        """Return one row per album: artist, album, track_count, has_cover, has_any_issue."""
        async with self.get_db() as db:
            async with db.execute(
                """
                SELECT COALESCE(NULLIF(album_artist, ''), MIN(artist)) as artist,
                       album, artist_norm, album_norm,
                       MIN(filepath) as sample_filepath,
                       COUNT(*) as track_count,
                       SUM(COALESCE(size, 0)) as total_size,
                       MAX(total_tracks) as total_tracks,
                       MAX(ext) as ext,
                       MAX(bitrate) as bitrate,
                       MAX(has_cover) as has_cover,
                       MAX(year) as year,
                       SUM(issue_missing_meta + issue_dirty_tags + issue_naming
                           + issue_duplicate + issue_misfiled) as issue_count,
                       SUM(lyrics_synced) as tracks_synced_lyrics,
                       SUM(lyrics_plain)  as tracks_plain_lyrics
                FROM library_index 
                WHERE user_id = ? 
                  AND LOWER(album_norm) NOT IN ('exploretracks', 'explore')
                  AND LOWER(album) NOT IN ('explore tracks', 'explore')
                GROUP BY user_id, artist_norm, album_norm
                ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE
                """,
                (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def query_all_album_disc_track_counts(self, user_id: str) -> List[Dict[str, Any]]:
        """Return disc counters for every album in one query (avoids album-list N+1)."""
        async with self.get_db() as db:
            async with db.execute(
                """
                SELECT artist_norm, album_norm, disc_num, COUNT(*) as track_count,
                       MAX(track_num) as max_track_num, MAX(total_tracks) as max_total_tracks
                FROM library_index WHERE user_id = ?
                GROUP BY artist_norm, album_norm, disc_num
                """,
                (user_id,),
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def query_album_disc_track_counts(
        self, user_id: str, artist_norm: str, album_norm: str
    ) -> List[Dict[str, Any]]:
        """Return track count and max track number per disc for multi-disc total track calculation."""
        async with self.get_db() as db:
            async with db.execute(
                """
                SELECT disc_num, COUNT(*) as track_count, MAX(track_num) as max_track_num, MAX(total_tracks) as max_total_tracks
                FROM library_index
                WHERE user_id = ? AND artist_norm = ? AND album_norm = ?
                GROUP BY disc_num
                """,
                (user_id, artist_norm, album_norm)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_library_index_stats(self, user_id: str) -> Dict[str, Any]:
        """Return summary statistics for the user's library index."""
        async with self.get_db() as db:
            async with db.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN track_mbid IS NOT NULL THEN 1 ELSE 0 END) as with_mbid, "
                "MAX(scanned_at) as last_scan "
                "FROM library_index WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else {"total": 0, "with_mbid": 0, "last_scan": None}

    async def invalidate_user_caches(self, user_id: str) -> None:
        """Remove all library_cache entries for a user so sub-pages re-read from library_index."""
        async with self.get_db() as db:
            await db.execute(
                "DELETE FROM library_cache WHERE key LIKE ?",
                (f"%_{user_id}",)
            )
            await db.commit()
        async with self.mem_cache_lock:
            stale = [k for k in self.mem_cache if k.endswith(f"_{user_id}")]
            for k in stale:
                self.mem_cache.pop(k, None)

    async def mark_instrumental(self, filepath: str, user_id: str):
        async with self.get_db() as db:
            await db.execute(
                "INSERT OR REPLACE INTO instrumental_overrides (filepath, user_id) VALUES (?, ?)",
                (filepath, user_id)
            )
            await db.commit()

    async def unmark_instrumental(self, filepath: str, user_id: str):
        async with self.get_db() as db:
            await db.execute(
                "DELETE FROM instrumental_overrides WHERE filepath = ? AND user_id = ?",
                (filepath, user_id)
            )
            await db.commit()

    async def get_instrumental_overrides(self, user_id: str) -> set:
        async with self.get_db() as db:
            async with db.execute(
                "SELECT filepath FROM instrumental_overrides WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return {row["filepath"] for row in rows}
