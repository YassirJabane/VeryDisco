from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from backend.app import main
from backend.app.clients.navidrome import NavidromeClient
from backend.app.config import AppConfig


def response(payload, code=200):
    return httpx.Response(code, json={'subsonic-response': payload}, request=httpx.Request('GET', 'http://navidrome.invalid'))


@pytest.mark.asyncio
async def test_failed_navidrome_request_is_not_an_empty_library(monkeypatch):
    client = SimpleNamespace(get=AsyncMock(return_value=response({}, 503)))
    monkeypatch.setattr('backend.app.clients.navidrome.get_http_client', AsyncMock(return_value=client))
    with pytest.raises(httpx.HTTPStatusError):
        await NavidromeClient('http://navidrome.invalid', 'user', 'secret').get_server_stats()


@pytest.mark.asyncio
async def test_partial_album_pagination_is_not_published_as_complete_stats(monkeypatch):
    client = SimpleNamespace(get=AsyncMock(side_effect=[
        response({'status': 'ok', 'artists': {'index': []}}),
        response({'status': 'ok', 'albumList2': {'album': [{'id': str(i), 'songCount': 10} for i in range(500)]}}),
        response({'status': 'failed', 'error': {'message': 'server unavailable'}}),
    ]))
    monkeypatch.setattr('backend.app.clients.navidrome.get_http_client', AsyncMock(return_value=client))
    with pytest.raises(ValueError, match='album statistics'):
        await NavidromeClient('http://navidrome.invalid', 'user', 'secret').get_server_stats()


@pytest.mark.asyncio
async def test_repeating_album_pages_cannot_loop_forever(monkeypatch):
    page = response({'status': 'ok', 'albumList2': {'album': [{'id': str(i), 'songCount': 1} for i in range(500)]}})
    client = SimpleNamespace(get=AsyncMock(side_effect=[response({'status': 'ok', 'artists': {}}), page, page]))
    monkeypatch.setattr('backend.app.clients.navidrome.get_http_client', AsyncMock(return_value=client))
    with pytest.raises(ValueError, match='repeated'):
        await NavidromeClient('http://navidrome.invalid', 'user', 'secret').get_server_stats()


@pytest.mark.asyncio
async def test_real_empty_navidrome_library_still_has_zero_counts(monkeypatch):
    client = SimpleNamespace(get=AsyncMock(side_effect=[response({'status': 'ok', 'artists': {}}), response({'status': 'ok', 'albumList2': {}})]))
    monkeypatch.setattr('backend.app.clients.navidrome.get_http_client', AsyncMock(return_value=client))
    assert await NavidromeClient('http://navidrome.invalid', 'user', 'secret').get_server_stats() == {'songs': 0, 'albums': 0, 'artists': 0}


@pytest.mark.asyncio
async def test_stats_endpoint_does_not_inherit_global_credentials_for_regular_user(monkeypatch):
    cfg = AppConfig(listenbrainz={'username': 'alice'}, slskd={'base_url': 'http://slskd.invalid'}, navidrome={'url': 'http://navidrome.invalid', 'username': 'alice', 'password': 'global-secret'})
    monkeypatch.setattr(main, 'config_manager', SimpleNamespace(config=cfg, is_configured=True))
    monkeypatch.setattr('backend.app.auth.get_current_user', AsyncMock(return_value={'id': 'alice', 'is_admin': False}))
    monkeypatch.setattr(main, 'db', SimpleNamespace(get_user_by_id=AsyncMock(return_value={'username': 'alice'})))
    get = AsyncMock()
    monkeypatch.setattr(NavidromeClient, 'get_server_stats', get)
    result = await main.get_navidrome_stats(None)
    assert result['available'] is False and result['songs'] is None
    get.assert_not_awaited()


@pytest.mark.asyncio
async def test_regular_users_cannot_read_other_users_global_logs(monkeypatch):
    monkeypatch.setattr('backend.app.auth.get_current_user', AsyncMock(return_value={'id': 'alice', 'is_admin': False}))
    with pytest.raises(HTTPException) as error:
        await main.stream_logs(None)
    assert error.value.status_code == 403
