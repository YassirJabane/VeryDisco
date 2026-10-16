import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.app.clients import lrclib
from backend.app.clients.lrclib import LrcLibClient, LrcLibUnavailable
from backend.app.lyrics_autosearch import new_progress, run_autosearch


@pytest.fixture(autouse=True)
def clean_gate(monkeypatch):
    monkeypatch.setattr(lrclib, '_next_request', 0)
    monkeypatch.setattr(lrclib, '_unavailable_until', 0)
    monkeypatch.setattr(lrclib, '_request_lock', asyncio.Lock())
    monkeypatch.setattr(lrclib.asyncio, 'sleep', AsyncMock())


def response(code, payload=None, **headers):
    return httpx.Response(code, json=[] if payload is None else payload,
                          headers=headers, request=httpx.Request('GET', 'https://lrclib.net/api/search'))


def http(monkeypatch, responses):
    get = AsyncMock(side_effect=responses)
    monkeypatch.setattr(lrclib, 'get_http_client', AsyncMock(return_value=SimpleNamespace(get=get)))
    return get


@pytest.mark.asyncio
@pytest.mark.parametrize('first', [500, 502, 503, 504, 408, 'timeout', 'invalid-json', 'invalid-shape'])
async def test_transient_failure_then_success_retries_same_query(monkeypatch, first):
    if first == 'timeout':
        bad = httpx.ReadTimeout('temporary')
    elif first == 'invalid-json':
        bad = httpx.Response(200, text='<html>proxy</html>', request=response(200).request)
    elif first == 'invalid-shape':
        bad = response(200, {'error': 'invalid payload'})
    else:
        bad = response(first)
    get = http(monkeypatch, [bad, response(200, [{'id': 1}])])
    assert await LrcLibClient()._search({'track_name': 'good day'}) == [{'id': 1}]
    assert get.await_count == 2
    assert get.call_args_list[0] == get.call_args_list[1]
    assert lrclib.asyncio.sleep.await_args_list[1].args[0] > 1.9


@pytest.mark.asyncio
async def test_three_failures_open_shared_circuit_without_nine_download_retries(monkeypatch):
    get = http(monkeypatch, [response(503), response(503), response(503)])
    with pytest.raises(LrcLibUnavailable):
        await LrcLibClient().get_lyrics('Artist', 'Song')
    assert get.await_count == 3
    assert lrclib.asyncio.sleep.await_args_list[2].args[0] > 3.9
    with pytest.raises(LrcLibUnavailable):
        await LrcLibClient()._search({'track_name': 'Another song'})
    assert get.await_count == 3
    assert lrclib._unavailable_until > lrclib.time.monotonic() + 59


@pytest.mark.asyncio
async def test_circuit_recovers_after_cooldown(monkeypatch):
    monkeypatch.setattr(lrclib, '_unavailable_until', lrclib.time.monotonic() - 1)
    get = http(monkeypatch, [response(200, [{'id': 1}])])
    assert await LrcLibClient()._search({}) == [{'id': 1}]
    get.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_after_respected_and_long_delay_does_not_hold_job_asleep(monkeypatch):
    http(monkeypatch, [response(503, **{'Retry-After': '10'}), response(200)])
    assert await LrcLibClient()._search({}) == []
    assert lrclib.asyncio.sleep.await_args_list[1].args[0] > 9.9
    get = http(monkeypatch, [response(503, **{'Retry-After': '120'})])
    with pytest.raises(LrcLibUnavailable):
        await LrcLibClient()._search({})
    get.assert_awaited_once()
    assert lrclib._unavailable_until > lrclib.time.monotonic() + 119


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [400, 401, 403, 429])
async def test_non_transient_errors_are_not_retried(monkeypatch, code):
    get = http(monkeypatch, [response(code)])
    with pytest.raises(httpx.HTTPStatusError):
        await LrcLibClient()._search({})
    get.assert_awaited_once()


@pytest.mark.asyncio
async def test_synced_confident_match_stops_before_broad_fallbacks(monkeypatch):
    get = http(monkeypatch, [response(200, [dict(id=1, artistName='Artist', trackName='Song',
                                               duration=200, syncedLyrics='[00:01]correct')])])
    assert await LrcLibClient().find_automatic('Artist', 'Song', 'Album', 200) == ('[00:01]correct', 'synced')
    get.assert_awaited_once()


@pytest.mark.asyncio
async def test_wrong_duration_synced_result_does_not_prevent_valid_fallback(monkeypatch):
    get = http(monkeypatch, [
        response(200, [dict(id=1, artistName='Artist', trackName='Song', duration=300, syncedLyrics='[00:01]wrong')]),
        response(200, [dict(id=2, artistName='Artist', trackName='Song', duration=200, syncedLyrics='[00:01]correct')]),
    ])
    assert await LrcLibClient().find_automatic('Artist', 'Song', 'Album', 200) == ('[00:01]correct', 'synced')
    assert get.await_count == 2


@pytest.mark.asyncio
async def test_bulk_stops_on_outage_without_touching_plain_or_next_tracks(monkeypatch, tmp_path):
    rows = []
    for title in ['One', 'Two', 'Three']:
        audio = tmp_path / f'{title}.mp3'
        audio.write_bytes(b'audio')
        rows.append(dict(filepath=str(audio), artist='Artist', title=title, album='Album', duration=200))
    (tmp_path / 'One.lrc').write_text('manual plain lyrics')
    db = SimpleNamespace(query_library_lyrics_missing=AsyncMock(return_value=rows),
                         get_instrumental_overrides=AsyncMock(return_value=set()),
                         refresh_library_paths=AsyncMock(), delete_cache=AsyncMock())
    get = http(monkeypatch, [response(503), response(503), response(503)])
    progress = new_progress()
    await run_autosearch(db, 'user', tmp_path, progress, AsyncMock())
    assert progress['status'] == 'service_unavailable'
    assert (progress['processed'], progress['failed'], progress['not_found']) == (1, 1, 0)
    assert get.await_count == 3
    assert (tmp_path / 'One.lrc').read_text() == 'manual plain lyrics'
    assert not (tmp_path / 'Two.lrc').exists()
    db.refresh_library_paths.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_outage_in_download_is_not_reported_as_missing(monkeypatch):
    get = http(monkeypatch, [response(200), response(503), response(503), response(503)])
    with pytest.raises(LrcLibUnavailable):
        await LrcLibClient().get_lyrics('Artist', 'Song')
    assert get.await_count == 4


@pytest.mark.asyncio
async def test_cancellation_is_not_retried(monkeypatch):
    get = http(monkeypatch, [asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await LrcLibClient()._search({})
    get.assert_awaited_once()


@pytest.mark.parametrize('value', ['', 'nonsense', 'nan', 'inf', '-1'])
def test_invalid_retry_after_does_not_create_permanent_cooldown(value):
    assert lrclib.retry_after_seconds(response(503, **{'Retry-After': value})) == 0


def test_http_date_retry_after_is_understood():
    from datetime import datetime, timezone, timedelta
    from email.utils import format_datetime
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True)
    delay = lrclib.retry_after_seconds(response(503, **{'Retry-After': future}))
    assert 118 < delay <= 120


@pytest.mark.asyncio
async def test_bulk_recovers_from_503_and_saves_synced_upgrade(monkeypatch, tmp_path):
    audio = tmp_path / 'song.mp3'
    audio.write_bytes(b'audio unchanged')
    sidecar = audio.with_suffix('.lrc')
    sidecar.write_text('old plain text')
    db = SimpleNamespace(query_library_lyrics_missing=AsyncMock(return_value=[dict(
        filepath=str(audio), artist='Artist', title='Song', album='Album', duration=200)]),
        get_instrumental_overrides=AsyncMock(return_value=set()),
        refresh_library_paths=AsyncMock(), delete_cache=AsyncMock())
    get = http(monkeypatch, [response(503, **{'Retry-After': '1'}), response(200, [dict(
        id=1, artistName='Artist', trackName='Song', duration=200, syncedLyrics='[00:01]upgrade')])])
    progress, notify = new_progress(), AsyncMock()
    await run_autosearch(db, 'user', tmp_path, progress, notify)
    assert progress['status'] == 'completed'
    assert progress['synced'] == 1
    assert progress['failed'] == progress['not_found'] == 0
    assert get.await_count == 2
    assert sidecar.read_text() == '[00:01]upgrade'
    assert audio.read_bytes() == b'audio unchanged'
    db.refresh_library_paths.assert_awaited_once()
    notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_search_outage_returns_service_error_not_empty_results(monkeypatch):
    from backend.app import main
    from starlette.requests import Request
    from fastapi import HTTPException
    monkeypatch.setattr('backend.app.auth.get_current_user', AsyncMock(return_value={'id': 'user'}))
    http(monkeypatch, [response(503), response(503), response(503)])
    with pytest.raises(HTTPException) as error:
        await main.search_lyrics_endpoint('Artist', 'Song', Request({'type': 'http'}))
    assert error.value.status_code == 503
