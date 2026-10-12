from backend.app.clients.spotify import _parse_embed_tracks


def test_spotify_explicit_badge_is_not_part_of_artist_name():
    tracks = _parse_embed_tracks("<h3>Say What's Real</h3><h4>EDrake</h4><h3>Fireworks</h3><h4>EDrake, Alicia Keys</h4><h3>Seeing Green</h3><h4>ENicki Minaj, Drake, Lil Wayne</h4>")
    assert tracks == [
        {"artist": "Drake", "title": "Say What's Real", "album": "", "duration": None},
        {"artist": "Drake, Alicia Keys", "title": "Fireworks", "album": "", "duration": None},
        {"artist": "Nicki Minaj, Drake, Lil Wayne", "title": "Seeing Green", "album": "", "duration": None},
    ]


def test_spotify_explicit_badge_with_space_is_not_part_of_artist_name():
    assert _parse_embed_tracks("<h3>Track</h3><h4>E Drake</h4>")[0]["artist"] == "Drake"


def test_spotify_embed_next_data_extracts_playlist_name_and_artwork():
    import json
    from backend.app.clients.spotify import _parse_embed_playlist

    next_data = {
        "props": {
            "pageProps": {
                "state": {
                    "data": {
                        "entity": {
                            "name": "This Is Vasco Rossi",
                            "title": "This Is Vasco Rossi",
                            "coverArt": {
                                "sources": [
                                    {"url": "https://pickasso.spotifycdn.com/image/ab67c0de0000deef/dt/v1/img/thisisv3/5l4nhyz8876RhgE2d18h65/en", "width": None, "height": None}
                                ]
                            },
                            "trackList": [
                                {"title": "Una Canzone D'Amore Buttata Via", "subtitle": "Vasco Rossi", "duration": 271376},
                                {"title": "La Pioggia Alla Domenica (con Marracash)", "subtitle": "Vasco Rossi,\xa0Marracash", "duration": 212828},
                            ],
                        }
                    }
                }
            }
        }
    }
    html = f'<html><head><title data-next-head=""></title></head><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script></body></html>'
    parsed = _parse_embed_playlist(html)

    assert parsed["name"] == "This Is Vasco Rossi"
    assert parsed["artwork_url"] == "https://pickasso.spotifycdn.com/image/ab67c0de0000deef/dt/v1/img/thisisv3/5l4nhyz8876RhgE2d18h65/en"
    assert parsed["tracks"] == [
        {"artist": "Vasco Rossi", "title": "Una Canzone D'Amore Buttata Via", "album": "", "duration": 271376},
        {"artist": "Vasco Rossi, Marracash", "title": "La Pioggia Alla Domenica (con Marracash)", "album": "", "duration": 212828},
    ]

