from pathlib import Path
import pytest
from backend.app.sync import (
    extract_base_album_name,
    normalize_album_name,
    find_existing_album_folder,
    find_superseded_album_folders,
    upgrade_or_merge_album_folders,
    consolidate_all_split_albums,
)

def test_extract_base_album_name_and_normalization():
    assert extract_base_album_name("Habibti (fomo)") == "Habibti"
    assert extract_base_album_name("Habibti (FOMO)") == "Habibti"
    assert extract_base_album_name("Habibti") == "Habibti"
    assert extract_base_album_name("Habibti (Deluxe Edition)") == "Habibti"
    assert extract_base_album_name("Habibti [Explicit]") == "Habibti"
    assert extract_base_album_name("Habibti - Deluxe") == "Habibti"
    assert extract_base_album_name("ABBA Gold (Anniversary Edition) (1992)") == "ABBA Gold"
    assert extract_base_album_name("Thriller (25th Anniversary Deluxe Edition)") == "Thriller"

    assert normalize_album_name("Habibti (fomo)") == "habibti"
    assert normalize_album_name("Habibti") == "habibti"
    assert normalize_album_name("Habibti (FOMO)") == normalize_album_name("Habibti")


def test_find_existing_album_folder_does_not_collapse_edition_into_base(tmp_path):
    artist_dir = tmp_path / "Drake"
    artist_dir.mkdir(parents=True)
    base_folder = artist_dir / "Habibti"
    base_folder.mkdir()

    # Upgraded edition should not collapse into the base album folder
    assert find_existing_album_folder(artist_dir, "Habibti (fomo)") is None

    # Exact matching base album returns the base folder
    assert find_existing_album_folder(artist_dir, "Habibti") == base_folder
    assert find_existing_album_folder(artist_dir, "habibti") == base_folder


def test_find_superseded_album_folders(tmp_path):
    artist_dir = tmp_path / "Drake"
    artist_dir.mkdir(parents=True)
    base_folder = artist_dir / "Habibti"
    base_folder.mkdir()

    # When target is an edition, the base folder is detected as superseded
    superseded = find_superseded_album_folders(artist_dir, "Habibti (fomo)")
    assert superseded == [base_folder]

    # When target matches exact folder, it is not superseded
    assert find_superseded_album_folders(artist_dir, "Habibti") == []


def test_upgrade_or_merge_album_folders_migrates_tracks_and_cleans_up_old_folder(tmp_path, monkeypatch):
    artist_dir = tmp_path / "Drake"
    base_folder = artist_dir / "Habibti"
    base_folder.mkdir(parents=True)

    # Create dummy audio file in base folder
    old_file = base_folder / "Drake - Habibti - 01 - First Track.mp3"
    old_file.write_bytes(b"dummy mp3 audio content")
    old_lrc = base_folder / "Drake - Habibti - 01 - First Track.lrc"
    old_lrc.write_text("[00:01.00] Test lyrics", encoding="utf-8")

    # Mock embed_metadata to avoid needing real audio tag parser on dummy bytes
    monkeypatch.setattr("backend.app.sync.embed_metadata", lambda **kwargs: None)
    monkeypatch.setattr("backend.app.sync.get_file_audio_info", lambda p: ("mp3", 320, 16, 44100))

    official_tracks = [
        {"title": "First Track", "track_position": 1, "disk_number": 1},
        {"title": "FOMO Track", "track_position": 2, "disk_number": 1},
    ]

    migrated = upgrade_or_merge_album_folders(
        music_dir=tmp_path,
        artist="Drake",
        target_album="Habibti (fomo)",
        official_album_tracks=official_tracks,
    )

    assert len(migrated) == 1
    upgraded_folder = artist_dir / "Habibti (fomo)"
    assert upgraded_folder.exists()
    assert (upgraded_folder / "Drake_Habibti (fomo)_01_First Track.mp3").exists()
    assert (upgraded_folder / "Drake_Habibti (fomo)_01_First Track.lrc").exists()

    # The superseded base folder must be completely removed
    assert not base_folder.exists()


def test_consolidate_all_split_albums(tmp_path, monkeypatch):
    artist_dir = tmp_path / "Drake"
    base_folder = artist_dir / "Habibti"
    base_folder.mkdir(parents=True)
    edition_folder = artist_dir / "Habibti (fomo)"
    edition_folder.mkdir(parents=True)

    # Base folder has track 1
    (base_folder / "Drake - Habibti - 01 - Track 1.mp3").write_bytes(b"audio 1")
    # Edition folder has track 2
    (edition_folder / "Drake - Habibti (fomo) - 02 - Track 2.mp3").write_bytes(b"audio 2")

    monkeypatch.setattr("backend.app.sync.embed_metadata", lambda **kwargs: None)
    monkeypatch.setattr("backend.app.sync.get_file_audio_info", lambda p: ("mp3", 320, 16, 44100))

    consolidated = consolidate_all_split_albums(tmp_path)
    assert len(consolidated) == 1
    assert consolidated[0]["target_album"] == "Habibti (fomo)"

    # Base folder was deleted and its track was migrated into the edition folder
    assert not base_folder.exists()
    assert (edition_folder / "Drake_Habibti (fomo)_01_Track 1.mp3").exists()
    assert (edition_folder / "Drake - Habibti (fomo) - 02 - Track 2.mp3").exists()
