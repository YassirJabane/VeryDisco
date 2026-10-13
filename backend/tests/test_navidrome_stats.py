import pytest

from backend.app.clients.navidrome import NavidromeClient


@pytest.mark.asyncio
async def test_server_stats_page_all_albums_and_sum_real_song_counts(monkeypatch):
    class Response:
        status_code = 200
        def __init__(self, payload):
            self.payload = payload
        def json(self):
            return {"subsonic-response": self.payload}
        def raise_for_status(self):
            return None

    class Client:
        async def get(self, url, params=None):
            if url.endswith("getArtists.view"):
                return Response({
                    "status": "ok",
                    "artists": {"index": {"artist": {"id": "a", "name": "Artist"}}},
                })
            offset = params.get("offset", 0)
            albums = (
                [{"id": str(i), "songCount": 2} for i in range(500)]
                if offset == 0 else
                [{"id": "last", "songCount": 7}]
            )
            return Response({"status": "ok", "albumList2": {"album": albums}})

    async def get_client():
        return Client()

    monkeypatch.setattr("backend.app.clients.navidrome.get_http_client", get_client)
    stats = await NavidromeClient("http://navidrome", "user", "password").get_server_stats()
    assert stats == {"songs": 1007, "albums": 501, "artists": 1}
