import pytest

from backend.app.clients.lrclib import LrcLibClient


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.params = []

    async def get(self, _url, params, **kwargs):
        self.params.append(params)
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_get_lyrics_falls_back_to_album_and_title(monkeypatch):
    fake = _Client([
        _Response([]),
        _Response([{
            "id": 1, "artistName": "Kanye West & Ty Dolla $ign",
            "albumName": "VULTURES 1",
            "trackName": "CARNIVAL", "plainLyrics": "fallback lyrics",
        }]),
        _Response([]),
    ])

    async def _client():
        return fake

    monkeypatch.setattr("backend.app.clients.lrclib.get_http_client", _client)
    lyrics, kind = await LrcLibClient().get_lyrics("¥$", "CARNIVAL", "VULTURES 1")

    assert (lyrics, kind) == ("fallback lyrics", "plain")
    assert fake.params == [
        {"artist_name": "¥$", "track_name": "CARNIVAL", "album_name": "VULTURES 1"},
        {"album_name": "VULTURES 1", "track_name": "CARNIVAL"},
        {"track_name": "CARNIVAL"},
    ]


@pytest.mark.asyncio
async def test_get_lyrics_propagates_instrumental_flag(monkeypatch):
    fake = _Client([_Response([{
        "id": 2, "artistName": "Artist", "trackName": "Interlude",
        "instrumental": True,
    }])])

    async def _client():
        return fake

    monkeypatch.setattr("backend.app.clients.lrclib.get_http_client", _client)
    assert await LrcLibClient().get_lyrics("Artist", "Interlude") == (None, "instrumental")


@pytest.mark.asyncio
async def test_title_only_fallback_never_marks_unrelated_artist_instrumental(monkeypatch):
    fake = _Client([_Response([]), _Response([{
        "id": 3, "artistName": "Another Artist", "trackName": "Interlude", "instrumental": True,
    }])])

    async def _client():
        return fake

    monkeypatch.setattr("backend.app.clients.lrclib.get_http_client", _client)
    assert await LrcLibClient().get_lyrics("¥$", "Interlude") == (None, "missing")
