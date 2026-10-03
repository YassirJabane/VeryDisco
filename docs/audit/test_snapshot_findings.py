"""Isolated regression checks derived from the 2026-10-02 audit.

No real services, media or app lifespan are started.
"""

import asyncio
import importlib
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
import respx
import yaml

from backend.app.config import ConfigManager
from backend.app.database import Database


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump({
        "listenbrainz": {"username": "audit"},
        "slskd": {"base_url": "http://slskd.invalid"},
        "navidrome": {"url": "http://navidrome.invalid"},
        "paths": {"music_dir": str(tmp_path / "music" / "a"),
                  "navidrome_playlists_dir": str(tmp_path / "playlists")},
        "schedule": {"run_on_startup": False},
    }), encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("JWT_SECRET_FILE", str(tmp_path / "session.secret"))
    monkeypatch.delenv("JWT_SECRET", raising=False)
    original_init = Database.__init__
    monkeypatch.setattr(Database, "__init__", lambda self, *a, **k:
                        original_init(self, str(tmp_path / "unused.db")))
    app_module = importlib.import_module("backend.app.main")
    monkeypatch.setattr(app_module, "config_manager", ConfigManager(str(config_path)))
    root = app_module.config_manager.config.paths.music_dir
    monkeypatch.setattr(app_module, "db", SimpleNamespace(
        get_user_by_id=AsyncMock(return_value={"id": "a", "username": "a", "is_admin": 0, "music_dir": root,
                                               "playlist_dir": str(tmp_path / "playlists" / "a")}),
        add_album_download=AsyncMock(return_value=123),
        save_user_config=AsyncMock(), update_user_paths=AsyncMock(),
        save_user_features=AsyncMock(), get_all_file_metadata=AsyncMock(return_value={}),
        query_library_missing_art_albums=AsyncMock(return_value=[]),
    ))
    monkeypatch.setattr(app_module, "_active_tasks", {})
    monkeypatch.setattr(app_module, "_background_tasks", set())
    return app_module


@pytest_asyncio.fixture
async def client(isolated_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated_app.app),
                                base_url="http://audit.test") as client:
        yield client


def authenticate(client, app):
    from backend.app.auth import create_access_token
    client.cookies.set("vd_session", create_access_token(
        "a", "a", False, app.config_manager.config.auth.secret_key, timedelta(hours=1)))


@pytest.mark.asyncio
async def test_fresh_database_initializes_user_indexes(tmp_path):
    db = Database(str(tmp_path / "fresh.db"))
    await db.initialize()
    async with db.get_db() as conn:
        indexes = {row[1] for row in await (await conn.execute("PRAGMA index_list('runs')")).fetchall()}
    assert "idx_runs_user_source" in indexes


@pytest.mark.asyncio
async def test_legacy_starred_table_migrates_without_losing_rows(tmp_path):
    db = Database(str(tmp_path / "legacy.db"))
    async with db.get_db() as conn:
        await conn.execute("CREATE TABLE processed_starred_tracks (navidrome_track_id TEXT PRIMARY KEY, artist TEXT NOT NULL, title TEXT NOT NULL, user_id TEXT, processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await conn.execute("INSERT INTO processed_starred_tracks (navidrome_track_id, artist, title, user_id) VALUES ('track', 'Artist', 'Title', 'alice')")
        await conn.commit()
    await db.initialize()
    assert await db.is_starred_track_processed("track", "alice")
    await db.mark_starred_track_processed("track", "Artist", "Title", "bob")
    assert await db.is_starred_track_processed("track", "alice")


@pytest.mark.asyncio
async def test_anonymous_static_route_stays_inside_dist(client, isolated_app, tmp_path, monkeypatch):
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (tmp_path / "private.txt").write_text("AUDIT_ONLY_MARKER", encoding="utf-8")
    monkeypatch.setattr(isolated_app, "frontend_dir", str(dist))
    response = await client.get("/%2e%2e%2f%2e%2e%2fprivate.txt")
    assert "AUDIT_ONLY_MARKER" not in response.text


@pytest.mark.asyncio
async def test_anonymous_album_download_is_denied(client, isolated_app, monkeypatch):
    submitted = []
    def capture(coro, **kwargs):
        submitted.append(kwargs)
        coro.close()
    monkeypatch.setattr(isolated_app, "_create_tracked_task", capture)
    response = await client.post("/api/download/album", json={"artist": "Audit", "album": "Example"})
    assert response.status_code == 401
    assert not submitted
    isolated_app.db.add_album_download.assert_not_awaited()


@pytest.mark.asyncio
async def test_anonymous_purge_is_denied(client, isolated_app, monkeypatch):
    from backend.app.clients.navidrome import NavidromeClient
    isolated_app.db.purge_pinned_artists = AsyncMock()
    monkeypatch.setattr(NavidromeClient, "trigger_scan", AsyncMock())
    response = await client.post("/api/pinned_artists/purge")
    assert response.status_code == 401
    isolated_app.db.purge_pinned_artists.assert_not_awaited()


@pytest.mark.asyncio
async def test_art_fetch_rejects_internal_url(client, isolated_app, monkeypatch):
    authenticate(client, isolated_app)
    folder = Path(isolated_app.config_manager.config.paths.music_dir) / "album"
    folder.mkdir(parents=True)
    monkeypatch.setattr(isolated_app, "trigger_navidrome_scan_debounced", AsyncMock())
    # The HTTP response is mocked: no real internal host is contacted.
    with respx.mock:
        internal = respx.get("http://127.0.0.1:9999/internal").respond(
            200, text="MOCK_INTERNAL_DATA")
        response = await client.post("/api/library/art/save", json={
            "folder_path": str(folder), "url": "http://127.0.0.1:9999/internal"})
        assert response.status_code == 400 and not internal.called
    assert not (folder / "cover.jpg").exists()


@pytest.mark.asyncio
async def test_art_fetch_rejects_non_image_from_trusted_host(client, isolated_app):
    authenticate(client, isolated_app)
    folder = Path(isolated_app.config_manager.config.paths.music_dir) / "album"
    folder.mkdir(parents=True)
    with respx.mock:
        respx.get("https://cdn-images.dzcdn.net/example").respond(200, text="not an image")
        response = await client.post("/api/library/art/save", json={
            "folder_path": str(folder), "url": "https://cdn-images.dzcdn.net/example"})
    assert response.status_code == 400
    assert not (folder / "cover.jpg").exists()


@pytest.mark.asyncio
async def test_anonymous_library_migration_is_denied(client, isolated_app, monkeypatch):
    from backend.app.scripts import fix_existing_singles
    migration = AsyncMock()
    monkeypatch.setattr(fix_existing_singles, "process_music_directory", migration)
    response = await client.post("/api/library/fix-singles")
    await asyncio.sleep(0)
    assert response.status_code == 401
    migration.assert_not_awaited()


@pytest.mark.asyncio
async def test_path_check_rejects_sibling_user_file(client, isolated_app, tmp_path):
    authenticate(client, isolated_app)
    target = tmp_path / "music" / "ab" / "private.txt"
    target.parent.mkdir(parents=True)
    target.write_text("SIBLING_USER_MARKER", encoding="utf-8")
    response = await client.get("/api/library/tracks/stream", params={"filepath": str(target)})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_audio_stream_rejects_non_audio_inside_library(client, isolated_app):
    authenticate(client, isolated_app)
    target = Path(isolated_app.config_manager.config.paths.music_dir) / "secret.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("not audio", encoding="utf-8")
    response = await client.get("/api/library/tracks/stream", params={"filepath": str(target)})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_cross_origin_write_denied_even_with_session(client, isolated_app):
    authenticate(client, isolated_app)
    response = await client.post("/api/pinned_artists/purge", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_manual_sync_rejects_playlist_path_traversal(client, isolated_app):
    authenticate(client, isolated_app)
    response = await client.post("/api/trigger", params={"source": "../other"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_user_cannot_replace_own_authorization_root(client, isolated_app, tmp_path):
    authenticate(client, isolated_app)
    root = str(tmp_path)
    response = await client.put("/api/users/me/config", json={"music_dir": root})
    assert response.status_code == 403
    isolated_app.db.update_user_paths.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_outside_library_root_cannot_reauthorize_itself(client, isolated_app, tmp_path):
    authenticate(client, isolated_app)
    current = isolated_app.db.get_user_by_id.return_value.copy()
    current["music_dir"] = str(tmp_path)
    isolated_app.db.get_user_by_id.return_value = current
    response = await client.get("/api/tasks")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_user_cannot_cancel_another_users_task(client, isolated_app):
    authenticate(client, isolated_app)
    task = Mock()
    isolated_app._active_tasks["other-task"] = {"task": task, "metadata": {"user_id": "b"}}
    response = await client.post("/api/tasks/other-task/stop")
    assert response.status_code == 403
    task.cancel.assert_not_called()


@pytest.mark.asyncio
async def test_admin_role_is_read_from_current_database_record(client, isolated_app):
    authenticate(client, isolated_app)
    current = isolated_app.db.get_user_by_id.return_value.copy()
    current["is_admin"] = 1
    isolated_app.db.get_user_by_id.return_value = current
    isolated_app._active_tasks["other-task"] = {"task": Mock(), "type": "sync", "metadata": {"user_id": "b"}, "started_at": "now"}
    response = await client.get("/api/tasks")
    assert response.status_code == 200
    assert response.json()["tasks"][0]["id"] == "other-task"


@pytest.mark.asyncio
async def test_cors_rejects_arbitrary_credentialed_origin(client):
    response = await client.options("/api/users/me/config", headers={
        "Origin": "https://untrusted.example",
        "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Headers": "content-type",
    })
    assert response.headers.get("access-control-allow-origin") is None


def test_invalid_config_save_preserves_last_valid_config(isolated_app):
    manager = isolated_app.config_manager
    assert manager.is_configured
    valid, _ = manager.save({"raw_yaml": "listenbrainz: ["})
    assert not valid and manager.is_configured and manager.config is not None
    assert Path(manager.config_path).read_text(encoding="utf-8") != "listenbrainz: ["


def test_config_reload_preserves_generated_session_secret(isolated_app):
    manager = isolated_app.config_manager
    old_secret = manager.config.auth.secret_key
    manager.load()
    assert manager.config.auth.secret_key == old_secret


def test_rename_formats_title(isolated_app):
    assert isolated_app.format_rename_pattern("{Title}", {"title": "Example"}, ".mp3") == "Example.mp3"


def test_rename_rejects_traversal(isolated_app):
    with pytest.raises(ValueError, match="Invalid renaming pattern"):
        isolated_app.format_rename_pattern("../other/{Title}", {"title": "Example"}, ".mp3")


def test_global_naming_tokens_render(isolated_app):
    cfg = isolated_app.config_manager.config.filename
    result = isolated_app.format_rename_pattern(
        f"{cfg.folder_pattern}/{cfg.file_pattern}",
        {"artist": "Artist", "album": "Album", "title": "Song", "track_num": 1}, ".mp3")
    assert "Artist" in result and "Song" in result and "{artist}" not in result


@pytest.mark.asyncio
async def test_same_task_id_completion_keeps_still_running_task(isolated_app):
    release_first, release_second = asyncio.Event(), asyncio.Event()
    first = isolated_app._create_tracked_task(release_first.wait(), task_id="same")
    second = isolated_app._create_tracked_task(release_second.wait(), task_id="same")
    try:
        release_first.set()
        await first
        await asyncio.sleep(0)
        assert not second.done()
        assert isolated_app._active_tasks["same"]["task"] is second
    finally:
        release_second.set()
        await second


@pytest.mark.asyncio
async def test_automated_file_checks_use_user_scoped_service(isolated_app, tmp_path, monkeypatch):
    Path(isolated_app.config_manager.config.paths.music_dir).mkdir(parents=True)
    monkeypatch.setattr(isolated_app, "get_all_album_folders", Mock(return_value=[]))
    monkeypatch.setattr(isolated_app, "get_file_checks_cache_path", lambda user_id: tmp_path / f"{user_id}.json")
    assert await isolated_app.scan_missing_art_for_user("a") == []
    assert (tmp_path / "a.json").exists()


def test_album_listing_skips_unreadable_media_files(isolated_app, monkeypatch):
    music_dir = Path(isolated_app.config_manager.config.paths.music_dir)
    album_dir = music_dir / "Artist" / "Album"
    album_dir.mkdir(parents=True)
    broken = album_dir / "01 Broken.mp3"
    broken.write_bytes(b"broken")
    monkeypatch.setattr(
        isolated_app,
        "read_file_metadata_with_cache",
        lambda *args: (_ for _ in ()).throw(OSError("Input/output error")),
    )

    albums = isolated_app.get_all_album_folders(music_dir, {}, [])

    assert albums[0]["album"] == "Album"
    assert albums[0]["quality"] == "Unknown"


def test_download_locator_rejects_unrelated_recent_audio(tmp_path):
    from backend.app.sync import find_downloaded_file
    unrelated = tmp_path / "Completely Different Song.mp3"
    unrelated.write_bytes(b"AUDIT_FAKE_AUDIO")
    assert find_downloaded_file(str(tmp_path), "Expected Song.flac", 9000000) is None


@pytest.mark.asyncio
async def test_scheduler_does_not_mark_playlist_seen_when_sync_is_skipped(isolated_app, tmp_path, monkeypatch):
    from backend.app.clients.listenbrainz import ListenBrainzClient
    from backend.app.scheduler import SchedulerManager
    import backend.app.sync as sync_module
    fake_db = SimpleNamespace(db_path=str(tmp_path / "scheduler.db"),
                              list_users=AsyncMock(return_value=[]), create_run=AsyncMock())
    manager = SchedulerManager(fake_db)
    monkeypatch.setattr(ListenBrainzClient, "resolve_playlist_mbid", AsyncMock(return_value="audit-mbid"))
    monkeypatch.setattr(ListenBrainzClient, "get_playlist_tracks", AsyncMock(return_value=[
        {"artist": "Audit", "title": "Example", "album": "Example"}]))
    lock = asyncio.Lock()
    await lock.acquire()
    monkeypatch.setattr(sync_module, "_sync_lock", lock)
    try:
        await manager.check_and_run_sync(isolated_app.config_manager.config)
        pending = list(isolated_app._background_tasks)
        if pending:
            await asyncio.gather(*pending)
        fake_db.create_run.assert_not_awaited()
        state_path = tmp_path / "app_state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text())
            assert "global:weekly-exploration" not in state.get("last_mbids", {})
    finally:
        lock.release()


@pytest.mark.asyncio
async def test_scheduler_marks_playlist_seen_only_after_completed_sync(isolated_app, tmp_path, monkeypatch):
    from backend.app.clients.listenbrainz import ListenBrainzClient
    from backend.app.scheduler import SchedulerManager
    import backend.app.sync as sync_module
    fake_db = SimpleNamespace(db_path=str(tmp_path / "scheduler.db"), list_users=AsyncMock(return_value=[]))
    manager = SchedulerManager(fake_db)
    monkeypatch.setattr(ListenBrainzClient, "resolve_playlist_mbid", AsyncMock(return_value="new-mbid"))
    monkeypatch.setattr(ListenBrainzClient, "get_playlist_tracks", AsyncMock(return_value=[
        {"artist": "Audit", "title": "Example", "album": "Example"}]))
    sync = AsyncMock(return_value=True)
    monkeypatch.setattr(sync_module, "run_sync", sync)
    await manager.check_and_run_sync(isolated_app.config_manager.config)
    sync.assert_awaited()
    state = json.loads((tmp_path / "app_state.json").read_text())
    assert state["last_mbids"]["global:weekly-exploration"] == "new-mbid"
