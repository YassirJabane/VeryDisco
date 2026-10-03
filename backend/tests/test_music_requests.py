import pytest
from fastapi import HTTPException

from backend.app.database import Database


@pytest.mark.asyncio
async def test_music_requests_are_deduplicated_private_and_decided_once(tmp_path):
    db = Database(str(tmp_path / "requests.db"))
    await db.initialize()
    async with db.get_db() as conn:
        await conn.execute("INSERT INTO users (id, username) VALUES ('one', 'one'), ('two', 'two')")
        await conn.commit()

    first = await db.add_music_request("one", "track", "Artist", "Song", "Album")
    assert await db.add_music_request("one", "track", "Artist", "Song", "Album") == first
    other = await db.add_music_request("two", "track", "Artist", "Song", "Album")
    assert other != first
    assert [item["id"] for item in await db.list_music_requests("one")] == [first]
    assert len(await db.list_music_requests("one", is_admin=True)) == 2
    assert await db.transition_music_request(first, "pending", "queued") is True
    assert await db.transition_music_request(first, "pending", "declined") is False
    assert await db.transition_music_request(first, "queued", "completed") is True
    assert (await db.get_music_request(first))["status"] == "completed"


@pytest.mark.asyncio
async def test_request_routes_enforce_admin_and_owner(tmp_path, monkeypatch):
    from backend.app import main, auth

    db = Database(str(tmp_path / "routes.db"))
    await db.initialize()
    async with db.get_db() as conn:
        await conn.execute("INSERT INTO users (id, username) VALUES ('one', 'one'), ('two', 'two')")
        await conn.commit()
    monkeypatch.setattr(main, "db", db)
    current = {"id": "one", "is_admin": False}

    async def get_user(request):
        return current

    monkeypatch.setattr(auth, "get_current_user", get_user)
    item = await main.create_music_request(main.MusicRequestInput(kind="track", artist="A", title="T"), None)
    assert item["status"] == "pending"
    current = {"id": "two", "is_admin": False}
    assert (await main.list_music_requests(None))["requests"] == []
    with pytest.raises(HTTPException) as error:
        await main.approve_music_request(item["id"], None)
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        await main.decline_music_request(item["id"], None)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_approved_track_request_records_actual_download_result(tmp_path, monkeypatch):
    from backend.app import main, auth, album_sync

    db = Database(str(tmp_path / "approved.db"))
    await db.initialize()
    async with db.get_db() as conn:
        await conn.execute("INSERT INTO users (id, username) VALUES ('admin', 'admin')")
        await conn.commit()
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main.config_manager, "is_configured", True)
    monkeypatch.setattr(main.config_manager, "config", object())

    async def admin_user(request):
        return {"id": "admin", "is_admin": True}

    async def fake_download(*args, **kwargs):
        return False

    captured = []

    def capture_task(coro, **kwargs):
        captured.append(coro)

    monkeypatch.setattr(auth, "get_current_user", admin_user)
    monkeypatch.setattr(album_sync, "download_single_track_task", fake_download)
    monkeypatch.setattr(main, "_create_tracked_task", capture_task)
    item = await main.create_music_request(main.MusicRequestInput(kind="track", artist="A", title="T"), None)
    assert item["status"] == "queued"
    assert len(captured) == 1
    await captured[0]
    assert (await db.get_music_request(item["id"]))["status"] == "failed"
