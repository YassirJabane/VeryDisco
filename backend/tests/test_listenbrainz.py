import pytest
import respx
import httpx
from backend.app.clients.listenbrainz import ListenBrainzClient

@pytest.mark.asyncio
@respx.mock
async def test_resolve_playlist_mbid_success():
    username = "testuser"
    playlist_source = "weekly-exploration"
    client = ListenBrainzClient(username=username, playlist_source=playlist_source)

    # Mock playlists endpoint
    mock_playlists_payload = {
        "playlists": [
            {
                "playlist": {
                    "title": "Random Playlist",
                    "identifier": "https://listenbrainz.org/playlist/11111111-1111-1111-1111-111111111111",
                    "extension": {
                        "listenbrainz.org": {
                            "algorithm_metadata": {
                                "source_patch": "weekly-jams"
                            }
                        }
                    }
                }
            },
            {
                "playlist": {
                    "title": "Weekly Exploration 2026-07-06",
                    "identifier": "https://listenbrainz.org/playlist/22222222-2222-2222-2222-222222222222",
                    "created": "2026-07-06T08:00:00Z",
                    "extension": {
                        "listenbrainz.org": {
                            "algorithm_metadata": {
                                "source_patch": "weekly-exploration"
                            }
                        }
                    }
                }
            }
        ]
    }
    
    respx.get(f"https://api.listenbrainz.org/1/user/{username}/playlists/createdfor").respond(
        status_code=200, json=mock_playlists_payload
    )

    mbid = await client.resolve_playlist_mbid()
    assert mbid == "22222222-2222-2222-2222-222222222222"

@pytest.mark.asyncio
@respx.mock
async def test_get_playlist_tracks():
    client = ListenBrainzClient(username="testuser", playlist_source="weekly-exploration")
    mbid = "22222222-2222-2222-2222-222222222222"

    mock_tracks_payload = {
        "playlist": {
            "title": "Weekly Exploration",
            "track": [
                {
                    "creator": "Daft Punk",
                    "title": "One More Time"
                },
                {
                    "creator": "Justice",
                    "title": "D.A.N.C.E."
                }
            ]
        }
    }

    respx.get(f"https://api.listenbrainz.org/1/playlist/{mbid}").respond(
        status_code=200, json=mock_tracks_payload
    )

    tracks = await client.get_playlist_tracks(mbid)
    assert len(tracks) == 2
    assert tracks[0]["artist"] == "Daft Punk"
    assert tracks[0]["title"] == "One More Time"
    assert tracks[1]["artist"] == "Justice"
    assert tracks[1]["title"] == "D.A.N.C.E."
