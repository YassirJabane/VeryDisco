from backend.app.main import _album_completion_status


def test_album_with_missing_tracks_is_not_full():
    rows = [{"total_tracks": 9}] * 6
    assert _album_completion_status(6, rows) == "partial"


def test_album_is_full_only_at_known_total():
    rows = [{"total_tracks": 9}] * 9
    assert _album_completion_status(9, rows) == "full"


def test_unknown_album_total_is_not_guessed_from_four_tracks():
    assert _album_completion_status(6) == "partial"
