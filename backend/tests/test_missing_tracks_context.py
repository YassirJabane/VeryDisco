from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mutagen.id3 import ID3
from starlette.requests import Request
from fastapi import HTTPException

from backend.app import album_sync, main, sync
from backend.app.missing_tracks import prepare_missing_tracks
from backend.app.metadata_pipeline.tags import read_track

RELEASE = 'e8146c19-187a-476e-bf9f-f51b94c7395e'
OTHER = '20e4f5e2-262c-428a-a1b3-f13b9b932f7e'
GROUP = '4e60f08e-4b53-4139-a8c8-5bd81fad0d62'


def media(path, title='Existing', track=1, disc=1, release=RELEASE):
    path.parent.mkdir(parents=True, exist_ok=True)
    ID3().save(path)
    sync.embed_metadata(str(path), 'Artist', title, 'Canonical Album: Deluxe', track_num=track,
        track_total=3, disc_num=disc, disc_total=2, album_artist='Artist', date='2020-03-13',
        mbid_album=release, mbid_recording='existing' if title == 'Existing' else None)
    return path


@pytest.fixture
def context(tmp_path, monkeypatch):
    root = tmp_path / 'music'
    folder = root / 'Artist' / 'Original Album Folder'
    existing = media(folder / 'original.mp3')
    db = SimpleNamespace(query_library_index_complete=AsyncMock(return_value=[{'filepath': str(existing)}]),
        get_provider_cache=AsyncMock(return_value=None), set_provider_cache=AsyncMock())
    release = dict(release_mbid=RELEASE, release_group_mbid=GROUP, album='Different canonical spelling',
        album_artist='Artist', date='2020-03-13', track_total=3, disc_total=2,
        tracks=[dict(title='Existing', artist='Artist', recording_mbid='existing', disc=1, track=1),
                dict(title='Missing', artist='Artist', recording_mbid='missing', disc=2, track=1),
                dict(title='Last', artist='Artist', recording_mbid='last', disc=2, track=2)])
    lookup = AsyncMock(return_value=release)
    monkeypatch.setattr('backend.app.missing_tracks.MusicBrainzReleaseProvider.release_details', lookup)
    return db, root, folder, existing, lookup


def request(title='Missing', disc=2, track=1):
    return main.MissingTrackItem(title=title, track_number=track, disc_number=disc)


@pytest.mark.asyncio
async def test_missing_tracks_inherit_one_existing_release_folder_and_disc_positions(context):
    db, root, folder, _, lookup = context
    prepared = await prepare_missing_tracks(db, 'alice', root, folder, RELEASE,
                                            [request(), request('Last', track=2), request()])
    assert len(prepared) == 2
    lookup.assert_awaited_once_with(RELEASE)
    for item in prepared:
        assert item['album'] == 'Canonical Album: Deluxe'
        assert item['mbid_album_override'] == RELEASE
        assert item['metadata_override']['album_root'] == str(folder)
        assert item['metadata_override']['album_disc_dir'] == str(folder / 'Disc 02')
        assert item['metadata_override']['metadata_extras']['release_group_mbid'] == [GROUP]
    assert [item['metadata_override']['track_num'] for item in prepared] == [1, 2]
    assert not (root / 'Artist' / 'Canonical Album_ Deluxe').exists()


@pytest.mark.asyncio
async def test_existing_disc_folder_style_is_retained(context):
    db, root, folder, _, _ = context
    disc_file = media(folder / 'CD2' / 'old.mp3', title='Old', track=8, disc=2)
    db.query_library_index_complete.return_value.append({'filepath': str(disc_file)})
    item = (await prepare_missing_tracks(db, 'alice', root, folder, RELEASE, [request()]))[0]
    assert item['metadata_override']['album_disc_dir'] == str(folder / 'CD2')


@pytest.mark.asyncio
async def test_different_or_mixed_release_refused_before_download(context):
    db, root, folder, _, lookup = context
    with pytest.raises(ValueError, match='differs'):
        await prepare_missing_tracks(db, 'alice', root, folder, OTHER, [request()])
    lookup.assert_not_awaited()
    second = media(folder / 'other.mp3', title='Other', release=OTHER)
    db.query_library_index_complete.return_value.append({'filepath': str(second)})
    with pytest.raises(ValueError, match='mixed releases'):
        await prepare_missing_tracks(db, 'alice', root, folder, RELEASE, [request()])


@pytest.mark.asyncio
async def test_wrong_position_and_unowned_album_rejected(context):
    db, root, folder, _, _ = context
    with pytest.raises(ValueError, match='uniquely locate'):
        await prepare_missing_tracks(db, 'alice', root, folder, RELEASE, [request(disc=1)])
    db.query_library_index_complete.return_value = []
    with pytest.raises(PermissionError):
        await prepare_missing_tracks(db, 'bob', root, folder, RELEASE, [request()])
    with pytest.raises(PermissionError):
        await prepare_missing_tracks(db, 'alice', root, root.parent, RELEASE, [request()])


@pytest.mark.asyncio
async def test_existing_track_is_not_queued_again(context):
    db, root, folder, _, _ = context
    assert await prepare_missing_tracks(db, 'alice', root, folder, RELEASE,
                                        [request('Existing', disc=1)]) == []


@pytest.mark.asyncio
async def test_all_missing_requests_validated_before_any_task_is_created(context, monkeypatch):
    db, root, folder, _, _ = context
    cfg = SimpleNamespace(paths=SimpleNamespace(music_dir=str(root)))
    monkeypatch.setattr(main, 'config_manager', SimpleNamespace(is_configured=True, config=cfg))
    db.get_user_by_id = AsyncMock(return_value={'music_dir': str(root)})
    monkeypatch.setattr(main, 'db', db)
    monkeypatch.setattr('backend.app.auth.get_current_user', AsyncMock(return_value={'id': 'alice'}))
    created = []
    monkeypatch.setattr(main, '_create_tracked_task', lambda *args, **kwargs: created.append(args))
    body = main.DownloadMissingTracksRequest(artist='Artist', album='Album', folder_path=str(folder),
        release_mbid=RELEASE, missing_tracks=[request(), request('Wrong', track=99)])
    with pytest.raises(HTTPException) as error:
        await main.download_missing_tracks(body, Request({'type': 'http'}))
    assert error.value.status_code == 409
    assert not created


@pytest.mark.asyncio
async def test_full_download_preserves_album_identity_without_new_metadata_lookup(context, monkeypatch, tmp_path):
    from backend.tests.test_track_dates_inventory import config
    from backend.app.clients.acoustid import acoustid_client
    db, root, folder, original, _ = context
    prepared = (await prepare_missing_tracks(db, 'alice', root, folder, RELEASE, [request()]))[0]
    original_bytes = original.read_bytes()
    raw = media(tmp_path / 'raw' / 'remote.mp3', title='Missing')
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
    metadata = AsyncMock(side_effect=AssertionError('Must not re-resolve a pinned album'))
    monkeypatch.setattr(album_sync, 'fetch_track_metadata_with_fallback', metadata)
    monkeypatch.setattr(acoustid_client, 'verify_track_against_metadata', AsyncMock(return_value=(True, 'matched')))
    assert await album_sync.download_single_track_task(**prepared, config=config(tmp_path), force=True)
    destination = next((folder / 'Disc 02').glob('*.mp3'))
    tags = read_track(destination)
    assert tags['release_mbid'] == RELEASE
    assert tags['album'] == 'Canonical Album: Deluxe'
    assert (tags['disc'], tags['track']) == (2, 1)
    assert tags['recording_mbid'] == 'missing'
    assert original.read_bytes() == original_bytes
    metadata.assert_not_awaited()
    assert list((root / 'Artist').iterdir()) == [folder]


@pytest.mark.asyncio
async def test_atomic_missing_publish_never_overwrites_a_concurrent_file(tmp_path, monkeypatch):
    source, destination = tmp_path / 'raw.mp3', tmp_path / 'target.mp3'
    source.write_bytes(b'new transfer')
    def tag(**kwargs):
        destination.write_bytes(b'another writer won')
    monkeypatch.setattr(album_sync, 'embed_metadata', tag)
    with pytest.raises(FileExistsError):
        await album_sync._publish_tagged_track(source, destination, {}, None, replace_existing=False)
    assert destination.read_bytes() == b'another writer won'
    assert source.read_bytes() == b'new transfer'
