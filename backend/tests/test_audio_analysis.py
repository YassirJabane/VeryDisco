import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.app.metadata_pipeline.audio_analysis import LocalAudioAnalyzer, audio_features, highlevel_features
from backend.app.metadata_pipeline.bpm import RecordingBpmProvider
from backend.app.metadata_pipeline.extras import write_extras
from backend.app.metadata_pipeline.tags import read_track
from backend.tests.test_bpm_rebuild import setup_library, MBID


def test_archive_and_flat_essentia_features_match_without_inventing_scores():
    nested = {'rhythm': {'bpm': 129.84, 'danceability': .7, 'onset_rate': 3},
              'tonal': {'key_key': 'D', 'key_scale': 'minor', 'key_strength': .8}}
    flat = {'rhythm.bpm': 129.84, 'rhythm.danceability': .7, 'rhythm.onset_rate': 3,
            'tonal.key_edma.key': 'D', 'tonal.key_edma.scale': 'minor', 'tonal.key_edma.strength': .8}
    first, second = audio_features(nested), audio_features(flat)
    assert first['key'] == second['key'] == 'D minor'
    assert first['camelot'] == second['camelot'] == '7A'
    assert first['bpm'] == second['bpm'] == 129.84
    assert 'bpm_confidence' not in first  # Missing confidence is never synthesized.
    assert audio_features({'rhythm': {'bpm': True, 'danceability': 'nan'}}) == {}


def test_highlevel_probabilities_are_suggestions_with_valid_scores_only():
    features = highlevel_features({'highlevel': {
        'mood_sad': {'value': 'sad', 'probability': .9},
        'voice_instrumental': {'value': 'instrumental', 'probability': .85},
        'invalid': {'value': 'x', 'probability': 'NaN'},
    }})
    assert set(features) == {'mood_sad', 'voice_instrumental'}


@pytest.mark.asyncio
async def test_new_bpm_rounding_key_and_apply_rollback_preserve_audio_tags(tmp_path):
    service, root, path, provider = setup_library(tmp_path)
    provider.lookup.return_value.update(bpm=['129.84'], features={
        'bpm': 129.84, 'key': 'D minor', 'key_strength': .8, 'danceability': .7})
    provider.lookup_highlevel = AsyncMock(return_value={'status': 'found', 'features': {
        'voice_instrumental': {'value': 'instrumental', 'probability': .99}}})
    before = path.read_bytes()
    plan = (await service.scan('user', root, audio_features_enabled=True, bpm_integer=True))[0]
    change = plan['tracks'][0]
    assert path.read_bytes() == before
    assert change['proposed']['bpm'] == ['130']
    assert change['bpm_lookup']['raw_bpm'] == '129.84'
    assert change['proposed']['key'] == ['Dm']
    assert 'instrumental' not in change['proposed']
    assert 'mood' not in change['proposed']
    await service.apply('user', plan['id'], root, include_artwork=False)
    assert read_track(path)['bpm'] == ['130']
    assert read_track(path)['key'] == ['Dm']
    await service.rollback('user', plan['id'], root)
    assert read_track(path)['bpm'] == []
    assert read_track(path)['key'] == []


@pytest.mark.asyncio
async def test_existing_decimal_bpm_and_key_are_preserved(tmp_path):
    service, root, path, provider = setup_library(tmp_path, ['129.84'])
    write_extras(path, {'key': ['Am']})
    provider.lookup.return_value.update(bpm=['140'], features={'key': 'D minor'})
    plan = (await service.scan('user', root, audio_features_enabled=True, bpm_integer=True))[0]
    assert plan['tracks'][0]['proposed']['bpm'] == ['129.84']
    assert 'key' not in plan['tracks'][0]['proposed']
    assert plan['bpm_summary'] == {'missing': 0, 'proposed': 0, 'unresolved': 0}


@pytest.mark.asyncio
async def test_local_analysis_fallback_without_archive_data(tmp_path):
    service, root, path, provider = setup_library(tmp_path)
    provider.lookup.return_value = {'status': 'not_found'}
    service.audio_analyzer = SimpleNamespace(analyze=AsyncMock(return_value={
        'status': 'found', 'source': 'Essentia local', 'features': {'bpm': 139.88, 'key': 'F# minor'}}))
    plan = (await service.scan('user', root, local_audio_analysis=True, audio_features_enabled=True, bpm_integer=True))[0]
    change = plan['tracks'][0]
    assert change['proposed']['bpm'] == ['140']
    assert change['proposed']['key'] == ['F#m']
    assert change['bpm_lookup']['source'] == 'Essentia local'
    assert change['audio_analysis']['local']['status'] == 'found'


@pytest.mark.asyncio
async def test_local_analyzer_missing_dependency_and_changed_file_do_not_start_worker(tmp_path, monkeypatch):
    path = tmp_path / 'song.mp3'
    path.write_bytes(b'original')
    monkeypatch.setattr('backend.app.metadata_pipeline.audio_analysis.importlib.util.find_spec', lambda _: None)
    start = AsyncMock()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', start)
    assert (await LocalAudioAnalyzer().analyze(path))['status'] == 'not_installed'
    assert (await LocalAudioAnalyzer().analyze(path, 1, 1))['status'] == 'file_changed'
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_worker_is_read_only_cached_and_reanalyses_changed_file(tmp_path, monkeypatch):
    path = tmp_path / 'song.mp3'
    path.write_bytes(b'original')
    cache = {}
    async def get(key): return cache.get(key)
    async def put(key, value, ttl): cache[key] = value
    class Process:
        returncode = 0
        async def communicate(self): return json.dumps({'rhythm.bpm': 129.84}).encode(), b''
    monkeypatch.setattr('backend.app.metadata_pipeline.audio_analysis.importlib.util.find_spec', lambda _: object())
    start = AsyncMock(return_value=Process())
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', start)
    analyzer = LocalAudioAnalyzer(get, put)
    assert (await analyzer.analyze(path))['features']['bpm'] == 129.84
    await analyzer.analyze(path)
    assert start.await_count == 1
    assert path.read_bytes() == b'original'
    path.write_bytes(b'changed audio')
    await analyzer.analyze(path)
    assert start.await_count == 2


@pytest.mark.asyncio
async def test_archive_lowlevel_features_and_highlevel_cached_separately(monkeypatch):
    requests = []
    def transport(request):
        requests.append(request)
        if request.url.path.endswith('high-level'):
            return httpx.Response(200, json={'highlevel': {'mood_happy': {'value': 'happy', 'probability': .9}}})
        return httpx.Response(200, json={'rhythm': {'bpm': 129.84},
            'tonal': {'key_key': 'D', 'key_scale': 'minor', 'key_strength': .8}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        monkeypatch.setattr('backend.app.metadata_pipeline.bpm.get_http_client', AsyncMock(return_value=client))
        provider = RecordingBpmProvider()
        assert (await provider.lookup(MBID))['features']['key'] == 'D minor'
        assert (await provider.lookup_highlevel(MBID))['features']['mood_happy']['value'] == 'happy'
        await provider.lookup_highlevel(MBID)
        assert len(requests) == 2
