from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from mutagen.id3 import ID3

from backend.app.database import Database
from backend.app.metadata_pipeline.bpm import RecordingBpmProvider
from backend.app.metadata_pipeline.service import MetadataRebuildService
from backend.app.metadata_pipeline.tags import read_track
from backend.app.sync import embed_metadata

MBID = '099b148e-fe99-4b79-be6e-5078e4bb7415'
OTHER_MBID = 'cc61e6ca-8453-42b9-86ce-724dfd49732f'


@pytest.mark.asyncio
async def test_bpm_archive_uses_exact_recording_and_sqlite_cache(test_db, monkeypatch):
    requests = []
    def transport(request):
        requests.append(request)
        assert request.url.path == f'/api/v1/{MBID}/low-level'
        return httpx.Response(200, json={'rhythm': {'bpm': 123.5},
                                        'metadata': {'audio_properties': {'length': 200}}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        monkeypatch.setattr('backend.app.metadata_pipeline.bpm.get_http_client', AsyncMock(return_value=client))
        provider = RecordingBpmProvider(test_db.get_provider_cache, test_db.set_provider_cache)
        result = await provider.lookup(MBID.upper(), 200)
        assert result['bpm'] == ['123.5'] and result['recording_mbid'] == MBID
        reopened = Database(test_db.db_path)
        other = RecordingBpmProvider(reopened.get_provider_cache, reopened.set_provider_cache)
        assert await other.lookup(MBID, 200) == result
        assert (await other.lookup(MBID, 300))['status'] == 'duration_mismatch'
        assert (await other.lookup(MBID, 200))['status'] == 'found'
        assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [404, 429, 503])
async def test_archive_misses_cached_and_outages_do_not_timeout_every_track(monkeypatch, status):
    requests = []
    def transport(request):
        requests.append(request)
        return httpx.Response(status, json={})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        monkeypatch.setattr('backend.app.metadata_pipeline.bpm.get_http_client', AsyncMock(return_value=client))
        provider = RecordingBpmProvider()
        expected = 'not_found' if status == 404 else 'unavailable'
        assert (await provider.lookup(MBID))['status'] == expected
        assert (await provider.lookup(MBID))['status'] == expected
        if status != 404:
            assert (await provider.lookup(OTHER_MBID))['status'] == 'unavailable'
        assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [None, 0, 'NaN', -20, True, [80, 160]])
async def test_invalid_archive_tempo_is_never_proposed(monkeypatch, value):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'rhythm': {'bpm': value}}),
    )) as client:
        monkeypatch.setattr('backend.app.metadata_pipeline.bpm.get_http_client', AsyncMock(return_value=client))
        result = await RecordingBpmProvider().lookup(MBID)
        assert result == {'status': 'not_found'}


@pytest.mark.asyncio
async def test_missing_or_invalid_recording_id_does_not_query_archive(monkeypatch):
    get = AsyncMock()
    monkeypatch.setattr('backend.app.metadata_pipeline.bpm.get_http_client', get)
    for mbid in ['', None, 'Artist - Title', '../../release/other']:
        assert (await RecordingBpmProvider().lookup(mbid))['status'] == 'missing_mbid'
    get.assert_not_awaited()


@pytest.mark.asyncio
async def test_archive_cannot_propose_tempo_for_a_different_recording(monkeypatch):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'rhythm': {'bpm': 120},
            'metadata': {'tags': {'musicbrainz_recordingid': [OTHER_MBID]}}}),
    )) as client:
        monkeypatch.setattr('backend.app.metadata_pipeline.bpm.get_http_client', AsyncMock(return_value=client))
        result = await RecordingBpmProvider().lookup(MBID)
        assert result == {'status': 'unavailable'}


@pytest.mark.asyncio
async def test_archive_bpm_is_rounded_to_two_decimal_places_not_false_precision(monkeypatch):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'rhythm': {'bpm': 117.968910217}}),
    )) as client:
        monkeypatch.setattr('backend.app.metadata_pipeline.bpm.get_http_client', AsyncMock(return_value=client))
        assert (await RecordingBpmProvider().lookup(MBID))['bpm'] == ['117.97']


def setup_library(tmp_path: Path, bpm=None):
    root = tmp_path / 'music'
    path = root / 'Artist' / 'Album' / '01 Track.mp3'
    path.parent.mkdir(parents=True)
    ID3().save(path)
    embed_metadata(str(path), 'Artist', 'Track', 'Album', track_num=1,
                   metadata_extras={'bpm': bpm} if bpm else {})
    release = {'release_mbid': 'release', 'release_group_mbid': 'group', 'album': 'Album',
        'album_artist': 'Artist', 'album_artists': [{'name': 'Artist', 'mbid': 'artist'}],
        'date': '2020-01-01', 'track_total': 1, 'disc_total': 1, 'confidence': 1,
        'tracks': [{'title': 'Track', 'artist': 'Artist', 'artists': [{'name': 'Artist', 'mbid': 'artist'}],
                    'track': 1, 'disc': 1, 'recording_mbid': MBID}]}
    provider = SimpleNamespace(match_release=AsyncMock(return_value=[release]), release_details=AsyncMock(return_value=release))
    bpm_provider = SimpleNamespace(lookup=AsyncMock(return_value={'status': 'found', 'bpm': ['120'],
        'source': 'AcousticBrainz audio analysis', 'recording_mbid': MBID}))
    service = MetadataRebuildService(tmp_path / 'state', provider=provider, bpm_provider=bpm_provider)
    return service, root, path, bpm_provider


@pytest.mark.asyncio
async def test_missing_bpm_proposal_is_read_only_then_apply_and_rollback(tmp_path):
    service, root, path, bpm = setup_library(tmp_path)
    original = path.read_bytes()
    plan = (await service.scan('user', root))[0]
    assert path.read_bytes() == original
    assert plan['tracks'][0]['proposed']['bpm'] == ['120']
    assert plan['bpm_summary'] == {'missing': 1, 'proposed': 1, 'unresolved': 0}
    bpm.lookup.assert_awaited_once_with(MBID, 0)
    await service.apply('user', plan['id'], root, include_artwork=False)
    assert read_track(path)['bpm'] == ['120']
    await service.rollback('user', plan['id'], root)
    assert read_track(path)['bpm'] == []


@pytest.mark.asyncio
async def test_existing_bpm_is_not_overwritten_or_queried(tmp_path):
    service, root, path, bpm = setup_library(tmp_path, bpm='90')
    plan = (await service.scan('user', root))[0]
    assert plan['tracks'][0]['proposed']['bpm'] == ['90']
    assert plan['bpm_summary']['missing'] == 0
    bpm.lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_lookup_survives_manual_release_selection(tmp_path):
    service, root, path, bpm = setup_library(tmp_path)
    plan = (await service.scan('user', root, lookup_missing_bpm=False))[0]
    selected = await service.select_release('user', plan['id'], 'release')
    assert selected['tracks'][0]['bpm_lookup'] == {'status': 'disabled'}
    assert selected['bpm_summary'] == {'missing': 1, 'proposed': 0, 'unresolved': 1}
    bpm.lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_release_selection_refreshes_missing_bpm_proposal(tmp_path):
    service, root, path, bpm = setup_library(tmp_path)
    plan = (await service.scan('user', root))[0]
    bpm.lookup.return_value = {'status': 'not_found'}
    selected = await service.select_release('user', plan['id'], 'release')
    assert 'bpm' not in selected['tracks'][0]['proposed']
    assert selected['tracks'][0]['bpm_lookup'] == {'status': 'not_found'}


@pytest.mark.asyncio
async def test_bpm_provider_failure_does_not_discard_album_proposal(tmp_path):
    service, root, path, bpm = setup_library(tmp_path)
    bpm.lookup.side_effect = RuntimeError('cache offline')
    plan = (await service.scan('user', root))[0]
    assert plan['matched_tracks'] == 1
    assert plan['tracks'][0]['bpm_lookup'] == {'status': 'unavailable'}
    assert 'bpm' not in plan['tracks'][0]['proposed']
    assert plan['bpm_summary']['unresolved'] == 1


@pytest.mark.asyncio
async def test_acoustid_mismatch_prevents_recording_bpm_enrichment(tmp_path):
    service, root, path, bpm = setup_library(tmp_path)
    service.verifier = SimpleNamespace(verify=AsyncMock(return_value={'status': 'mismatch'}))
    plan = (await service.scan('user', root, verify_acoustid=True))[0]
    assert plan['tracks'][0]['bpm_lookup'] == {'status': 'recording_mismatch'}
    bpm.lookup.assert_not_awaited()
