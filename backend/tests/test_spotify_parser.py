from backend.app.clients.spotify import _parse_embed_tracks


def test_spotify_explicit_badge_is_not_part_of_artist_name():
    tracks = _parse_embed_tracks("<h3>Say What's Real</h3><h4>E Drake</h4><h3>Fireworks</h3><h4>E Drake, Alicia Keys</h4>")
    assert tracks == [
        {"artist": "Drake", "title": "Say What's Real", "album": "", "duration": None},
        {"artist": "Drake, Alicia Keys", "title": "Fireworks", "album": "", "duration": None},
    ]
