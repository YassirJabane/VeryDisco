from backend.app.library_reader import calculate_album_total_tracks, match_official_album_tracks
from backend.app.main import _local_album_status


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


def test_local_six_of_six_tags_cannot_certify_a_full_album():
    assert _local_album_status(6, 6) == "unverified"
    assert _local_album_status(6, 9) == "partially"


def test_official_tracklist_reveals_missing_tracks_and_rejects_wrong_position():
    official = [
        {"title": title, "track_position": position, "disk_number": 1}
        for position, title in enumerate(
            ["Let's Go Crazy", "Take Me With U", "The Beautiful Ones", "Computer Blue",
             "Darling Nikki", "When Doves Cry", "I Would Die 4 U", "Baby I'm a Star", "Purple Rain"], 1
        )
    ]
    local = [
        {"title": title, "track_num": position, "disc_num": 1, "filepath": f"/{position}.mp3"}
        for position, title in enumerate(
            ["Let's Go Crazy", "Take Me With U", "The Beautiful Ones", "Computer Blue",
             "Darling Nikki", "When Doves Cry"], 1
        )
    ]
    local.append({"title": "Wrong Song", "track_num": 7, "disc_num": 1, "filepath": "/wrong.mp3"})
    result = match_official_album_tracks(official, local)
    assert len(result) == 9
    assert sum(track["exists"] for track in result) == 6
    assert all(track["verified"] for track in result)
    assert [track["title"] for track in result if not track["exists"]] == [
        "I Would Die 4 U", "Baby I'm a Star", "Purple Rain"
    ]
