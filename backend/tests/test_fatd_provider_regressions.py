import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from mutagen.id3 import ID3

from backend.app import album_sync, sync
from backend.app.clients import musicbrainz as mb
from backend.app.metadata_pipeline.providers import MusicBrainzReleaseProvider, MusicBrainzUnavailable
from backend.app.metadata_pipeline.tags import read_track


@pytest.fixture
def request_clock(monkeypatch):
    """Exercise the production gate/backoff deterministically without real delays."""
    clock = [100.0]
    sleeps = []
    async def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(mb, '_rate_lock', asyncio.Lock())
    monkeypatch.setattr(mb, '_last_request_time', 0.0)
    monkeypatch.setattr(mb.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(mb.asyncio, 'sleep', sleep)
    return sleeps


def mock_http(monkeypatch, responses):
    client = SimpleNamespace(get=AsyncMock(side_effect=responses))
    monkeypatch.setattr('backend.app.clients.http_client.get_http_client', AsyncMock(return_value=client))
    return client


@pytest.mark.asyncio
async def test_rebuild_retries_503_and_shares_gate_with_downloads(monkeypatch, request_clock):
    req = httpx.Request('GET', 'https://musicbrainz.org/ws/2/release/test')
    client = mock_http(monkeypatch, [
        httpx.Response(503, request=req, headers={'Retry-After': '8'}),
        httpx.Response(200, request=req, json={'id': 'release'}),
        httpx.Response(200, request=req, json={'id': 'recording'}),
    ])
    assert await MusicBrainzReleaseProvider()._get('/release/test', {}) == {'id': 'release'}
    assert await mb._mb_get('/recording/test') == {'id': 'recording'}
    assert request_clock == [8.0, mb._MIN_INTERVAL, mb._MIN_INTERVAL]
    assert client.get.await_count == 3


@pytest.mark.asyncio
async def test_exhausted_503_is_not_cached_as_a_missing_release(monkeypatch, request_clock):
    req = httpx.Request('GET', 'https://musicbrainz.org/ws/2/release/test')
    cache_set = AsyncMock()
    client = mock_http(monkeypatch, [httpx.Response(503, request=req)] * 3)
    provider = MusicBrainzReleaseProvider(cache_set=cache_set)
    with pytest.raises(MusicBrainzUnavailable, match='temporarily unavailable'):
        await provider.release_details('test')
    assert client.get.await_count == 3
    cache_set.assert_not_awaited()


@pytest.mark.asyncio
async def test_404_is_not_retried(monkeypatch, request_clock):
    req = httpx.Request('GET', 'https://musicbrainz.org/ws/2/release/test')
    client = mock_http(monkeypatch, [httpx.Response(404, request=req)])
    assert await MusicBrainzReleaseProvider().release_details('test') is None
    assert client.get.await_count == 1
    assert request_clock == []


@pytest.mark.parametrize('target', ['For All The Dogs Scary Hours Edition', 'For All The Dogs (Scary Hours Edition)'])
def test_named_edition_detects_real_base_folder_without_artist_specific_rules(tmp_path, target):
    artist = tmp_path / 'Drake'
    base = artist / 'For All the Dogs'
    base.mkdir(parents=True)
    assert sync.find_superseded_album_folders(artist, target) == [base]
    assert sync.find_superseded_album_folders(artist, target + ' (2011 Remaster)') == []
    assert sync.find_superseded_album_folders(artist, 'Other Album Scary Hours Edition') == []


@pytest.mark.asyncio
@pytest.mark.parametrize('migrate_base', [False, True])
@pytest.mark.parametrize('extra_track', [False, True])
async def test_album_reuses_disc_subfolders_and_migrates_unbracketed_edition(monkeypatch, tmp_path, migrate_base, extra_track):
    monkeypatch.setenv('VERYDISCO_RECOVERY_DIR', str(tmp_path / 'recovery'))
    music = tmp_path / 'music'
    playlists = tmp_path / 'playlists'
    playlists.mkdir()
    album = 'For All The Dogs Scary Hours Edition'
    destination = music / 'Drake' / album / 'Disc 01'
    destination.mkdir(parents=True)
    tracks = [{'title': '8am in Charlotte', 'track_position': 1, 'disk_number': 1, 'id': 'recording-1'},
              {'title': 'BBL Love (interlude)', 'track_position': 2, 'disk_number': 1, 'id': 'recording-2'}]
    sources = []
    for index, track in enumerate(tracks):
        parent = music / 'Drake' / 'For All the Dogs' if migrate_base and index == 1 else destination
        parent.mkdir(parents=True, exist_ok=True)
        source = parent / f"{index + 1:02d} - {track['title']}.mp3"
        ID3().save(source)
        sync.embed_metadata(str(source), 'Drake', track['title'], 'For All the Dogs' if parent != destination else album,
                            mbid_recording=track['id'], mbid_album='base-release' if parent != destination else 'edition-release')
        sources.append(source)
    tagged = []
    original_embed = sync.embed_metadata
    def embed(**kwargs):
        tagged.append(kwargs)
        return original_embed(**kwargs)
    monkeypatch.setattr(sync, 'embed_metadata', embed)
    monkeypatch.setattr(sync, 'harmonize_album_directory', lambda **kwargs: 0)
    monkeypatch.setattr(sync, 'cleanup_album_explore_tracks', lambda *args: None)
    monkeypatch.setattr(sync, 'get_file_audio_info', lambda *args: ('mp3', 320, 16, 44100))
    tracklist = {'tracks': tracks, 'album_artist': 'Drake', 'album_artists': ['Drake'], 'release_mbid': 'edition-release'}
    monkeypatch.setattr(mb.musicbrainz_client, 'get_album_tracklist', AsyncMock(return_value=tracklist))
    monkeypatch.setattr(mb.musicbrainz_client, 'get_cover_art', AsyncMock(return_value=None))
    class FakeClient:
        def __init__(self, **kwargs): self.base_url = kwargs.get('base_url', '')
        get_album_cover = AsyncMock(return_value=None)
        def _get_headers(self): return {}
    for name in ['SlskdClient', 'LrcLibClient', 'DeezerClient']:
        monkeypatch.setattr(album_sync, name, FakeClient)
    metadata = AsyncMock(side_effect=AssertionError('Existing album must not fetch metadata/lyrics again'))
    monkeypatch.setattr(album_sync, 'fetch_track_metadata_with_fallback', metadata)
    fallback = AsyncMock(return_value=True)
    monkeypatch.setattr(album_sync, 'download_single_track_task', fallback)
    db = SimpleNamespace(get_user_by_id=AsyncMock(return_value=None), get_user_config=AsyncMock(return_value={}),
                         update_album_download_status=AsyncMock(), refresh_library_paths=AsyncMock(), mark_instrumental=AsyncMock())
    config = SimpleNamespace(paths=SimpleNamespace(music_dir=str(music), navidrome_playlists_dir=str(playlists)),
        listenbrainz=SimpleNamespace(active_playlists=[]), slskd=SimpleNamespace(base_url='http://slskd.invalid', api_key='', audio_quality=SimpleNamespace(preset='none')),
        lyrics=SimpleNamespace(base_url='http://lrclib.invalid'), timeouts=SimpleNamespace(http_seconds=5),
        schedule=SimpleNamespace(max_candidate_attempts=1), navidrome=SimpleNamespace(url=''))
    files = [{'filename': f"remote\\{i + 1:02d} - {t['title']}.mp3", 'size': 100} for i, t in enumerate(tracks)]
    if extra_track:
        tracks.append({'title': 'Extra Scary Hours Track', 'track_position': 3, 'disk_number': 1, 'id': 'recording-3'})
        files.append({'filename': 'remote/03 - Extra Scary Hours Track.mp3', 'size': 100})
    # A peer queue error must not make single-track fallback download reused tracks.
    http = SimpleNamespace(post=AsyncMock(return_value=httpx.Response(500)))
    monkeypatch.setattr('backend.app.clients.http_client.get_http_client', AsyncMock(return_value=http))
    await album_sync._download_album_task_internal(1, 'Drake', '', album, config, db, user_id='user',
        chosen_username='peer', chosen_folder='remote', chosen_files=files, release_mbid='edition-release')
    db.update_album_download_status.assert_awaited_with(1, 'completed')
    metadata.assert_not_awaited()
    if extra_track:
        assert http.post.await_args.kwargs['json'] == [{'filename': 'remote/03 - Extra Scary Hours Track.mp3', 'size': 100}]
        fallback.assert_awaited_once()
        assert fallback.await_args.kwargs['title'] == 'Extra Scary Hours Track'
        assert fallback.await_args.kwargs['force'] is False
    else:
        fallback.assert_not_awaited()
        http.post.assert_not_awaited()
    assert len(tagged) == (1 if migrate_base else 0)
    assert sources[0].exists()
    if migrate_base:
        assert not sources[1].exists()
        assert len(list((tmp_path / 'recovery').rglob(sources[1].name))) == 1
        assert sorted(read_track(p)['recording_mbid'] for p in music.rglob('*.mp3')) == ['recording-1', 'recording-2']
    else:
        assert sources[1].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('publish_fails', [False, True])
async def test_reusing_string_path_updates_m3u_and_never_redownloads_on_local_failure(monkeypatch, tmp_path, publish_fails):
    music, playlists = tmp_path / 'music', tmp_path / 'playlists'
    source = music / 'Drake' / 'Other location' / '01 - Song.mp3'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'original media')
    playlists.mkdir()
    monkeypatch.setattr(sync, 'upgrade_or_merge_album_folders', lambda **kwargs: [])
    monkeypatch.setattr(sync, 'harmonize_album_directory', lambda **kwargs: 0)
    monkeypatch.setattr(sync, 'cleanup_album_explore_tracks', lambda *args: None)
    monkeypatch.setattr(sync, 'find_existing_track_file', lambda *args: (str(source), None))
    monkeypatch.setattr(sync, 'get_file_audio_info', lambda *args: ('mp3', 320, 16, 44100))
    monkeypatch.setattr('backend.app.metadata_pipeline.tags.read_track', lambda *args: {'album': 'Album', 'recording_mbid': 'rec'})
    m3u_calls = []
    monkeypatch.setattr(sync, 'update_m3u_references', lambda *args: m3u_calls.append(args))
    publish = AsyncMock(side_effect=OSError('Access denied') if publish_fails else None)
    monkeypatch.setattr(album_sync, '_publish_tagged_track', publish)
    monkeypatch.setattr(album_sync, 'fetch_track_metadata_with_fallback', AsyncMock(return_value={
        'title': 'Song', 'track_num': 1, 'cover_bytes': None, 'artist': 'Drake', 'album_artist': 'Drake', 'date': None}))
    class FakeClient:
        def __init__(self, **kwargs): pass
        get_album_cover = AsyncMock(return_value=None)
        get_lyrics = AsyncMock(return_value=(None, 'missing'))
    for name in ['SlskdClient', 'LrcLibClient', 'DeezerClient']:
        monkeypatch.setattr(album_sync, name, FakeClient)
    monkeypatch.setattr(mb.musicbrainz_client, 'get_album_tracklist', AsyncMock(return_value={
        'album_artist': 'Drake', 'release_mbid': 'release',
        'tracks': [{'title': 'Song', 'track_position': 1, 'disk_number': 1, 'id': 'rec'}]}))
    monkeypatch.setattr(mb.musicbrainz_client, 'get_cover_art', AsyncMock(return_value=None))
    db = SimpleNamespace(get_user_by_id=AsyncMock(return_value=None), get_user_config=AsyncMock(return_value={}),
        update_album_download_status=AsyncMock(), refresh_library_paths=AsyncMock())
    config = SimpleNamespace(paths=SimpleNamespace(music_dir=str(music), navidrome_playlists_dir=str(playlists)),
        listenbrainz=SimpleNamespace(active_playlists=[]), slskd=SimpleNamespace(base_url='http://slskd.invalid', api_key='', audio_quality=SimpleNamespace(preset='none')),
        lyrics=SimpleNamespace(base_url='http://lrclib.invalid'), timeouts=SimpleNamespace(http_seconds=5),
        schedule=SimpleNamespace(max_candidate_attempts=1), navidrome=SimpleNamespace(url=''))
    network = mock_http(monkeypatch, [])
    await album_sync._download_album_task_internal(1, 'Drake', '', 'Album', config, db, user_id='user',
        chosen_username='peer', chosen_folder='remote', chosen_files=[{'filename': 'remote/01 - Song.mp3', 'size': 100}], release_mbid='release')
    db.update_album_download_status.assert_awaited_with(1, 'failed' if publish_fails else 'completed')
    assert source.read_bytes() == b'original media'
    publish.assert_awaited_once()
    network.get.assert_not_awaited()
    if publish_fails:
        assert not m3u_calls
    else:
        assert m3u_calls[0][1] == source.name
