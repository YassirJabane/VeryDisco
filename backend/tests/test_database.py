import pytest
from backend.app.database import Database

@pytest.mark.asyncio
async def test_database_runs_and_tracks(test_db: Database):
    # 1. Create a run
    run_id = await test_db.create_run(status="running")
    assert run_id == 1
    
    # 2. Add a track
    track_id = await test_db.add_track(
        run_id=run_id,
        artist="Justice",
        title="Genesis",
        status="pending"
    )
    assert track_id == 1

    # 3. Verify tracks for run
    tracks = await test_db.get_tracks_for_run(run_id)
    assert len(tracks) == 1
    assert tracks[0]["artist"] == "Justice"
    assert tracks[0]["title"] == "Genesis"
    assert tracks[0]["status"] == "pending"

    # 4. Update track status
    await test_db.update_track(
        track_id=track_id,
        status="downloaded",
        filename="Justice - Genesis.mp3",
        lyrics_status="synced",
        bitrate=320,
        size=8000000
    )
    
    tracks_updated = await test_db.get_tracks_for_run(run_id)
    assert tracks_updated[0]["status"] == "downloaded"
    assert tracks_updated[0]["filename"] == "Justice - Genesis.mp3"
    assert tracks_updated[0]["lyrics_status"] == "synced"
    assert tracks_updated[0]["bitrate"] == 320
    assert tracks_updated[0]["size"] == 8000000

    # 5. Update run summary stats
    await test_db.update_run(
        run_id=run_id,
        status="completed",
        tracks_found=1,
        tracks_downloaded=1,
        tracks_skipped=0,
        tracks_failed=0
    )

    latest_run = await test_db.get_latest_run()
    assert latest_run is not None
    assert latest_run["status"] == "completed"
    assert latest_run["tracks_found"] == 1
    assert latest_run["tracks_downloaded"] == 1

@pytest.mark.asyncio
async def test_database_logs(test_db: Database):
    # Add logs
    await test_db.add_log(level="INFO", message="Starting system test")
    await test_db.add_log(level="ERROR", message="An error occurred", run_id=5)

    logs = await test_db.get_logs(limit=10)
    assert len(logs) == 2
    # Verify order is chronological
    assert logs[0]["message"] == "Starting system test"
    assert logs[1]["message"] == "An error occurred"
    assert logs[1]["run_id"] == 5


@pytest.mark.asyncio
async def test_expired_disk_cache_is_not_rehydrated(test_db: Database):
    await test_db.set_cache("audit", {"private": True})
    async with test_db.get_db() as conn:
        await conn.execute("UPDATE library_cache SET updated_at = datetime('now', '-2 hours') WHERE key = 'audit'")
        await conn.commit()
    test_db.mem_cache.clear()
    assert await test_db.get_cache("audit") is None


@pytest.mark.asyncio
async def test_starred_track_status_is_per_user(test_db: Database):
    await test_db.mark_starred_track_processed("shared-track", "Artist", "Title", "alice")
    assert await test_db.is_starred_track_processed("shared-track", "alice")
    assert not await test_db.is_starred_track_processed("shared-track", "bob")
    await test_db.mark_starred_track_processed("shared-track", "Artist", "Title", "bob")
    assert await test_db.is_starred_track_processed("shared-track", "alice")
    assert await test_db.is_starred_track_processed("shared-track", "bob")


@pytest.mark.asyncio
async def test_provider_cache_and_incremental_acoustid_candidates(test_db: Database):
    await test_db.set_provider_cache("musicbrainz:test", {"ok": True}, 60)
    assert await test_db.get_provider_cache("musicbrainz:test") == {"ok": True}
    row = {
        "user_id": "user", "filepath": "/music/track.mp3", "mtime": 1.0,
        "artist": "Artist", "album": "Album", "title": "Track", "track_num": 1,
        "total_tracks": 1, "disc_num": 1, "total_discs": 1, "year": "2020",
        "album_artist": "Artist", "duration": 10, "ext": "mp3", "bitrate": 320,
        "bit_depth": 0, "sample_rate": 44100, "track_mbid": None, "album_mbid": None,
        "lyrics_synced": 0, "lyrics_plain": 0, "has_cover": 0,
        "issue_missing_meta": 0, "issue_dirty_tags": 0, "issue_dirty_reason": None,
        "issue_naming": 0, "issue_naming_expected": None, "issue_duplicate": 0,
        "issue_duplicate_of": None, "issue_misfiled": 0, "issue_misfiled_reason": None,
        "artist_norm": "artist", "album_norm": "album", "title_norm": "track",
        "size": 100, "mtime_ns": 10, "ctime_ns": 10, "device": 1, "inode": 2,
        "artists_json": '["Artist"]', "embedded_cover": 0, "has_comment": 0,
        "compilation": 0,
    }
    await test_db.upsert_library_index_batch([row])
    assert len(await test_db.get_acoustid_candidates("user")) == 1
    await test_db.save_acoustid_result("/music/track.mp3", "verified", None, 100, 10)
    assert await test_db.get_acoustid_candidates("user") == []
    row["mtime_ns"] = 11
    await test_db.upsert_library_index_batch([row])
    assert len(await test_db.get_acoustid_candidates("user")) == 1
