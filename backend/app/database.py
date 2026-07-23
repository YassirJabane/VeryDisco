import os
import aiosqlite
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
        try:
            conn = await aiosqlite.connect(self.db_path)
        except Exception:
            # Fallback if primary db_path is not writable (e.g., permission error in docker)
            fallback = os.path.join(os.getcwd(), "verydisco.db")
            conn = await aiosqlite.connect(fallback)
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
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
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
                year TEXT
            );
            """)
            
            # Create essential indexes for fast status polling and query performance
            await db.execute("CREATE INDEX IF NOT EXISTS idx_tracks_run_id ON tracks(run_id);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_runs_user_source ON runs(user_id, source);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_album_downloads_user_status ON album_downloads(user_id, status);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_logs_run_id ON logs(run_id);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_file_metadata_cache_mtime ON file_metadata_cache(mtime);")
            await db.commit()
            
            # Migrate year column to file_metadata_cache
            try:
                await db.execute("SELECT year FROM file_metadata_cache LIMIT 1")
            except Exception:
                try:
                    await db.execute("ALTER TABLE file_metadata_cache ADD COLUMN year TEXT")
                except Exception:
                    pass

            # Add user_id column to processed_starred_tracks if upgrading from old schema
            try:
                await db.execute("SELECT user_id FROM processed_starred_tracks LIMIT 1")
            except Exception:
                try:
                    await db.execute("ALTER TABLE processed_starred_tracks ADD COLUMN user_id TEXT")
                    await db.commit()
                except Exception:
                    pass

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
            # Add user_id to album_downloads for per-user tracking
            try:
                await db.execute("SELECT user_id FROM album_downloads LIMIT 1")
            except Exception:
                try:
                    await db.execute("ALTER TABLE album_downloads ADD COLUMN user_id TEXT")
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
                UNIQUE(user_id, filepath)
            );
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_li_user_album    ON library_index(user_id, album_norm);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_li_user_artist   ON library_index(user_id, artist_norm);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_li_track_mbid    ON library_index(track_mbid);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_li_album_mbid    ON library_index(album_mbid);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_li_issues        ON library_index(user_id, issue_dirty_tags, issue_missing_meta, issue_naming, issue_duplicate, issue_misfiled);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_li_lyrics        ON library_index(user_id, lyrics_synced, lyrics_plain);")
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

    async def add_album_download(self, artist: str, title: str, album: str) -> int:
        async with self.get_db() as conn:
            cursor = await conn.execute(
                "INSERT INTO album_downloads (artist, title, album, status) VALUES (?, ?, ?, 'pending')",
                (artist, title, album)
            )
            await conn.commit()
            return cursor.lastrowid

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
            async with db.execute("SELECT value FROM library_cache WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    try:
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
                (filepath, mtime, artist, album, title, track_num, total_tracks, quality_desc, bitrate, bit_depth, sample_rate, duration, year)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, entries)
            await db.commit()
        self.metadata_mem_cache = None

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
                    "SELECT 1 FROM processed_starred_tracks WHERE navidrome_track_id = ?", (track_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    return row is not None

    async def mark_starred_track_processed(self, track_id: str, artist: str, title: str, user_id: Optional[str] = None):
        async with self.get_db() as db:
            await db.execute(
                "INSERT OR REPLACE INTO processed_starred_tracks (navidrome_track_id, artist, title, user_id) VALUES (?, ?, ?, ?)",
                (track_id, artist, title, user_id)
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
        self, artist: str, title: str, album: str, user_id: Optional[str] = None
    ) -> int:
        async with self.get_db() as conn:
            cursor = await conn.execute(
                "INSERT INTO album_downloads (artist, title, album, status, user_id) VALUES (?, ?, ?, 'pending', ?)",
                (artist, title, album, user_id)
            )
            await conn.commit()
            return cursor.lastrowid

    async def save_acoustid_result(self, file_path: str, status: str, reason: Optional[str] = None):
        async with self.get_db() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO acoustid_results (file_path, status, reason, scanned_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                (file_path, status, reason)
            )
            await conn.commit()

    async def get_acoustid_results(self) -> list:
        async with self.get_db() as conn:
            cursor = await conn.execute("SELECT * FROM acoustid_results")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

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
        ]
        placeholders = ", ".join(["?"] * len(cols))
        col_str = ", ".join(cols)
        sql = f"INSERT OR REPLACE INTO library_index ({col_str}) VALUES ({placeholders})"
        rows = [tuple(e.get(c) for c in cols) for e in entries]
        async with self.get_db() as db:
            await db.executemany(sql, rows)
            await db.commit()

    async def mark_track_mbid(self, filepath: str, track_mbid: Optional[str], album_mbid: Optional[str]) -> None:
        """Update MBID columns for a single row after enrichment lookup."""
        from datetime import datetime
        async with self.get_db() as db:
            await db.execute(
                "UPDATE library_index SET track_mbid = ?, album_mbid = ?, mbid_enriched_at = ? WHERE filepath = ?",
                (track_mbid, album_mbid, datetime.utcnow().isoformat(), filepath)
            )
            await db.commit()

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
                SELECT artist, album, artist_norm, album_norm,
                       MIN(filepath) as sample_filepath,
                       COUNT(*) as track_count,
                       MAX(has_cover) as has_cover,
                       MAX(year) as year,
                       SUM(issue_missing_meta + issue_dirty_tags + issue_naming
                           + issue_duplicate + issue_misfiled) as issue_count,
                       SUM(lyrics_synced) as tracks_synced_lyrics,
                       SUM(lyrics_plain)  as tracks_plain_lyrics
                FROM library_index WHERE user_id = ?
                GROUP BY user_id, artist_norm, album_norm
                ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE
                """,
                (user_id,)
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
