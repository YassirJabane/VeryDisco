from backend.app.main import _collect_naming_mismatches
from pathlib import Path


def test_naming_audit_rechecks_filename_folder_and_case():
    music_dir = Path("C:/test-music")
    rows = [
        {
            "filepath": str(music_dir / "Artist" / "2024 - Album" / "01 - Good.mp3"),
            "artist": "Artist", "album": "Album", "title": "Good",
            "track_num": 1, "year": "2024", "issue_naming": 0,
        },
        {
            "filepath": str(music_dir / "Wrong Artist" / "2024 - Album" / "01 - Good.mp3"),
            "artist": "Artist", "album": "Album", "title": "Good",
            "track_num": 1, "year": "2024", "issue_naming": 0,
        },
        {
            "filepath": str(music_dir / "Artist" / "2024 - Album" / "bad.mp3"),
            "artist": "Artist", "album": "Album", "title": "Good",
            "track_num": 1, "year": "2024", "issue_naming": 0,
        },
        {
            "filepath": str(music_dir / "artist" / "2024 - Album" / "01 - Good.mp3"),
            "artist": "Artist", "album": "Album", "title": "Good",
            "track_num": 1, "year": "2024", "issue_naming": 0,
        },
    ]
    mismatches = _collect_naming_mismatches(
        rows, music_dir, "{artist}/{year} - {album}/{track:02d} - {title}"
    )
    assert [item["current_relative"] for item in mismatches] == [
        "Wrong Artist\\2024 - Album\\01 - Good.mp3",
        "Artist\\2024 - Album\\bad.mp3",
        "artist\\2024 - Album\\01 - Good.mp3",
    ]
    assert all(item["expected_relative"] == "Artist\\2024 - Album\\01 - Good.mp3" for item in mismatches)
