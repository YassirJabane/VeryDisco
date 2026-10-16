"""Release regressions: account boundaries, recording identity, and durable retries."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from backend.app.config import AppConfig
from backend.app.integrations import listenbrainz_credentials
from backend.app import main


@pytest.fixture
def audit_app(tmp_path, monkeypatch):
    cfg = AppConfig(
        listenbrainz={"username": "global-admin", "token": "global-token"},
        slskd={"base_url": "http://slskd.invalid"},
        paths={"music_dir": str(tmp_path / "music"), "navidrome_playlists_dir": str(tmp_path / "playlists")},
    )
    monkeypatch.setattr(main, "config_manager", SimpleNamespace(config=cfg, is_configured=True))
    monkeypatch.setattr("backend.app.auth.get_current_user", AsyncMock(return_value={"id": "alice", "is_admin": False}))
    monkeypatch.setattr(main, "_active_tasks", {})
    monkeypatch.setattr(main, "db", SimpleNamespace(
        get_user_by_id=AsyncMock(return_value={"id": "alice", "is_admin": False}),
        get_album_tracklists=AsyncMock(return_value={}),
        query_library_index_for_user=AsyncMock(return_value=[]),
        get_provider_cache=AsyncMock(return_value=None),
        set_provider_cache=AsyncMock(),
    ))
    return main


@pytest.mark.parametrize(("user", "personal", "expected"), [
    ({"is_admin": False}, {}, ("", "")),
    ({"is_admin": False}, {"lb_username": "alice"}, ("alice", "")),
    ({"is_admin": True}, {}, ("global-admin", "global-token")),
    ({"is_admin": True}, {"lb_username": "other-account"}, ("other-account", "")),
    ({"is_admin": False}, {"lb_username": "alice", "lb_token": "alice-token"}, ("alice", "alice-token")),
])
def test_background_and_http_share_account_boundaries(audit_app, user, personal, expected):
    assert listenbrainz_credentials(audit_app.config_manager.config, user, personal) == expected


@pytest.mark.asyncio
async def test_batch_check_uses_recording_identity_and_track_artist(audit_app):
    row = {"artist": "Tyler, The Creator", "artist_norm": "variousartists", "album": "Flower Boy",
           "album_norm": "flowerboy", "title_norm": "seeyouagain", "title": "See You Again",
           "track_mbid": "original-recording", "filepath": "original.flac", "ext": "flac"}
    audit_app.db.query_library_index_for_user.return_value = [row]
    results = await audit_app.batch_check_existence([
        audit_app.BatchCheckItem(artist="Tyler, The Creator", title="See You Again", track_mbid="original-recording"),
        audit_app.BatchCheckItem(artist="Tyler, The Creator", title="See You Again", track_mbid="remix-recording"),
        audit_app.BatchCheckItem(artist="Tyler, The Creator", title="See You Again", album="Flower Boy"),
    ], None)
    assert [item["exists"] for item in results] == [True, False, True]


def test_batch_album_title_fallback_does_not_use_another_artist():
    other = {"artist_norm": "anotherartist", "album_norm": "greatesthits"}
    assert main._matching_album_rows({}, {"greatesthits": [other]}, "abba", "greatesthits") == []


@pytest.mark.asyncio
async def test_retry_preserves_explore_and_replacement_context(audit_app, test_db, monkeypatch):
    monkeypatch.setattr(audit_app, "db", test_db)
    options = {"force": True, "is_explore": True, "replace_path_override": "/own/song.mp3"}
    job_id = await test_db.create_download_job("Artist", "Track", "Album", "alice", request=options)
    await test_db.update_download_job(job_id, "failed")
    captured = {}

    def capture(coro, **kwargs):
        captured.update(coro.cr_frame.f_locals)
        if "function" in captured and "args" in captured:
            import inspect
            captured.update(inspect.signature(captured["function"]).bind(*captured["args"], **captured["kwargs"]).arguments)
        coro.close()

    monkeypatch.setattr(audit_app, "_create_tracked_task", capture)
    verify = AsyncMock()
    monkeypatch.setattr(audit_app, "verify_user_access_to_path", verify)
    await audit_app.retry_track_download(job_id, None)
    assert {key: captured[key] for key in options} == options
    verify.assert_awaited_once_with("alice", "/own/song.mp3")
    assert await test_db.get_download_job(job_id, "bob") is None
    await test_db.update_download_job(job_id, "completed")
    with pytest.raises(HTTPException) as error:
        await audit_app.retry_track_download(job_id, None)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_cancelling_queued_track_marks_journal_retryable(audit_app, test_db, monkeypatch):
    from backend.app import album_sync
    semaphore = asyncio.Semaphore(0)
    monkeypatch.setattr(album_sync, "_single_track_semaphore", semaphore)
    created = asyncio.Event()
    original_create = test_db.create_download_job

    async def create(*args, **kwargs):
        job_id = await original_create(*args, **kwargs)
        created.set()
        return job_id

    monkeypatch.setattr(test_db, "create_download_job", create)
    task = asyncio.create_task(album_sync.download_single_track_task(
        "Artist", "Track", "Album", audit_app.config_manager.config, test_db, user_id="alice", is_explore=True,
    ))
    await asyncio.wait_for(created.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    job = (await test_db.list_download_jobs("alice"))[0]
    assert job["status"] == "interrupted"
    assert json.loads(job["request_json"])["is_explore"] is True


@pytest.mark.asyncio
async def test_playlist_read_never_overwrites_edited_tracks(audit_app, monkeypatch):
    tracks = [{"artist": "Local artist", "title": "Added locally"}]
    item = {"id": 1, "provider": "spotify", "provider_id": "provider-id", "source": "spotify-source",
            "name": "Edited name", "description": "Local description", "artwork_url": "", "tracks": tracks}
    audit_app.db.list_imported_playlists = AsyncMock(return_value=[item])
    audit_app.db.update_imported_playlist = AsyncMock()
    monkeypatch.setattr(audit_app, "_spotify_playlist_metadata_checked", set())
    monkeypatch.setattr("backend.app.clients.spotify.SpotifyClient.get_playlist", AsyncMock(return_value={
        "name": "Provider name", "artwork_url": "https://example.com/cover.jpg", "tracks": [],
    }))
    monkeypatch.setattr("backend.app.sync.save_playlist_artwork_and_m3u", AsyncMock())
    monkeypatch.setattr(audit_app, "trigger_navidrome_scan_debounced", AsyncMock())
    result = await audit_app.list_imported_playlists(None)
    assert result[0]["tracks"] == tracks
    assert result[0]["name"] == "Edited name"
    assert result[0]["artwork_url"] == "https://example.com/cover.jpg"


@pytest.mark.asyncio
async def test_failed_delete_keeps_library_catalog_row(audit_app, monkeypatch):
    monkeypatch.setattr(audit_app, "_resolve_duplicates_sync", Mock(return_value=(1, [{"path": "denied.mp3", "error": "Access denied"}])))
    audit_app.db.invalidate_library_paths = AsyncMock()
    audit_app.db.invalidate_user_caches = AsyncMock()
    monkeypatch.setattr(audit_app, "trigger_navidrome_scan_debounced", AsyncMock())
    await audit_app.resolve_duplicates_endpoint(audit_app.ResolveDuplicatesRequest(paths_to_delete=["deleted.mp3", "denied.mp3"]), None)
    audit_app.db.invalidate_library_paths.assert_awaited_once_with("alice", ["deleted.mp3"])


@pytest.mark.asyncio
async def test_no_mbid_is_not_reported_as_saved_feedback(monkeypatch):
    from backend.app.clients.listenbrainz import ListenBrainzClient
    client = ListenBrainzClient("alice", "weekly-jams", "token")
    monkeypatch.setattr(client, "resolve_recording_mbid", AsyncMock(return_value=None))
    assert await client.submit_feedback("Artist", "Unknown Track") is False


@pytest.mark.asyncio
async def test_switching_off_all_playlists_does_not_restore_global_selection(audit_app, tmp_path, monkeypatch):
    from backend.app.scheduler import SchedulerManager
    from backend.app.clients.listenbrainz import ListenBrainzClient
    fake_db = SimpleNamespace(
        db_path=str(tmp_path / "scheduler.db"),
        list_users=AsyncMock(return_value=[{"id": "alice", "username": "alice"}]),
        get_user_config=AsyncMock(return_value={"lb_username": "alice", "active_playlists": []}),
    )
    manager = SchedulerManager(fake_db)
    resolve = AsyncMock()
    monkeypatch.setattr(ListenBrainzClient, "resolve_playlist_mbid", resolve)
    await manager.check_and_run_sync(audit_app.config_manager.config)
    resolve.assert_not_awaited()
    assert manager.scheduler.get_jobs() == []


def test_quality_check_accepts_missing_audio_properties(audit_app):
    from backend.app.sync import check_quality_status
    assert check_quality_status("flac", None, None, None, audit_app.config_manager.config) == "worse"


@pytest.mark.asyncio
@pytest.mark.parametrize("tagging_fails", [False, True])
async def test_tagging_failure_never_overwrites_existing_audio(tmp_path, monkeypatch, tagging_fails):
    from pathlib import Path
    from backend.app.album_sync import _publish_tagged_track
    source, destination = tmp_path / "raw.mp3", tmp_path / "library.mp3"
    source.write_bytes(b"new transfer")
    destination.write_bytes(b"previous library file")

    def tag(file_path, **kwargs):
        if tagging_fails:
            raise OSError("Failed to tag")
        Path(file_path).write_bytes(b"validated and tagged")

    monkeypatch.setattr("backend.app.album_sync.embed_metadata", tag)
    if tagging_fails:
        with pytest.raises(OSError):
            await _publish_tagged_track(source, destination, {}, None)
        assert destination.read_bytes() == b"previous library file"
        assert source.read_bytes() == b"new transfer"
    else:
        await _publish_tagged_track(source, destination, {}, "lyrics")
        assert destination.read_bytes() == b"validated and tagged"
        assert destination.with_suffix(".lrc").read_text() == "lyrics"
        assert not source.exists()
    assert not list(tmp_path.glob(".verydisco-*"))


@pytest.mark.asyncio
async def test_analytics_does_not_invent_history_or_use_admin_credentials(audit_app, monkeypatch):
    audit_app.db.get_user_config = AsyncMock(return_value={})
    audit_app.config_manager.config.navidrome.url = "http://navidrome.invalid"
    audit_app.config_manager.config.navidrome.username = "admin"
    audit_app.config_manager.config.navidrome.password = "admin-secret"
    get_history = AsyncMock()
    get_top = AsyncMock()
    monkeypatch.setattr("backend.app.clients.navidrome.NavidromeClient.get_history", get_history)
    monkeypatch.setattr("backend.app.clients.navidrome.NavidromeClient.get_top_albums", get_top)
    response = await audit_app.get_stats_summary_endpoint(None)
    get_history.assert_not_awaited()
    get_top.assert_not_awaited()
    assert response["library_stats"]["listened_tracks"] is None
    assert response["navidrome_history"]["available"] is False


@pytest.mark.asyncio
async def test_existing_download_journal_migrates_without_losing_history(tmp_path):
    from backend.app.database import Database
    legacy = Database(str(tmp_path / "legacy.db"))
    # Reproduce the exact pre-release table by removing only the new column.
    await legacy.initialize()
    job_id = await legacy.create_download_job("Artist", "Preserved Track", user_id="alice")
    async with legacy.get_db() as conn:
        await conn.execute("ALTER TABLE download_jobs DROP COLUMN request_json")
        await conn.commit()
    await legacy.initialize()
    job = await legacy.get_download_job(job_id, "alice")
    assert job["title"] == "Preserved Track"
    assert json.loads(job["request_json"]) == {}


@pytest.mark.asyncio
async def test_user_can_stop_own_task_when_artist_contains_slash(audit_app):
    import httpx
    from urllib.parse import quote
    task_id = "track:alice:AC/DC:Thunderstruck:The Razors Edge"
    task = asyncio.create_task(asyncio.Event().wait())
    audit_app._active_tasks[task_id] = {"task": task, "metadata": {"user_id": "alice"}}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=audit_app.app), base_url="http://audit.test") as client:
            response = await client.post(f"/api/tasks/{quote(task_id, safe='')}/stop")
        assert response.status_code == 200
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_mbid_album_check_uses_official_total_instead_of_local_tags(audit_app):
    mbid = "11111111-2222-3333-4444-555555555555"
    audit_app.db.query_library_index_for_user.return_value = [
        {"artist_norm": "artist", "album_norm": "album", "album_mbid": mbid, "total_tracks": 6}
        for _ in range(6)
    ]
    audit_app.db.get_album_tracklists.return_value = {("artist", "album"): {"total_tracks": 9}}
    result = await audit_app.batch_check_existence([audit_app.BatchCheckItem(artist="Artist", title="Album", album_id=mbid)], None)
    assert result[0]["status"] == "partial"
    audit_app.db.get_album_tracklists.return_value = {}
    result = await audit_app.batch_check_existence([audit_app.BatchCheckItem(artist="Artist", title="Album", album_id=mbid)], None)
    assert result[0]["status"] != "full"


@pytest.mark.asyncio
async def test_album_completion_counts_tracks_not_copies(audit_app):
    rows = [{"artist_norm": "drake", "album_norm": "forallthedogsscaryhoursedition",
        "title": f"Track {i}", "title_norm": f"track{i}", "disc_num": 1, "track_num": i,
        "ext": "mp3", "bitrate": 320, "filepath": f"/old/{i}.mp3"} for i in range(1, 24)]
    audit_app.db.query_library_index_for_user.return_value = rows + [{**r, 'filepath': r['filepath'].replace('/old/', '/new/')} for r in rows]
    result = await audit_app.batch_check_existence([audit_app.BatchCheckItem(
        artist='Drake', title='For All The Dogs Scary Hours Edition', album_id=123, expected_tracks=29)], None)
    assert result[0]['status'] == 'partial'
    assert result[0]['matched_tracks'] == 23
    assert len(result[0]['tracks']) == 23


@pytest.mark.asyncio
async def test_album_checks_preserve_edition_identity(audit_app):
    audit_app.db.query_library_index_for_user.return_value = [{
        'artist_norm': 'drake', 'album_norm': 'forallthedogs', 'track_num': i,
        'title': f'Track {i}', 'ext': 'mp3', 'bitrate': 320} for i in range(1, 24)]
    result = await audit_app.batch_check_existence([audit_app.BatchCheckItem(
        artist='Drake', title='For All The Dogs (Scary Hours Edition)', album_id=123, expected_tracks=29)], None)
    assert result[0]['status'] == 'missing'
    audit_app.db.query_library_index_for_user.return_value = [{**r, 'album_norm': 'forallthedogsscaryhoursedition'}
        for r in audit_app.db.query_library_index_for_user.return_value]
    result = await audit_app.batch_check_existence([audit_app.BatchCheckItem(
        artist='Drake', title='For All The Dogs Scary Hours Edition', album_id=123, expected_tracks=29)], None)
    assert result[0]['status'] == 'partial'
    assert result[0]['matched_tracks'] == 23


@pytest.mark.asyncio
async def test_album_quality_uses_best_duplicate_and_does_not_claim_no_upgrade(audit_app, monkeypatch):
    monkeypatch.setattr('backend.app.sync.check_quality_status', lambda ext, bitrate, *args: 'worse' if bitrate < 320 else 'same')
    rows = [{'artist_norm': 'artist', 'album_norm': 'album', 'track_num': 1, 'title': 'Song', 'ext': 'mp3', 'bitrate': 128}]
    audit_app.db.query_library_index_for_user.return_value = rows
    item = audit_app.BatchCheckItem(artist='Artist', title='Album', album_id=123, expected_tracks=1)
    result = await audit_app.batch_check_existence([item], None)
    assert result[0]['status'] == 'full'
    assert result[0]['upgrade_available'] is True
    rows.append({**rows[0], 'bitrate': 320})
    result = await audit_app.batch_check_existence([item], None)
    assert result[0]['matched_tracks'] == 1
    assert result[0]['upgrade_available'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_available', [False, True])
async def test_direct_search_missing_total_cannot_use_a_stale_base_edition_total(audit_app, monkeypatch, provider_available):
    from backend.app.clients.deezer import DeezerClient
    audit_app.db.query_library_index_for_user.return_value = [{
        'artist_norm': 'drake', 'album_norm': 'forallthedogsscaryhoursedition',
        'title': f'Track {i}', 'track_num': i, 'ext': 'mp3', 'bitrate': 320,
    } for i in range(1, 24)]
    # A previous mistag/migration may leave 23 tracks and an old cached total of 23.
    audit_app.db.get_album_tracklists.return_value = {
        ('drake', 'forallthedogsscaryhoursedition'): {'total_tracks': 23, 'release_mbid': 'base-release'}}
    lookup = AsyncMock(return_value={'id': 200, 'title': 'For All The Dogs Scary Hours Edition', 'nb_tracks': 29} if provider_available else None)
    monkeypatch.setattr(DeezerClient, 'get_album_metadata', lookup)
    result = await audit_app.batch_check_existence([audit_app.BatchCheckItem(
        artist='Drake', title='For All The Dogs Scary Hours Edition', album_id=200)], None)
    assert result[0]['status'] == 'partial'
    assert result[0]['matched_tracks'] == 23
    assert result[0]['expected_tracks'] == (29 if provider_available else 0)
    lookup.assert_awaited_once_with(200)


@pytest.mark.asyncio
async def test_requested_mbid_does_not_inherit_another_release_total(audit_app):
    mbid = '11111111-2222-3333-4444-555555555555'
    audit_app.db.query_library_index_for_user.return_value = [{
        'artist_norm': 'artist', 'album_norm': 'albumdeluxe', 'album_mbid': mbid,
        'title': f'Track {i}', 'track_num': i} for i in range(1, 24)]
    audit_app.db.get_album_tracklists.return_value = {
        ('artist', 'albumdeluxe'): {'release_mbid': 'other-release', 'total_tracks': 23}}
    result = await audit_app.batch_check_existence([audit_app.BatchCheckItem(
        artist='Artist', title='Album Deluxe', album_id=mbid)], None)
    assert result[0]['status'] == 'partial'
    assert result[0]['expected_tracks'] == 0
    audit_app.db.get_album_tracklists.return_value[('artist', 'albumdeluxe')] = {'release_mbid': mbid, 'total_tracks': 29}
    result = await audit_app.batch_check_existence([audit_app.BatchCheckItem(
        artist='Artist', title='Album Deluxe', album_id=mbid)], None)
    assert result[0]['status'] == 'partial'
    assert result[0]['expected_tracks'] == 29
