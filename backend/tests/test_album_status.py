from backend.app.main import (
    _album_completion_status,
    _matching_album_rows,
    _select_deezer_artist,
)


def test_album_with_missing_tracks_is_not_full():
    assert _album_completion_status(6, expected_tracks=9) == "partial"


def test_album_is_full_only_at_known_total():
    assert _album_completion_status(9, expected_tracks=9) == "full"


def test_unknown_album_total_is_not_guessed_from_four_tracks():
    assert _album_completion_status(6) == "partial"


def test_external_release_total_can_certify_completion():
    assert _album_completion_status(6, expected_tracks=9) == "partial"
    assert _album_completion_status(9, expected_tracks=9) == "full"


def test_collaborative_album_matches_each_artist_page_by_title():
    rows = [{
        "artist_norm": "21savagemetroboomin",
        "album_norm": "savagemodeii",
        "title": "Runnin",
    }]
    exact = {("21savagemetroboomin", "savagemodeii"): rows}
    by_title = {"savagemodeii": rows}

    assert _matching_album_rows(exact, by_title, "21savage", "savagemodeii") == rows
    assert _matching_album_rows(exact, by_title, "metroboomin", "savagemodeii") == rows


def test_deezer_artist_selection_uses_established_exact_name_not_first_result():
    candidates = [
        {"id": 204768937, "name": "Abba", "nb_fan": 4, "nb_album": 1},
        {"id": 393059191, "name": "Abba", "nb_fan": 20, "nb_album": 16},
        {"id": 180, "name": "ABBA", "nb_fan": 12_000_000, "nb_album": 31},
        {"id": 1, "name": "ABBA Cover Band", "nb_fan": 99_000_000, "nb_album": 80},
    ]

    assert _select_deezer_artist("ABBA", candidates)["id"] == 180
