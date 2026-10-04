from backend.app.library_reader import calculate_album_total_tracks


def test_album_total_does_not_sum_repeated_album_total_per_disc():
    rows = [
        {"track_count": 12, "max_track_num": 12, "max_total_tracks": 25},
        {"track_count": 13, "max_track_num": 13, "max_total_tracks": 25},
    ]

    assert calculate_album_total_tracks(rows) == 25


def test_album_total_still_sums_observed_positions_across_discs():
    rows = [
        {"track_count": 10, "max_track_num": 10, "max_total_tracks": 10},
        {"track_count": 8, "max_track_num": 8, "max_total_tracks": 8},
    ]

    assert calculate_album_total_tracks(rows) == 18
