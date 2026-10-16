from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from PIL import Image
from starlette.requests import Request

from backend.app.clients.listenbrainz import ListenBrainzClient, extract_artwork, _artwork_metadata_cache
from backend.app.thumbnails import folder_signature, local_thumbnail

RELEASE = '76df3287-6cda-33eb-8e9a-044b5e15ffdd'
RECORDING = 'e97f805a-ab48-4c52-855e-07049142113d'


def test_caa_identity_is_not_recording_identity():
    meta = {'extension': {'jspf': {'additional_metadata': {'caa_release_mbid': RELEASE, 'caa_id': 829521842}}}}
    assert extract_artwork(meta)['artwork'] == f'https://coverartarchive.org/release/{RELEASE}/829521842-250.jpg'
    assert extract_artwork({'recording_mbid': RECORDING})['artwork'] == ''
    assert extract_artwork({'caa_release_mbid': RELEASE, 'caa_id': '../bad'})['artwork'].endswith('/front-250')


@pytest.mark.asyncio
@respx.mock
async def test_metadata_batch_cache_and_album_identity():
    _artwork_metadata_cache.clear()
    client = ListenBrainzClient('test', 'daily-jams')
    route = respx.post('https://api.listenbrainz.org/1/metadata/recording/').respond(json={
        RECORDING: {'release': {'name': 'Dummy', 'caa_release_mbid': RELEASE, 'caa_id': 829521842}}
    })
    tracks = [{'mbid': RECORDING, 'album': 'Dummy'}, {'recording_mbid': RECORDING, 'album': 'Other edition'}]
    await client.enrich_artwork(tracks)
    assert tracks[0]['artwork'].endswith('/829521842-250.jpg')
    assert not tracks[1].get('artwork')
    await client.enrich_artwork([{'mbid': RECORDING, 'album': 'Dummy'}])
    assert route.call_count == 1
    _artwork_metadata_cache.clear()


@pytest.mark.asyncio
@respx.mock
async def test_metadata_outage_keeps_tracks_usable():
    _artwork_metadata_cache.clear()
    respx.post('https://api.listenbrainz.org/1/metadata/recording/').respond(status_code=503)
    tracks = [{'recording_mbid': RECORDING, 'artist': 'Artist', 'title': 'Song'}]
    await ListenBrainzClient('test', 'daily-jams').enrich_artwork(tracks)
    assert not tracks[0].get('artwork')
    assert tracks[0]['title'] == 'Song'
    _artwork_metadata_cache.clear()


def test_local_thumbnail_size_cache_invalidation_and_original(tmp_path):
    original = tmp_path / 'cover.png'
    Image.new('RGBA', (1600, 800), (255, 0, 0, 128)).save(original)
    original_bytes = original.read_bytes()
    signature = folder_signature(tmp_path)
    data = local_thumbnail(str(tmp_path), signature, 160)
    with Image.open(BytesIO(data)) as image:
        assert image.size == (160, 80)
        assert image.format == 'JPEG'
    assert len(data) < len(original_bytes)
    assert original.read_bytes() == original_bytes
    assert local_thumbnail(str(tmp_path), signature, 160) is data
    Image.new('RGB', (800, 1600), 'blue').save(original)
    updated = local_thumbnail(str(tmp_path), folder_signature(tmp_path), 160)
    with Image.open(BytesIO(updated)) as image:
        assert image.size == (80, 160)


@pytest.mark.asyncio
async def test_cover_endpoint_auth_scope_and_etag(tmp_path, monkeypatch):
    from backend.app import main
    Image.new('RGB', (600, 600), 'red').save(tmp_path / 'cover.jpg')
    monkeypatch.setattr('backend.app.auth.get_current_user', AsyncMock(return_value={'id': 'alice'}))
    monkeypatch.setattr(main, 'db', SimpleNamespace(get_user_by_id=AsyncMock(return_value={'music_dir': str(tmp_path)})))
    monkeypatch.setattr(main, 'config_manager', SimpleNamespace(config=SimpleNamespace(paths=SimpleNamespace(music_dir=str(tmp_path)))))
    request = Request({'type': 'http', 'headers': []})
    response = await main.get_album_cover(str(tmp_path), request, size=160)
    assert response.media_type == 'image/jpeg'
    request = Request({'type': 'http', 'headers': [(b'if-none-match', response.headers['etag'].encode())]})
    assert (await main.get_album_cover(str(tmp_path), request, size=160)).status_code == 304
    with pytest.raises(main.HTTPException) as error:
        await main.get_album_cover(str(tmp_path.parent), request, size=160)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_imported_playlist_thumbnail_is_scoped_to_owner(tmp_path, monkeypatch):
    from backend.app import main
    folder = tmp_path / 'spotify-alice'
    folder.mkdir()
    Image.new('RGB', (1000, 1000), 'green').save(folder / 'cover.jpg')
    monkeypatch.setattr('backend.app.auth.get_current_user', AsyncMock(return_value={'id': 'alice'}))
    monkeypatch.setattr(main, 'db', SimpleNamespace(
        get_user_by_id=AsyncMock(return_value={'playlist_dir': str(tmp_path)}),
        list_imported_playlists=AsyncMock(return_value=[{'id': 1, 'source': 'spotify-alice'}]),
    ))
    request = Request({'type': 'http', 'headers': []})
    response = await main.get_imported_playlist_thumbnail(1, request)
    with Image.open(BytesIO(response.body)) as image:
        assert image.size == (250, 250)
    with pytest.raises(main.HTTPException) as error:
        await main.get_imported_playlist_thumbnail(2, request)
    assert error.value.status_code == 404
    main.db.list_imported_playlists.assert_called_with('alice')
