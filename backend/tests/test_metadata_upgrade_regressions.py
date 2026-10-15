import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mutagen.id3 import ID3, TCON, TSRC, TPUB, USLT, TDOR

from backend.app import logger as app_logger
from backend.app import main, sync
from backend.app.media_versions import remaster_identity, same_master, master_matches_path
from backend.app.metadata_pipeline.extras import read_extras


@pytest.fixture(autouse=True)
def isolated_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv('VERYDISCO_RECOVERY_DIR', str(tmp_path / '.verydisco-recovery'))


@pytest.mark.parametrize(('title', 'expected'), [
    ('Song - 2011 Remaster', ('Song', '2011')),
    ('Song (Remastered 2011)', ('Song', '2011')),
    ('Song [2011 Remastered]', ('Song', '2011')),
    ('Song (Remastered)', ('Song', '')),
    ('1999', ('1999', None)),
    ('1985', ('1985', None)),
    ('Remaster', ('Remaster', None)),
])
def test_remaster_identity_does_not_destroy_display_or_numeric_titles(title, expected):
    assert remaster_identity(title) == expected


def test_remaster_versions_are_not_interchangeable():
    assert not same_master('Song', 'Song (2011 Remaster)')
    assert not same_master('Song (2009 Remaster)', 'Song (2011 Remaster)')
    assert same_master('Song - 2011 Remaster', 'Song (Remastered 2011)')
    assert master_matches_path('Song', 'Artist/Album (2011 Remaster)/01 Song.flac', 'Album (2011 Remaster)')
    assert not master_matches_path('Song', 'Artist/Album (2011 Remaster)/01 Song.flac')
    assert not sync.match_track_titles('Song', 'Artist - Song (2011 Remaster).flac', 'Artist')


def test_optional_metadata_survives_post_download_wipe(tmp_path):
    path = tmp_path / 'song.mp3'
    tags = ID3()
    tags.add(TCON(encoding=3, text=['Hip-Hop', 'Rap']))
    tags.add(TSRC(encoding=3, text=['US1234567890']))
    tags.add(TPUB(encoding=3, text=['Good Label']))
    tags.add(TDOR(encoding=3, text=['2001']))
    tags.add(USLT(encoding=3, lang='eng', desc='', text='Original lyrics'))
    tags.save(path)
    sync.embed_metadata(str(path), 'Artist', 'Song', 'Album', metadata_extras={'genre': ['Alternative']})
    values = read_extras(path)
    assert values['genre'] == ['Alternative']
    assert values['isrc'] == ['US1234567890']
    assert values['label'] == ['Good Label']
    assert values['original_date'] == ['2001']
    assert ID3(path).getall('USLT')[0].text == 'Original lyrics'


def test_failed_full_wipe_does_not_modify_original(tmp_path, monkeypatch):
    path = tmp_path / 'song.mp3'
    tags = ID3()
    tags.save(path)
    original = path.read_bytes()
    def fail(*args):
        raise OSError('disk full while writing optional tags')
    monkeypatch.setattr('backend.app.metadata_pipeline.extras.write_extras', fail)
    with pytest.raises(OSError):
        sync.embed_metadata(str(path), 'Artist', 'Song', 'Album')
    assert path.read_bytes() == original
    assert not list(tmp_path.glob('.verydisco-tags-*'))


def test_named_edition_reuses_base_but_not_other_masters(tmp_path):
    artist = tmp_path / 'Drake'
    base = artist / 'For All the Dogs'
    base.mkdir(parents=True)
    assert sync.extract_base_album_name('For All the Dogs (Scary Hours Edition)') == 'For All the Dogs'
    assert sync.find_existing_album_folder(artist, 'For All the Dogs (Scary Hours Edition)') is None
    assert sync.find_superseded_album_folders(artist, 'For All the Dogs (Scary Hours Edition)') == [base]
    assert sync.find_superseded_album_folders(artist, 'For All the Dogs (2011 Remaster)') == []
    deluxe = artist / 'For All the Dogs (Deluxe)'
    deluxe.mkdir()
    assert sync.find_existing_album_folder(artist, 'For All the Dogs (Scary Hours Edition)') is None
    base.rmdir()
    assert sync.find_existing_album_folder(artist, 'For All the Dogs') is None


def test_upgrade_only_moves_matched_tracks_and_preserves_recording_id(tmp_path, monkeypatch):
    base = tmp_path / 'music' / 'Drake' / 'For All the Dogs'
    base.mkdir(parents=True)
    source = base / 'Drake_For All the Dogs_01_Virginia Beach.mp3'
    source.write_bytes(b'audio')
    unrelated = base / 'Another song.mp3'
    unrelated.write_bytes(b'keep')
    calls = []
    monkeypatch.setattr(sync, 'embed_metadata', lambda **kwargs: calls.append(kwargs))
    result = sync.upgrade_or_merge_album_folders(tmp_path / 'music', 'Drake', 'For All the Dogs (Scary Hours Edition)',
        [{'title': 'Virginia Beach', 'track_position': 1, 'disk_number': 1, 'id': 'recording'}])
    assert len(result) == 1
    assert calls[0]['mbid_recording'] == 'recording'
    assert unrelated.read_bytes() == b'keep'
    assert not source.exists()
    assert list((tmp_path / '.verydisco-recovery').rglob(source.name))


def test_upgrade_tag_failure_retains_donor(tmp_path, monkeypatch):
    base = tmp_path / 'music' / 'Drake' / 'Album'
    base.mkdir(parents=True)
    source = base / '01 Track.mp3'
    source.write_bytes(b'original')
    def fail(**kwargs):
        raise OSError('access denied')
    monkeypatch.setattr(sync, 'embed_metadata', fail)
    assert sync.upgrade_or_merge_album_folders(tmp_path / 'music', 'Drake', 'Album (Deluxe)',
        [{'title': 'Track', 'track_position': 1, 'disk_number': 1}]) == []
    assert source.read_bytes() == b'original'


@pytest.mark.asyncio
async def test_plain_lrc_is_listed_for_synced_upgrade_and_refresh_removes_it(test_db, tmp_path):
    path = tmp_path / 'song.mp3'
    path.write_bytes((b'\xff\xfb\x90\x64' + b'\x00' * 413) * 20)
    ID3().save(path)
    sync.embed_metadata(str(path), 'Artist', 'Song', 'Album')
    path.with_suffix('.lrc').write_text('Plain words, no timestamps', encoding='utf-8')
    await test_db.refresh_library_paths('user', [str(path)])
    rows = await test_db.query_library_lyrics_missing('user')
    assert len(rows) == 1 and rows[0]['lyrics_plain'] == 1
    path.with_suffix('.lrc').write_text('[00:01.00] Synced words', encoding='utf-8')
    await test_db.refresh_library_paths('user', [str(path)])
    assert await test_db.query_library_lyrics_missing('user') == []


@pytest.mark.asyncio
async def test_log_replay_includes_startup_and_thread_messages(test_db, monkeypatch):
    handler = app_logger.QueueHandler()
    monkeypatch.setattr(app_logger, '_pending', [])
    handler.emit(logging.LogRecord('verydisco', logging.INFO, '', 0, 'before database', (), None))
    app_logger.attach_database(test_db)
    try:
        await asyncio.to_thread(handler.emit, logging.LogRecord('verydisco', logging.WARNING, '', 0, 'worker thread', (), None))
        await app_logger.flush_logs()
        rows = await test_db.get_logs(limit=0, since=app_logger.boot_started_at)
        assert [r['message'] for r in rows] == ['before database', 'worker thread']
        assert len(await test_db.get_logs(limit=1)) == 1
        assert [r['message'] for r in await test_db.get_logs(limit=0, after_id=rows[0]['id'])] == ['worker thread']
    finally:
        await app_logger.shutdown_logging()


@pytest.mark.asyncio
async def test_sse_replays_all_history_with_resume_ids(test_db, monkeypatch):
    monkeypatch.setattr(main, 'db', test_db)
    await test_db.add_log('INFO', 'previous boot', timestamp='2000-01-01')
    first = await test_db.add_log('INFO', 'startup', timestamp=app_logger.boot_started_at)
    second = await test_db.add_log('INFO', 'later', timestamp=app_logger.boot_started_at)
    request = SimpleNamespace(headers={}, is_disconnected=AsyncMock(return_value=True))
    response = await main.stream_logs(request)
    events = [event async for event in response.body_iterator]
    assert len(events) == 2 and f'id: {first}' in events[0]
    response = await main.stream_logs(request, after=first)
    events = [event async for event in response.body_iterator]
    assert len(events) == 1 and f'id: {second}' in events[0]


@pytest.mark.asyncio
async def test_metadata_extras_use_ids_and_cache_release_queries(monkeypatch):
    from backend.app.clients import musicbrainz as mb
    monkeypatch.setattr(mb, '_tag_extras_cache', {})
    get = AsyncMock(side_effect=[
        {'genres': [{'name': 'Hip Hop', 'count': 4}], 'release-group': {'id': 'group', 'first-release-date': '2001'}, 'label-info': [{'label': {'name': 'Label'}}]},
        {'isrcs': ['US1234567890'], 'artist-credit': [{'artist': {'id': 'artist-id'}}]},
    ])
    monkeypatch.setattr(mb, '_mb_get', get)
    result = await mb.musicbrainz_client.get_metadata_extras('recording', 'release')
    assert result == {'genre': ['Hip Hop'], 'release_group_mbid': 'group', 'original_date': '2001', 'label': ['Label'], 'isrc': ['US1234567890'], 'artist_mbids': ['artist-id']}
    assert await mb.musicbrainz_client.get_metadata_extras('recording', 'release') == result
    assert get.await_count == 2


@pytest.mark.asyncio
async def test_explicit_release_cannot_fall_back_to_unrelated_metadata(monkeypatch):
    from backend.app.clients.musicbrainz import MusicBrainzClient
    client = MusicBrainzClient()
    monkeypatch.setattr(client, 'get_album_tracklist', AsyncMock(return_value={'title': 'Edition', 'tracks': [{'title': 'Another Song'}]}))
    search = AsyncMock()
    monkeypatch.setattr(client, 'search_recording', search)
    assert await client.get_track_metadata('Artist', 'Song', 'Edition', release_mbid='release') is None
    search.assert_not_awaited()


def test_album_completeness_rejects_conflicting_recording_and_duplicate_files():
    from backend.app.library_reader import match_official_album_tracks
    official = [{'title': 'Song', 'id': 'wanted'}, {'title': 'Other Song', 'id': 'other'}]
    local = [{'title': 'Song', 'track_mbid': 'remix'}, {'title': 'Song', 'track_mbid': 'remix'}]
    assert not any(t['exists'] for t in match_official_album_tracks(official, local))


def test_starred_album_requires_all_tracks_not_a_nonempty_directory(tmp_path):
    from backend.app.scheduler import _check_album_dir_exists_sync
    path = tmp_path / 'song.mp3'
    path.write_bytes((b'\xff\xfb\x90\x64' + b'\x00' * 413) * 20)
    sync.embed_metadata(str(path), 'Artist', 'Song', 'Album')
    assert not _check_album_dir_exists_sync(tmp_path)
    assert not _check_album_dir_exists_sync(tmp_path, [{'title': 'Song'}, {'title': 'Missing'}])
    assert _check_album_dir_exists_sync(tmp_path, [{'title': 'Song'}])


def test_failed_explore_promotion_retains_source(tmp_path, monkeypatch):
    from backend.app.scheduler import _promote_track_sync
    source = tmp_path / 'explore' / 'Song.mp3'
    source.parent.mkdir()
    source.write_bytes(b'original')
    def fail(**kwargs):
        raise OSError('access denied')
    monkeypatch.setattr(sync, 'embed_metadata', fail)
    with pytest.raises(OSError):
        _promote_track_sync(source, tmp_path / 'music', 'Artist', 'Song', 'Album', tmp_path / 'playlists', None, {})
    assert source.read_bytes() == b'original'
    assert not list((tmp_path / 'music').rglob('.verydisco-promote-*'))


@pytest.mark.asyncio
async def test_cover_lookup_preserves_edition_qualifiers(monkeypatch):
    from backend.app.clients.deezer import DeezerClient
    client = DeezerClient()
    monkeypatch.setattr(client, '_request_json', AsyncMock(return_value={'data': [
        {'title': 'Album', 'artist': {'name': 'Artist'}, 'cover_xl': 'wrong'},
        {'title': 'Album (Deluxe)', 'artist': {'name': 'Artist'}, 'cover_xl': 'correct'},
    ]}))
    download = AsyncMock(return_value=b'cover')
    monkeypatch.setattr(client, 'download_cover_art', download)
    assert await client.get_album_cover('Artist', 'Album (Deluxe)') == b'cover'
    download.assert_awaited_once_with('correct')
