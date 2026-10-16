import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.app.clients.lrclib import LrcLibClient
from backend.app.lyrics_autosearch import new_progress, run_autosearch
from backend.app.artist_identity import artist_alias_scope, current_artist_aliases, search_queries_with_aliases


def library(tmp_path, names):
    rows = []
    for name in names:
        audio = tmp_path / f'{name}.mp3'
        audio.write_bytes(b'audio')
        rows.append(dict(filepath=str(audio), artist='Artist', title=name, album='Album', duration=200))
    db = SimpleNamespace(
        query_library_lyrics_missing=AsyncMock(return_value=rows),
        get_instrumental_overrides=AsyncMock(return_value=set()),
        refresh_library_paths=AsyncMock(), delete_cache=AsyncMock())
    return db, rows


@pytest.mark.asyncio
async def test_bulk_fills_missing_upgrades_plain_and_preserves_synced(tmp_path, monkeypatch):
    db, rows = library(tmp_path, ['Missing', 'Plain', 'Synced', 'KeepPlain', 'Instrumental'])
    (tmp_path / 'Plain.txt').write_text('old words')
    (tmp_path / 'KeepPlain.lrc').write_text('keep these words')
    (tmp_path / 'Synced.lrc').write_text('[00:01.00]manual')
    lookup = AsyncMock(side_effect=[('new words', 'plain'), ('[00:01.00]upgrade', 'synced'), (None, 'missing')])
    monkeypatch.setattr(LrcLibClient, 'find_automatic', lookup)
    progress, notify = new_progress(), AsyncMock()
    await run_autosearch(db, 'user', tmp_path, progress, notify)
    assert progress['status'] == 'completed'
    assert (progress['plain'], progress['synced'], progress['skipped'], progress['not_found']) == (1, 1, 2, 1)
    assert (tmp_path / 'Missing.lrc').read_text() == 'new words'
    assert (tmp_path / 'Plain.lrc').read_text() == '[00:01.00]upgrade'
    assert (tmp_path / 'Synced.lrc').read_text() == '[00:01.00]manual'
    assert (tmp_path / 'KeepPlain.lrc').read_text() == 'keep these words'
    assert lookup.call_args_list[1].kwargs['synced_only'] is True
    assert db.refresh_library_paths.await_count == 3
    notify.assert_awaited_once()
    db.query_library_lyrics_missing.assert_awaited_once_with('user')


@pytest.mark.asyncio
async def test_bulk_rechecks_manual_changes_and_scope(tmp_path, monkeypatch):
    root = tmp_path / 'music'
    root.mkdir()
    db, rows = library(root, ['Edited', 'Outside', 'Marked'])
    outside = tmp_path / 'outside.mp3'
    outside.write_bytes(b'audio')
    rows[1]['filepath'] = str(outside)
    async def lookup(*args, **kwargs):
        (root / 'Edited.lrc').write_text('manual edit')
        return '[00:01.00]automatic', 'synced'
    monkeypatch.setattr(LrcLibClient, 'find_automatic', AsyncMock(side_effect=lookup))
    db.get_instrumental_overrides.return_value = {rows[2]['filepath']}
    progress = new_progress()
    await run_autosearch(db, 'user', root, progress, AsyncMock())
    assert (root / 'Edited.lrc').read_text() == 'manual edit'
    assert not outside.with_suffix('.lrc').exists()
    assert progress['skipped'] == 2
    assert progress['failed'] == 1
    db.refresh_library_paths.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_stops_on_rate_limit(tmp_path, monkeypatch):
    db, _ = library(tmp_path, ['One', 'Two'])
    response = httpx.Response(429, request=httpx.Request('GET', 'https://lrclib.net/api/search'))
    lookup = AsyncMock(side_effect=httpx.HTTPStatusError('limit', request=response.request, response=response))
    monkeypatch.setattr(LrcLibClient, 'find_automatic', lookup)
    progress = new_progress()
    await run_autosearch(db, 'user', tmp_path, progress, AsyncMock())
    assert progress['status'] == 'rate_limited'
    assert progress['processed'] == 1
    lookup.assert_awaited_once()


@pytest.mark.asyncio
async def test_automatic_matching_rejects_wrong_artist_duration_and_plain_upgrade(monkeypatch):
    client = LrcLibClient()
    def candidate(**kwargs):
        return dict(artistName='Artist', trackName='Song', duration=200, **kwargs)
    results = [
        dict(artistName='Wrong artist', trackName='Song', albumName='Album', duration=200, syncedLyrics='[00:01]wrong'),
        dict(artistName='Artist', trackName='Song', duration=240, syncedLyrics='[00:01]wrong version'),
        candidate(plainLyrics='plain'),
    ]
    monkeypatch.setattr(client, 'search_lyrics', AsyncMock(return_value=results))
    assert await client.find_automatic('Artist', 'Song', 'Album', 200, synced_only=True) == (None, 'missing')
    assert await client.find_automatic('Artist', 'Song', 'Album', 200) == ('plain', 'plain')
    results.append(candidate(syncedLyrics='[00:01]correct'))
    assert await client.find_automatic('Artist', 'Song', 'Album', 200) == ('[00:01]correct', 'synced')


@pytest.mark.asyncio
async def test_automatic_search_continues_past_plain_strict_result(monkeypatch):
    client = LrcLibClient()
    search = AsyncMock(side_effect=[
        [dict(id=1, artistName='Artist', trackName='Song', duration=200, plainLyrics='plain')],
        [dict(id=2, artistName='Artist', trackName='Song', duration=200, syncedLyrics='[00:01]upgrade')],
        [],
    ])
    monkeypatch.setattr(client, '_search', search)
    assert await client.find_automatic('Artist', 'Song', 'Album', 200, synced_only=True) == ('[00:01]upgrade', 'synced')
    assert search.await_count == 3


@pytest.mark.asyncio
async def test_aliases_only_resolved_after_first_empty_query_and_reused(monkeypatch):
    calls = []
    async def mb_get(path, params):
        calls.append(path)
        return {'artists': [{'id': 'artist-id', 'name': 'Jay-Z', 'aliases': [
            {'name': 'Shawn Carter', 'type': 'Artist name'}]}]}
    monkeypatch.setattr('backend.app.clients.musicbrainz._mb_get', mb_get)

    @artist_alias_scope
    async def operation():
        queries = search_queries_with_aliases('Jay-Z', ['Song Jay Z', 'Song'], title='Song')
        assert await anext(queries) == 'Song Jay Z'
        assert calls == []
        assert await anext(queries) == 'Song Shawn Carter'
        assert current_artist_aliases().same_artist('Jay-Z', 'Shawn Carter')
        await queries.aclose()
        following = [q async for q in search_queries_with_aliases('Jay-Z', ['Next Jay Z'], title='Next')]
        assert 'Next Shawn Carter' in following
        assert calls == ['/artist']
    await operation()
    assert current_artist_aliases() is None


@pytest.mark.asyncio
async def test_ambiguous_artist_lookup_does_not_supply_aliases(monkeypatch):
    monkeypatch.setattr('backend.app.clients.musicbrainz._mb_get', AsyncMock(return_value={'artists': [
        {'id': 'a', 'name': 'Common'}, {'id': 'b', 'name': 'Common'}]}))
    @artist_alias_scope
    async def operation():
        assert [q async for q in search_queries_with_aliases('Common', ['Song Common', 'Song'], title='Song')] == ['Song Common', 'Song']
    await operation()


@pytest.mark.asyncio
async def test_api_prevents_duplicate_jobs_and_isolates_progress(monkeypatch, tmp_path):
    from backend.app import main, auth, lyrics_autosearch
    from starlette.requests import Request
    monkeypatch.setattr(main, '_active_tasks', {})
    monkeypatch.setattr(main, '_lyrics_autosearch_progress', {})
    monkeypatch.setattr(auth, 'get_current_user', AsyncMock(return_value={'id': 'alice'}))
    monkeypatch.setattr(main.config_manager, 'config', SimpleNamespace(
        paths=SimpleNamespace(music_dir=str(tmp_path)), lyrics=SimpleNamespace(base_url='https://lrclib.net'),
        timeouts=SimpleNamespace(http_seconds=20)))
    monkeypatch.setattr(main.db, 'get_user_by_id', AsyncMock(return_value={'music_dir': str(tmp_path)}))
    gate = asyncio.Event()
    runner = AsyncMock(side_effect=lambda *args, **kwargs: None)
    async def run(*args, **kwargs):
        await runner(*args, **kwargs)
        await gate.wait()
    monkeypatch.setattr(lyrics_autosearch, 'run_autosearch', run)
    request = Request({'type': 'http', 'method': 'POST', 'path': '/api/lyrics/autosearch', 'headers': []})
    try:
        first, second = await asyncio.gather(main.start_lyrics_autosearch(request), main.start_lyrics_autosearch(request))
        assert first is second
        await asyncio.sleep(0)
        runner.assert_awaited_once()
        assert (await main.lyrics_autosearch_status(request))['status'] == 'running'
        monkeypatch.setattr(auth, 'get_current_user', AsyncMock(return_value={'id': 'bob'}))
        assert await main.lyrics_autosearch_status(request) == {'status': 'idle'}
    finally:
        gate.set()
        await asyncio.gather(*(entry['task'] for entry in main._active_tasks.values()))


@pytest.mark.asyncio
async def test_provider_rate_limit_sets_cooldown_and_preserves_error(monkeypatch):
    from backend.app.clients import lrclib
    request = httpx.Request('GET', 'https://lrclib.net/api/search')
    get = AsyncMock(return_value=httpx.Response(429, request=request, headers={'Retry-After': '120'}))
    monkeypatch.setattr(lrclib, 'get_http_client', AsyncMock(return_value=SimpleNamespace(get=get)))
    monkeypatch.setattr(lrclib, '_next_request', 0)
    with pytest.raises(httpx.HTTPStatusError):
        await LrcLibClient()._search({'track_name': 'Song'})
    assert lrclib._next_request >= lrclib.time.monotonic() + 119


@pytest.mark.asyncio
async def test_symbol_artist_names_do_not_match_other_symbols(monkeypatch):
    client = LrcLibClient()
    monkeypatch.setattr(client, 'search_lyrics', AsyncMock(return_value=[
        {'artistName': '$$$', 'trackName': 'Song', 'plainLyrics': 'wrong'}]))
    assert await client.find_automatic('¥$', 'Song') == (None, 'missing')
