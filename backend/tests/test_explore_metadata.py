from backend.app.main import _itunes_track_matches


def test_itunes_enrichment_rejects_an_unrequested_remix():
    requested_artist = "The Weeknd"
    requested_title = "Blinding Lights"

    assert not _itunes_track_matches(
        {"artistName": "The Weeknd", "trackName": "Blinding Lights (Remix)"},
        requested_artist,
        requested_title,
    )
    assert _itunes_track_matches(
        {"artistName": "The Weeknd", "trackName": "Blinding Lights"},
        requested_artist,
        requested_title,
    )


def test_itunes_enrichment_uses_primary_artist_but_keeps_exact_track_title():
    assert _itunes_track_matches(
        {"artistName": "Tyler, The Creator", "trackName": "See You Again"},
        "Tyler, The Creator feat. Kali Uchis",
        "See You Again",
    )
    assert not _itunes_track_matches(
        {"artistName": "Tyler, The Creator", "trackName": "See You Again (Remix)"},
        "Tyler, The Creator feat. Kali Uchis",
        "See You Again",
    )
