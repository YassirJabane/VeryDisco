from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock
import pytest
from mutagen.id3 import ID3
from backend.app import album_sync, main, sync
from backend.app.metadata_pipeline.service import MetadataRebuildService
from backend.app.metadata_pipeline.tags import read_track
from backend.app.media_versions import album_identity_key


@pytest.mark.asyncio
async def test_download_and_rebuild_use_the_same_original_full_date(monkeypatch):
    from backend.app.clients import musicbrainz as mb
    from backend.app.metadata_pipeline.providers import MusicBrainzReleaseProvider
    release = {'id': 'release', 'title': 'Album Deluxe', 'date': '2023-11-17',
        'release-group': {'id': 'group', 'first-release-date': '2023-10-06'},
        'artist-credit': [{'name': 'Drake', 'artist': {'id': 'artist', 'name': 'Drake'}}],
        'media': [{'position': 1, 'tracks': [{'position': 20, 'recording': {'id': 'rec-20', 'title': 'Rich Baby Daddy'}}]}]}
    monkeypatch.setattr(mb, 'get_release_with_media', AsyncMock(return_value=release))
    client = mb.MusicBrainzClient()
    download = await client.get_track_metadata('Drake', 'Rich Baby Daddy', 'Album Deluxe', release_mbid='release')
    rebuild = MusicBrainzReleaseProvider._normalize_release(release)
    assert download['date'] == rebuild['date'] == '2023-10-06'
    assert rebuild['release_date'] == '2023-11-17'


def media(path, title='Rich Baby Daddy', album='For All The Dogs Scary Hours Edition', track=20, date='2023-10-06'):
    path.parent.mkdir(parents=True, exist_ok=True)
    ID3().save(path)
    sync.embed_metadata(str(path), 'Drake', title, album, track_num=track, track_total=23,
        album_artist='Drake', date=date, disc_num=1, disc_total=2, mbid_album='release')
    return path


@pytest.mark.asyncio
async def test_rebuild_includes_unindexed_root_track_with_disc_files_and_preserves_full_dates(tmp_path):
    root = tmp_path / 'music'
    album = root / 'Drake' / 'For All The Dogs Scary Hours Edition'
    paths, official = [], []
    for position in range(1, 24):
        title = 'Rich Baby Daddy' if position == 20 else f'Song {position}'
        parent = album if position == 20 else album / 'Disc 01'
        local_title = title + ' (feat. Sexyy Red & SZA)' if position == 20 else title
        paths.append(media(parent / f'{position:02d} - {title}.mp3', title=local_title, track=0 if position == 20 else position))
        official.append({'title': title, 'artist': 'Drake', 'artists': [{'name': 'Drake', 'mbid': 'artist'}],
            'track': position, 'disc': 1, 'recording_mbid': f'rec-{position}', 'duration': 0})
    release = {'release_mbid': 'release', 'release_group_mbid': 'group', 'album': album.name,
        'album_artist': 'Drake', 'album_artists': [{'name': 'Drake', 'mbid': 'artist'}],
        'date': '2023-10-06', 'track_total': 29, 'disc_total': 2, 'tracks': official,
        'confidence': 1.0, 'labels': [], 'genre': []}
    provider = SimpleNamespace(match_release=AsyncMock(return_value=[release]))
    service = MetadataRebuildService(tmp_path / 'state', provider=provider)
    # Only 22 rows were indexed, and they retained just year rather than date.
    rows = [{'filepath': str(p), 'size': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns, 'year': '2023'}
        for p in paths if 'Rich Baby Daddy' not in p.name]
    plans = await service.scan('user', root, indexed_tracks=rows)
    assert len(plans) == 1
    assert plans[0]['folder'] == str(album)
    assert plans[0]['total_tracks'] == plans[0]['matched_tracks'] == 23
    rich = next(t for t in plans[0]['tracks'] if 'Rich Baby Daddy' in t['path'])
    assert rich['current']['track'] == 0
    assert rich['proposed']['track'] == 20
    assert all(t['current']['date'] == t['proposed']['date'] == '2023-10-06' for t in plans[0]['tracks'])
    await service.apply('user', plans[0]['id'], root, include_artwork=False)
    assert read_track(paths[19])['track'] == 20
    assert {read_track(p)['date'] for p in paths} == {'2023-10-06'}
    next_plan = (await service.scan('user', root, indexed_tracks=rows))[0]
    assert all(t['current']['date'] == t['proposed']['date'] for t in next_plan['tracks'])
    assert next(t for t in next_plan['tracks'] if 'Rich Baby Daddy' in t['path'])['current']['track'] == 20


def config(tmp_path):
    return SimpleNamespace(paths=SimpleNamespace(music_dir=str(tmp_path / 'music'), navidrome_playlists_dir=str(tmp_path / 'playlists')),
        listenbrainz=SimpleNamespace(active_playlists=['daily-jams']),
        slskd=SimpleNamespace(base_url='http://slskd.invalid', api_key='', downloads_dir=str(tmp_path / 'raw'), audio_quality={}),
        lyrics=SimpleNamespace(base_url='http://lrclib.invalid'),
        timeouts=SimpleNamespace(http_seconds=1, search_seconds=1, download_seconds=1),
        schedule=SimpleNamespace(max_candidate_attempts=1), navidrome=SimpleNamespace(url=''))


@pytest.mark.asyncio
@pytest.mark.parametrize('inside_album', [False, True])
async def test_existing_single_reuse_keeps_position_and_date(monkeypatch, tmp_path, inside_album):
    cfg = config(tmp_path)
    parent = tmp_path / 'music' / 'Drake' / 'For All The Dogs Scary Hours Edition' / 'Disc 01' if inside_album else tmp_path / 'old'
    source = media(parent / '20 - Rich Baby Daddy.mp3')
    original = source.read_bytes()
    monkeypatch.setattr(sync, 'find_existing_track_file', lambda *args, **kwargs: (source, None))
    monkeypatch.setattr(sync, 'get_file_audio_info', lambda *args: ('mp3', 320, 16, 44100))
    monkeypatch.setattr(sync, 'check_quality_status', lambda *args: 'same')
    assert await album_sync.download_single_track_task('Drake', 'Rich Baby Daddy', 'For All The Dogs Scary Hours Edition', cfg)
    files = list((tmp_path / 'music').rglob('*.mp3'))
    assert len(files) == 1
    assert read_track(files[0])['track'] == 20
    assert read_track(files[0])['date'] == '2023-10-06'
    if inside_album:
        assert source.read_bytes() == original
    else:
        assert not source.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('known_position', [False, True])
async def test_scoped_single_fallback_never_publishes_track_zero_on_provider_failure(monkeypatch, tmp_path, known_position):
    from backend.app.clients.acoustid import acoustid_client
    cfg = config(tmp_path)
    raw = media(tmp_path / 'raw' / 'remote.mp3', track=0)
    class Slskd:
        def __init__(self, **kwargs): pass
        search_candidates = AsyncMock(return_value=([{'username': 'peer', 'filename': 'remote.mp3', 'size': raw.stat().st_size}], None))
        request_download = AsyncMock(return_value=True)
        get_download_progress = AsyncMock(return_value=('succeeded', 'transfer', 100))
        delete_download = AsyncMock()
    class Client:
        def __init__(self, **kwargs): pass
        get_lyrics = AsyncMock(return_value=(None, 'missing'))
    monkeypatch.setattr(album_sync, 'SlskdClient', Slskd)
    monkeypatch.setattr(album_sync, 'LrcLibClient', Client)
    monkeypatch.setattr(album_sync, 'DeezerClient', Client)
    monkeypatch.setattr(sync, 'find_downloaded_file', lambda *args: raw)
    metadata = AsyncMock(side_effect=OSError('provider offline'))
    monkeypatch.setattr(album_sync, 'fetch_track_metadata_with_fallback', metadata)
    monkeypatch.setattr(acoustid_client, 'verify_track_against_metadata', AsyncMock(return_value=(True, 'matched')))
    known = {'title': 'Rich Baby Daddy', 'artist': 'Drake', 'album_artist': 'Drake', 'track_num': 20,
        'track_total': 23, 'disc_num': 1, 'disc_total': 2, 'date': '2023-10-06', 'mbid_recording': 'rec-20'} if known_position else None
    success = await album_sync.download_single_track_task('Drake', 'Rich Baby Daddy', 'For All The Dogs Scary Hours Edition', cfg,
        force=True, mbid_album_override='release', metadata_override=known)
    assert success is known_position
    metadata.assert_awaited_once_with(ANY, 'Drake', 'Rich Baby Daddy', 'For All The Dogs Scary Hours Edition', release_mbid='release')
    files = list((tmp_path / 'music').rglob('*.mp3'))
    if known_position:
        assert len(files) == 1
        tags = read_track(files[0])
        assert (tags['track'], tags['disc'], tags['disc_total'], tags['date']) == (20, 1, 2, '2023-10-06')
    else:
        assert not files
        assert raw.exists()


def test_harmonization_upgrades_year_to_full_date_without_losing_position(tmp_path):
    path = media(tmp_path / 'album' / '20 - Rich Baby Daddy.mp3', date='2023')
    assert sync.harmonize_album_directory(path.parent, target_date='2023-10-06') == 1
    tags = read_track(path)
    assert tags['date'] == '2023-10-06'
    assert tags['track'] == 20


@pytest.mark.asyncio
async def test_existing_track_zero_is_repaired_from_official_context_without_a_transfer(monkeypatch, tmp_path):
    cfg = config(tmp_path)
    album = 'For All The Dogs Scary Hours Edition'
    source = media(tmp_path / 'music' / 'Drake' / album / '00 - Rich Baby Daddy.mp3', track=0, date='2023')
    monkeypatch.setattr(sync, 'find_existing_track_file', lambda *args, **kwargs: (source, None))
    monkeypatch.setattr(sync, 'get_file_audio_info', lambda *args: ('mp3', 320, 16, 44100))
    monkeypatch.setattr(sync, 'check_quality_status', lambda *args: 'same')
    db = SimpleNamespace(get_user_by_id=AsyncMock(return_value=None), get_user_config=AsyncMock(return_value={}),
        refresh_library_paths=AsyncMock())
    result = await album_sync.download_single_track_task('Drake', 'Rich Baby Daddy', album, cfg, db=db, user_id='user',
        mbid_album_override='release', metadata_override={'track_num': 20, 'track_total': 23, 'disc_num': 1,
            'disc_total': 2, 'date': '2023-10-06', 'mbid_album': 'release', 'mbid_recording': 'rec-20'})
    assert result is True
    assert not source.exists()
    destination = next((tmp_path / 'music').rglob('*.mp3'))
    assert destination.parent.name == 'Disc 01'
    assert read_track(destination)['track'] == 20
    assert read_track(destination)['date'] == '2023-10-06'
    db.refresh_library_paths.assert_awaited_once_with('user', [str(source), str(destination)])


@pytest.mark.asyncio
async def test_catalog_and_album_summary_keep_full_date_after_scan_and_refresh(test_db, monkeypatch, tmp_path):
    path = media(tmp_path / 'music' / 'Drake' / 'Album (Deluxe)' / '20 - Song.mp3', album='Album (Deluxe)')
    def metadata(*args):
        return {'artist': 'Drake', 'title': 'Rich Baby Daddy', 'album': 'Album (Deluxe)', 'album_artist': 'Drake',
            'album_artists': ['Drake'], 'artists': ['Drake'], 'year': '2023', 'date': '2023-10-06',
            'track_num': 20, 'total_tracks': 23, 'disc_num': 1, 'disc_total': 2, 'duration': 200,
            'bitrate': 320, 'bit_depth': 16, 'sample_rate': 44100, 'ext': 'mp3', 'track_mbid': 'rec-20',
            'album_mbid': 'release', 'size': path.stat().st_size, 'mtime_ns': path.stat().st_mtime_ns,
            'ctime_ns': path.stat().st_ctime_ns, 'device': 0, 'inode': 0, 'embedded_cover': False,
            'quality_desc': 'MP3 320kbps', 'has_comment': False, 'compilation': False, 'mtime': path.stat().st_mtime}
    monkeypatch.setattr(main, 'read_file_metadata_with_cache', metadata)
    rows = main._build_library_index_sync('user', tmp_path / 'music', tmp_path / 'playlists', '', {}, lambda *args: None)
    assert rows[0]['album_norm'] == album_identity_key('Album (Deluxe)')
    await test_db.replace_library_index('user', rows)
    assert (await test_db.query_library_albums_grouped('user'))[0]['date'] == '2023-10-06'
    monkeypatch.setattr('backend.app.library_reader.read_library_file', metadata)
    await test_db.refresh_library_paths('user', [str(path)])
    row = (await test_db.query_library_index_complete('user'))[0]
    assert (row['date'], row['year'], row['album_norm']) == ('2023-10-06', '2023', album_identity_key('Album (Deluxe)'))
