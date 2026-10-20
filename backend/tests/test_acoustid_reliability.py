import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.requests import Request

from backend.app.clients.acoustid import AcoustIDClient, verification_status
from backend.app.artist_identity import ArtistAliasContext, artist_alias_scope, current_artist_aliases


@pytest.mark.asyncio
@pytest.mark.parametrize('first', [503, 429, 'timeout', 'invalid-json', 'internal'])
async def test_transient_api_failure_is_retried(monkeypatch, first):
    client = AcoustIDClient()
    monkeypatch.setattr(client, 'get_api_key', lambda: 'test')
    request = httpx.Request('POST', client.base_url)
    good = httpx.Response(200, json={'status': 'ok', 'results': []}, request=request)
    if first == 'timeout':
        bad = httpx.ReadTimeout('temporary')
    elif first == 'invalid-json':
        bad = httpx.Response(200, text='<html>proxy error</html>', request=request)
    elif first == 'internal':
        bad = httpx.Response(200, json={'status': 'error', 'error': {'message': 'internal error'}}, request=request)
    else:
        bad = httpx.Response(first, request=request)
    post = AsyncMock(side_effect=[bad, good])
    monkeypatch.setattr('backend.app.clients.http_client.get_http_client', AsyncMock(return_value=SimpleNamespace(post=post)))
    monkeypatch.setattr('backend.app.clients.acoustid.asyncio.sleep', AsyncMock())
    assert await client.lookup_fingerprint('fingerprint', 137) == {'status': 'ok', 'results': []}
    assert post.await_count == 2


@pytest.mark.asyncio
async def test_bad_api_key_is_not_retried_and_outage_is_not_unknown(monkeypatch):
    client = AcoustIDClient()
    monkeypatch.setattr(client, 'get_api_key', lambda: 'test')
    request = httpx.Request('POST', client.base_url)
    post = AsyncMock(return_value=httpx.Response(200, json={'status': 'error', 'error': {'message': 'invalid API key'}}, request=request))
    monkeypatch.setattr('backend.app.clients.http_client.get_http_client', AsyncMock(return_value=SimpleNamespace(post=post)))
    monkeypatch.setattr('backend.app.clients.acoustid.asyncio.sleep', AsyncMock())
    monkeypatch.setattr(client, 'generate_fingerprint', AsyncMock(return_value={'fingerprint': 'fp', 'duration': 137}))
    valid, reason = await client.verify_track_against_metadata(Path('file.mp3'), 'Drake', 'Song')
    assert verification_status(valid, reason) == 'unavailable'
    assert 'No match found' not in reason
    assert post.await_count == 1


@pytest.mark.asyncio
async def test_retries_are_bounded(monkeypatch):
    client = AcoustIDClient()
    monkeypatch.setattr(client, 'get_api_key', lambda: 'test')
    post = AsyncMock(side_effect=httpx.ReadTimeout('temporary'))
    monkeypatch.setattr('backend.app.clients.http_client.get_http_client', AsyncMock(return_value=SimpleNamespace(post=post)))
    monkeypatch.setattr('backend.app.clients.acoustid.asyncio.sleep', AsyncMock())
    assert (await client.lookup_fingerprint('fp', 137))['status'] == 'error'
    assert post.await_count == 3


def verification_client(monkeypatch, results):
    client = AcoustIDClient()
    monkeypatch.setattr(client, 'get_api_key', lambda: 'test')
    monkeypatch.setattr(client, 'generate_fingerprint', AsyncMock(return_value={'fingerprint': 'fp', 'duration': 137}))
    monkeypatch.setattr(client, 'lookup_fingerprint', AsyncMock(return_value={'status': 'ok', 'results': results}))
    monkeypatch.setattr(client, 'read_musicbrainz_recording_id', lambda _: 'expected-recording')
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize('strict', [False, True])
async def test_mbid_can_verify_without_text_and_fingerprint_is_generated_once(monkeypatch, strict):
    client = verification_client(monkeypatch, [{'score': .9994396, 'recordings': [{'id': 'expected-recording'}]}])
    valid, _ = await client.verify_track_against_metadata(Path('file.mp3'), 'Drake', 'Jumbotron Shit Poppin',
        'expected-recording', require_recording_match=strict)
    assert valid
    assert client.generate_fingerprint.await_count == 1
    assert client.lookup_fingerprint.await_count == 1


@pytest.mark.asyncio
async def test_replacement_cannot_verify_a_different_version_by_title_alone(monkeypatch):
    client = verification_client(monkeypatch, [{'score': .99, 'recordings': [
        {'id': 'slowed-recording', 'title': 'Trance', 'artists': [{'name': 'Metro Boomin'}]}]}])
    valid, reason = await client.verify_track_against_metadata(Path('file.mp3'), 'Metro Boomin', 'Trance',
        'original-recording', require_recording_match=True)
    assert not valid
    assert verification_status(valid, reason) == 'mismatch'
    assert 'original-recording' in reason and 'slowed-recording' in reason


@pytest.mark.asyncio
async def test_peer_tags_cannot_override_requested_identity(monkeypatch):
    client = verification_client(monkeypatch, [{'score': .99, 'recordings': [{'id': 'expected-recording', 'title': 'Other song', 'artists': [{'name': 'Other artist'}]}]}])
    valid, reason = await client.verify_track_against_metadata(Path('peer.mp3'), 'Drake', 'Song')
    assert verification_status(valid, reason) == 'mismatch'


@pytest.mark.asyncio
async def test_low_score_text_match_cannot_override_high_confidence_other_song(monkeypatch):
    client = verification_client(monkeypatch, [
        {'score': .95, 'recordings': [{'title': 'Other song', 'artists': [{'name': 'Drake'}]}]},
        {'score': .2, 'recordings': [{'title': 'Song', 'artists': [{'name': 'Drake'}]}]},
    ])
    valid, reason = await client.verify_track_against_metadata(Path('file.mp3'), 'Drake', 'Song')
    assert verification_status(valid, reason) == 'mismatch'


@pytest.mark.asyncio
async def test_unknown_never_requires_discarding_audio(monkeypatch):
    client = verification_client(monkeypatch, [])
    valid, reason = await client.verify_track_against_metadata(Path('file.mp3'), 'Drake', 'Song')
    assert verification_status(valid, reason) == 'unknown'
    assert await client.verify_track(Path('file.mp3'), 'expected', 'Drake', 'Song')


@pytest.mark.asyncio
@pytest.mark.parametrize('title,duration', [('DEAD', 67.21), ('FOREVER', 172.26), ('ISABELLA', 8.92)])
async def test_ys_unknown_is_a_fingerprint_result_not_an_artist_alias_failure(monkeypatch, title, duration):
    client = verification_client(monkeypatch, [])
    client.generate_fingerprint.return_value = {'fingerprint': 'fp', 'duration': duration}
    valid, reason = await client.verify_track_against_metadata(Path('ys.mp3'), '¥$', title, 'expected-recording')
    assert not valid
    assert verification_status(valid, reason) == 'unknown'
    assert f'{duration:.2f}s' in reason
    assert 'expected-recording' in reason
    client.lookup_fingerprint.assert_awaited_once_with('fp', duration, 'recordings releasegroups')
    # The exact same artist credit verifies as soon as AcoustID returns the recording.
    client.lookup_fingerprint.return_value = {'status': 'ok', 'results': [
        {'score': .99, 'recordings': [{'id': 'expected-recording'}]}]}
    valid, reason = await client.verify_track_against_metadata(Path('ys.mp3'), '¥$', title, 'expected-recording')
    assert valid
    assert verification_status(valid, reason) == 'verified'


@pytest.mark.asyncio
async def test_aliases_shared_by_album_tracks_and_child_fallback_then_discarded(monkeypatch):
    from backend.app.clients import musicbrainz
    fetch = AsyncMock(return_value={'id': 'jay', 'name': 'JAY-Z', 'aliases': [
        {'name': 'Jay Z', 'type': 'Artist name'}, {'name': 'Hova', 'type': 'Artist name'},
        {'name': 'Search only', 'type': 'Search hint'},
    ]})
    monkeypatch.setattr(musicbrainz, '_mb_get', fetch)
    contexts = []

    @artist_alias_scope
    async def track():
        context = current_artist_aliases()
        contexts.append(context)
        await context.ingest({'artist-credit': [{'name': 'JAY-Z', 'artist': {'id': 'jay', 'name': 'JAY-Z'}}]})
        assert context.same_artist('Hova', 'JAY-Z', 'jay')
        assert not context.same_artist('Search only', 'JAY-Z', 'jay')
        assert not context.same_artist('Hova', 'JAY-Z', 'other-id')

    @artist_alias_scope
    async def album():
        await track()
        await track()
        await asyncio.create_task(track())

    await album()
    assert len({id(c) for c in contexts}) == 1
    assert fetch.await_count == 1
    assert current_artist_aliases() is None
    await album()
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_recheck_is_owned_read_only_and_updates_result(tmp_path, monkeypatch):
    from backend.app import main
    from backend.app.clients.acoustid import acoustid_client
    path = tmp_path / 'song.mp3'
    path.write_bytes(b'original audio')
    monkeypatch.setattr('backend.app.auth.get_current_user', AsyncMock(return_value={'id': 'alice'}))
    monkeypatch.setattr(main, 'config_manager', SimpleNamespace(config=SimpleNamespace(acoustid=SimpleNamespace(api_key='test'), paths=SimpleNamespace(music_dir=str(tmp_path), navidrome_playlists_dir=str(tmp_path / 'playlists')))))
    database = SimpleNamespace(
        get_user_by_id=AsyncMock(return_value={'music_dir': str(tmp_path)}),
        get_acoustid_failures=AsyncMock(return_value=[{'file_path': str(path), 'reason': 'No match found'}]),
        save_acoustid_result=AsyncMock(), delete_cache=AsyncMock(),
    )
    monkeypatch.setattr(main, 'db', database)
    monkeypatch.setattr(main, '_active_tasks', {})
    monkeypatch.setattr(main, '_acoustid_recheck_locks', {})
    monkeypatch.setattr(acoustid_client, 'verify_track_against_metadata', AsyncMock(return_value=(True, 'Verified by MusicBrainz Recording ID match')))
    result = await main.recheck_acoustid_track(main.AcoustidRecheckRequest(file_path=str(path)), Request({'type': 'http'}))
    assert result['status'] == 'verified'
    assert path.read_bytes() == b'original audio'
    assert database.save_acoustid_result.call_args.args[1] == 'verified'
    database.get_acoustid_failures.assert_awaited_with('alice', music_dir=tmp_path.resolve(), playlist_dir=str(tmp_path / 'playlists'))
    database.get_acoustid_failures.return_value = []
    with pytest.raises(main.HTTPException) as error:
        await main.recheck_acoustid_track(main.AcoustidRecheckRequest(file_path=str(path)), Request({'type': 'http'}))
    assert error.value.status_code == 404
    with pytest.raises(main.HTTPException) as error:
        await main.recheck_acoustid_track(main.AcoustidRecheckRequest(file_path=str(tmp_path.parent / 'other.mp3')), Request({'type': 'http'}))
    assert error.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize('actual_id,expected_status', [('jay', 'verified'), ('unrelated', 'mismatch')])
async def test_verifier_uses_artist_aliases_but_rejects_another_mbid(monkeypatch, actual_id, expected_status):
    client = verification_client(monkeypatch, [{'score': .95, 'recordings': [
        {'title': 'Song', 'artists': [{'id': actual_id, 'name': 'JAY-Z'}]},
    ]}])

    @artist_alias_scope
    async def operation():
        current_artist_aliases().add({'id': 'jay', 'name': 'JAY-Z', 'aliases': [{'name': 'Hova', 'type': 'Artist name'}]})
        valid, reason = await client.verify_track_against_metadata(Path('file.mp3'), 'Hova', 'Song')
        assert verification_status(valid, reason) == expected_status
    await operation()


@pytest.mark.asyncio
async def test_cached_album_acquires_aliases_once_for_each_new_operation(monkeypatch):
    from backend.app.clients import musicbrainz
    fetch = AsyncMock(return_value={'id': 'jay', 'name': 'JAY-Z', 'aliases': [{'name': 'Hova', 'type': 'Artist name'}]})
    monkeypatch.setattr(musicbrainz, '_mb_get', fetch)
    winner = {'id': 'release', 'title': 'Album', 'artist-credit': [{'name': 'JAY-Z', 'artist': {'id': 'jay', 'name': 'JAY-Z'}}], 'discs': []}
    monkeypatch.setattr(musicbrainz, 'inspect_album_releases', AsyncMock(return_value={'winner': winner}))

    @artist_alias_scope
    async def operation():
        for _ in range(3):
            await musicbrainz.MusicBrainzClient().get_album_tracklist('JAY-Z', 'Album')
            assert 'Hova' in current_artist_aliases().names_for('JAY-Z')
        assert 'aliases' not in winner['artist-credit'][0]['artist']
    await operation()
    assert fetch.await_count == 1
    await operation()
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_recheck_candidates_include_unchanged_failed_tracks_only_for_owner(test_db):
    async with test_db.get_db() as connection:
        await connection.execute("INSERT INTO library_index (user_id, filepath, artist, title, album, size, mtime_ns, mtime) VALUES ('alice', '/song.mp3', 'Drake', 'Song', 'Album', 100, 1, 1)")
        await connection.commit()
    await test_db.save_acoustid_result('/song.mp3', 'failed', 'No match found in AcoustID database', 100, 1)
    assert not await test_db.get_acoustid_candidates('alice')
    assert len(await test_db.get_acoustid_candidates('alice', 0, retry_failed=True)) == 1
    assert not await test_db.get_acoustid_candidates('bob', 0, retry_failed=True)
    await test_db.save_acoustid_result('/song.mp3', 'verified', 'Verified by ID', 100, 1)
    assert not await test_db.get_acoustid_candidates('alice', 0, retry_failed=True)


@pytest.mark.asyncio
async def test_fpcalc_duration_estimate_is_corrected_before_lookup(monkeypatch, tmp_path):
    class Process:
        returncode = 0
        async def communicate(self):
            return json.dumps({'fingerprint': 'real-fingerprint', 'duration': 147.5}).encode(), b''
    client = AcoustIDClient()
    client._fpcalc_checked = client._fpcalc_ok = True
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=Process()))
    monkeypatch.setattr('mutagen.File', lambda _: SimpleNamespace(info=SimpleNamespace(length=137.15136)))
    monkeypatch.setattr(client, 'get_api_key', lambda: 'test')
    lookup = AsyncMock(return_value={'status': 'ok', 'results': [{'score': .9993407, 'recordings': [{'id': 'expected'}]}]})
    monkeypatch.setattr(client, 'lookup_fingerprint', lookup)
    valid, _ = await client.verify_track_against_metadata(tmp_path / 'song.mp3', 'Drake', 'Jumbotron Shit Poppin', expected_mbid='expected')
    assert valid
    assert lookup.call_args.args[:2] == ('real-fingerprint', 137.15136)


@pytest.mark.asyncio
async def test_partial_fingerprint_from_decoder_failure_is_not_sent(monkeypatch, tmp_path):
    class Process:
        returncode = 3
        async def communicate(self):
            return json.dumps({'fingerprint': 'partial-fingerprint', 'duration': 178.62}).encode(), b'ERROR: Invalid data found when processing input'
    client = AcoustIDClient()
    client._fpcalc_checked = client._fpcalc_ok = True
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=Process()))
    monkeypatch.setattr(client, 'get_api_key', lambda: 'test')
    lookup = AsyncMock()
    monkeypatch.setattr(client, 'lookup_fingerprint', lookup)
    valid, reason = await client.verify_track_against_metadata(tmp_path / 'song.mp3', 'Drake', 'Papi’s Home')
    assert verification_status(valid, reason) == 'skipped'
    assert 'fpcalc exit 3' in reason
    assert 'Invalid data' in reason
    assert 'installed' not in reason
    lookup.assert_not_called()


@pytest.mark.asyncio
async def test_rebuild_uses_shared_corrected_fingerprint(monkeypatch):
    from backend.app.clients.acoustid import acoustid_client
    from backend.app.metadata_pipeline.providers import AcoustIDRecordingVerifier
    monkeypatch.setattr(acoustid_client, 'generate_fingerprint', AsyncMock(return_value={'fingerprint': 'fp', 'duration': 137.151}))
    lookup = AsyncMock(return_value={'status': 'ok', 'results': [{'score': .99, 'recordings': [{'id': 'expected'}]}]})
    monkeypatch.setattr(acoustid_client, 'lookup_fingerprint', lookup)
    result = await AcoustIDRecordingVerifier().verify('song.mp3', 'expected', 'test-key')
    assert result['status'] == 'match'
    lookup.assert_awaited_once_with('fp', 137, 'recordingids', api_key='test-key')
