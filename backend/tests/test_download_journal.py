import pytest


@pytest.mark.asyncio
async def test_single_track_download_journal_keeps_queries_attempts_and_outcome(test_db):
    job_id = await test_db.create_download_job("Artist", "Track", "Album", "user-1")
    await test_db.update_download_job(
        job_id, "searching", query_text="Artist Album Track *", query_strategy=1
    )
    attempt_id = await test_db.add_download_attempt(
        job_id, 1, "Artist Album Track *", "peer", "Artist - Track.flac", 1234, "requested"
    )
    await test_db.update_download_attempt(attempt_id, "rejected", "AcoustID metadata mismatch", "transfer-1")
    await test_db.update_download_job(job_id, "failed", error_reason="No acceptable candidate completed")

    jobs = await test_db.list_download_jobs("user-1")
    attempts = await test_db.get_download_attempts(job_id)

    assert jobs[0]["status"] == "failed"
    assert jobs[0]["query_strategy"] == 1
    assert jobs[0]["query_text"] == "Artist Album Track *"
    assert jobs[0]["completed_at"]
    assert attempts == [{
        **attempts[0],
        "job_id": job_id,
        "attempt_number": 1,
        "username": "peer",
        "remote_filename": "Artist - Track.flac",
        "remote_size": 1234,
        "transfer_id": "transfer-1",
        "status": "rejected",
        "reason": "AcoustID metadata mismatch",
    }]
