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
