from backend.app.main import _album_completion_status


def test_album_with_missing_tracks_is_not_full():
    assert _album_completion_status(6, expected_tracks=9) == "partial"


def test_album_is_full_only_at_known_total():
    assert _album_completion_status(9, expected_tracks=9) == "full"


def test_unknown_album_total_is_not_guessed_from_four_tracks():
    assert _album_completion_status(6) == "partial"


def test_external_release_total_can_certify_completion():
    assert _album_completion_status(6, expected_tracks=9) == "partial"
    assert _album_completion_status(9, expected_tracks=9) == "full"
