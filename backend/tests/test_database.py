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
