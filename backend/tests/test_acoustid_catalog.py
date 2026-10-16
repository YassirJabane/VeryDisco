from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app import main
from backend.app.clients.acoustid import acoustid_client


async def add_file(db, path, *, user='alice', status=None, size=100, mtime=10, reason=''):
    async with db.get_db() as conn:
        await conn.execute('''INSERT INTO library_index
            (user_id, filepath, size, mtime_ns, mtime, artist, title, album)
            VALUES (?, ?, ?, ?, 1, 'Artist', 'Song', 'Album')''', (user, str(path), size, mtime))
        await conn.commit()
    if status:
        await db.save_acoustid_result(str(path), status, reason, size, mtime)


def configure(monkeypatch, db, root, playlists=None):
    playlists = playlists or root / 'custom-playlists'
    monkeypatch.setattr(main, 'db', db)
    monkeypatch.setattr(main, 'config_manager', SimpleNamespace(config=SimpleNamespace(
        paths=SimpleNamespace(music_dir=str(root), navidrome_playlists_dir=str(playlists)),
        acoustid=SimpleNamespace(api_key='test'))))
    monkeypatch.setattr(db, 'get_user_by_id', AsyncMock(return_value={
        'music_dir': str(root), 'playlist_dir': str(playlists)}))
    monkeypatch.setattr('backend.app.auth.get_current_user', AsyncMock(return_value={'id': 'alice'}))
    monkeypatch.setattr(main, '_active_tasks', {})
    monkeypatch.setattr(main, '_acoustid_recheck_locks', {})


@pytest.mark.asyncio
async def test_stats_and_remaining_share_scope_and_freshness(test_db, tmp_path, monkeypatch):
    root = tmp_path / 'music'
    playlists = root / 'custom-playlists'
    configure(monkeypatch, test_db, root, playlists)
    for folder in ['Explore', 'Explore Tracks', 'ExploreTracks', 'playlists', 'navidrome_playlists',
                   '.staging', '.staging_test', '.hidden', 'custom-playlists']:
        await add_file(test_db, root / folder / 'track.mp3', status='verified')
        await add_file(test_db, root / folder / 'missing.mp3')
    await add_file(test_db, root / 'verified.mp3', status='verified')
    await add_file(test_db, root / 'unknown.ogg', status='unknown', reason='No match found')
    await add_file(test_db, root / 'skipped.m4a', status='verified', reason='Skipped: decoder failed')
    await add_file(test_db, root / 'mismatch.flac', status='failed', reason='Different song')
    await add_file(test_db, root / 'new.mp4')
    await add_file(test_db, root / 'changed.mp3', status='verified')
    async with test_db.get_db() as conn:
        await conn.execute('UPDATE library_index SET mtime_ns=11 WHERE filepath=?', (str(root / 'changed.mp3'),))
        await conn.commit()
    # Historical results, shared roots and out-of-root catalog rows must not inflate totals.
    await test_db.save_acoustid_result(str(root / 'removed.mp3'), 'verified', '', 100, 10)
    await add_file(test_db, root / 'other-user.mp3', user='bob', status='verified')
    await add_file(test_db, tmp_path / 'outside.mp3', status='verified')
    await add_file(test_db, root / 'cover.jpg', status='verified')
    monkeypatch.setattr(main, 'get_file_checks_cache_path', lambda *_: pytest.fail('Legacy total cache must not be used'))
    stats = await main.get_acoustid_stats(Request({'type': 'http'}))
    assert {key: stats[key] for key in ['total', 'scanned', 'verified', 'failed', 'inconclusive', 'remaining']} == {
        'total': 6, 'scanned': 4, 'verified': 1, 'failed': 1, 'inconclusive': 2, 'remaining': 2}
    candidates = await test_db.get_acoustid_candidates('alice', 0, music_dir=root, playlist_dir=playlists)
    assert {row['filepath'] for row in candidates} == {str(root / 'new.mp4'), str(root / 'changed.mp3')}
    assert stats['remaining'] == len(candidates)
    assert stats['scanned'] + stats['remaining'] == stats['total']
    assert stats['verified'] + stats['failed'] + stats['inconclusive'] == stats['scanned']
    assert len(stats['failures']) == 3
    failures = await test_db.get_acoustid_failures('alice', music_dir=root, playlist_dir=playlists)
    assert {r['file_path'] for r in failures} == {r['file_path'] for r in stats['failures']}


@pytest.mark.asyncio
async def test_limit_applies_after_exclusions_and_current_verifications_are_never_remaining(test_db, tmp_path):
    for index in range(55):
        await add_file(test_db, tmp_path / 'Explore' / f'{index}.mp3')
    for index in range(3):
        await add_file(test_db, tmp_path / f'{index}.mp3', status='verified')
    await add_file(test_db, tmp_path / 'remaining.mp3')
    rows = await test_db.get_acoustid_candidates('alice', 1, music_dir=tmp_path)
    assert [row['filepath'] for row in rows] == [str(tmp_path / 'remaining.mp3')]


@pytest.mark.asyncio
async def test_scan_remaining_does_not_refingerprint_when_only_index_is_stale(test_db, tmp_path, monkeypatch):
    configure(monkeypatch, test_db, tmp_path)
    audio = tmp_path / 'song.mp3'
    audio.write_bytes(b'not real audio; verification mocked')
    await add_file(test_db, audio, size=1, mtime=1)
    current = audio.stat()
    await test_db.save_acoustid_result(str(audio), 'verified', '', current.st_size, current.st_mtime_ns)
    refresh = AsyncMock()
    monkeypatch.setattr(test_db, 'refresh_library_paths', refresh)
    verify = AsyncMock(return_value=(True, 'Verified'))
    monkeypatch.setattr(acoustid_client, 'verify_track_against_metadata', verify)
    await main._scan_acoustid_batch_internal(0, 'alice')
    verify.assert_not_awaited()
    refresh.assert_awaited_once_with('alice', [str(audio.resolve())])
    # An explicit retry is still allowed even if the signature matches the prior check.
    await test_db.save_acoustid_result(str(audio), 'unknown', 'No match found', current.st_size, current.st_mtime_ns)
    await main._scan_acoustid_batch_internal(0, 'alice', retry_failed=True)
    verify.assert_awaited_once_with(audio)


@pytest.mark.asyncio
async def test_stats_never_fall_back_to_global_results_on_authentication_failure(monkeypatch):
    monkeypatch.setattr('backend.app.auth.get_current_user', AsyncMock(side_effect=HTTPException(401)))
    with pytest.raises(HTTPException) as error:
        await main.get_acoustid_stats(Request({'type': 'http'}))
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_second_scan_remaining_never_repeats_completed_files(test_db, tmp_path, monkeypatch):
    configure(monkeypatch, test_db, tmp_path)
    audio = tmp_path / 'new.mp3'
    audio.write_bytes(b'audio')
    stat = audio.stat()
    await add_file(test_db, audio, size=stat.st_size, mtime=stat.st_mtime_ns)
    verify = AsyncMock(return_value=(True, 'Verified'))
    monkeypatch.setattr(acoustid_client, 'verify_track_against_metadata', verify)
    await main._scan_acoustid_batch_internal(0, 'alice')
    await main._scan_acoustid_batch_internal(0, 'alice')
    verify.assert_awaited_once_with(audio)
    stats = await main.get_acoustid_stats(Request({'type': 'http'}))
    assert stats['total'] == stats['scanned'] == stats['verified'] == 1
    assert stats['remaining'] == 0
