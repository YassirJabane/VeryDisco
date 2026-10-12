import pytest
from mutagen.id3 import ID3, TPUB, TXXX

from backend.app.database import Database
from backend.app.sync import embed_metadata


@pytest.mark.asyncio
async def test_album_tracklist_persists_and_updates_on_refresh(test_db: Database):
    key = ("drake", "scaryhours2")
    tracks = [
        {"title": "What's Next", "track_position": 1, "disk_number": 1},
        {"title": "Wants and Needs", "track_position": 2, "disk_number": 1},
    ]
    await test_db.save_album_tracklist("user", *key, "release-id", tracks)

    # Reading from a new Database instance proves this is SQLite state, not
    # process memory or a request-local MusicBrainz result.
    reopened = Database(test_db.db_path)
    assert (await reopened.get_album_tracklists("user"))[key]["tracks"] == tracks

    tracks.append({"title": "Lemon Pepper Freestyle", "track_position": 3, "disk_number": 1})
    await reopened.save_album_tracklist("user", *key, "release-id", tracks)
    assert (await test_db.get_album_tracklists("user"))[key]["total_tracks"] == 3

    await reopened.prune_album_tracklists("user", [])
    assert await test_db.get_album_tracklists("user") == {}


def test_import_replaces_advertising_tags(tmp_path):
    audio_path = tmp_path / "track.mp3"
    audio_path.write_bytes(b"")
    original = ID3()
    original.add(TPUB(encoding=3, text="www.t.me/pmedia_music"))
    original.add(TXXX(encoding=3, desc="PROMO", text="www.t.me/pmedia_music"))
    original.save(audio_path)

    embed_metadata(
        str(audio_path), artist="Drake", title="Lemon Pepper Freestyle",
        album="Scary Hours 2", album_artist="Drake", track_num=3,
        genre="www.t.me/pmedia_music",
    )

    tags = ID3(audio_path)
    assert tags.getall("TPUB") == []
    assert tags.getall("TCON") == []
    assert not any(frame.desc == "PROMO" for frame in tags.getall("TXXX"))
    assert tags.getall("TIT2")[0].text[0] == "Lemon Pepper Freestyle"
    assert tags.getall("TPE1")[0].text[0] == "Drake"
    assert tags.getall("TALB")[0].text[0] == "Scary Hours 2"


def test_musicbrainz_rejects_tribute_artist_and_scores_compilation_correctly():
    from backend.app.clients.musicbrainz import _is_matching_release_artist, score_release

    abba_release = {
        "title": "ABBA Gold: Greatest Hits",
        "status": "Official",
        "artist-credit": [{"name": "ABBA"}],
        "release-group": {
            "primary-type": "Album",
            "secondary-types": ["Compilation"],
        },
        "media": [{"format": "CD"}],
        "date": "1992-09-21",
    }
    tribute_release = {
        "title": "ABBA Gold",
        "status": "Official",
        "artist-credit": [{"name": "The Real ABBA Gold"}],
        "release-group": {
            "primary-type": "Album",
            "secondary-types": [],
        },
        "media": [{"format": "Digital Media"}],
        "date": "2010-01-01",
    }

    assert _is_matching_release_artist(abba_release, "ABBA") is True
    assert _is_matching_release_artist(tribute_release, "ABBA") is False
    assert score_release(abba_release, "ABBA Gold", "ABBA") > score_release(tribute_release, "ABBA Gold", "ABBA")
    assert score_release(tribute_release, "ABBA Gold", "ABBA") < 0


@pytest.mark.asyncio
async def test_fix_directory_tags_relocates_tribute_folder_to_canonical_artist(tmp_path, monkeypatch):
    from backend.app.sync import fix_directory_tags_and_rescan
    from backend.app.clients.musicbrainz import musicbrainz_client

    async def fake_get_album_tracklist(artist, album, release_mbid=None, force_refresh=False):
        return {
            "release_mbid": "993a3532-83ac-4e89-8596-45816806aac6",
            "title": "ABBA Gold",
            "album_artist": "ABBA",
            "release_date": "1992-09-21",
            "nb_tracks": 19,
            "nb_discs": 1,
            "tracks": [
                {"title": "Dancing Queen", "track_position": 1, "disk_number": 1, "disc_num": 1, "id": "rec-1"},
            ],
        }

    monkeypatch.setattr(musicbrainz_client, "get_album_tracklist", fake_get_album_tracklist)

    tribute_dir = tmp_path / "The Real ABBA Gold" / "ABBA Gold"
    tribute_dir.mkdir(parents=True)
    audio_path = tribute_dir / "The Real ABBA Gold_ABBA Gold_01_Dancing Queen.mp3"
    audio_path.write_bytes(b"")
    embed_metadata(
        str(audio_path),
        artist="The Real ABBA Gold",
        title="Dancing Queen",
        album="ABBA Gold",
        album_artist="The Real ABBA Gold",
        track_num=1,
    )

    details = await fix_directory_tags_and_rescan(
        dir_path=tribute_dir,
        target_artist="ABBA",
        target_album="ABBA Gold",
        music_dir=tmp_path,
        return_details=True,
    )

    assert details["updated_count"] == 1
    new_file = tmp_path / "ABBA" / "ABBA Gold" / "ABBA_ABBA Gold_01_Dancing Queen.mp3"
    assert new_file.is_file()
    assert not tribute_dir.exists()
    assert not (tmp_path / "The Real ABBA Gold").exists()

    tags = ID3(new_file)
    assert tags.getall("TPE1")[0].text[0] == "ABBA"
    assert tags.getall("TPE2")[0].text[0] == "ABBA"
    assert tags.getall("TALB")[0].text[0] == "ABBA Gold"


@pytest.mark.asyncio
async def test_inspect_album_releases_prefers_full_deluxe_over_single_via_alias_and_deezer_count(monkeypatch):
    import backend.app.clients.musicbrainz as mb_mod
    from backend.app.clients.deezer import deezer_client

    captured_queries = []

    async def fake_mb_get(path, params=None):
        if path == "/release":
            captured_queries.append((params or {}).get("query", ""))
            return {
                "releases": [
                    {
                        "id": "c59fae72-f604-4953-981e-3b0f42a122b0",
                        "title": "BULLY",
                        "status": "Pseudo-Release",
                        "disambiguation": "V1, altered",
                        "track-count": 1,
                        "artist-credit": [{"name": "Kanye West"}],
                        "release-group": {"id": "rg-1", "primary-type": "Album"},
                        "media": [{"format": "Digital Media", "track-count": 1}],
                    },
                    {
                        "id": "e398f34d-7f63-4b23-80d6-2d14942ea1b1",
                        "title": "BULLY",
                        "status": "Official",
                        "disambiguation": "deluxe",
                        "track-count": 20,
                        "artist-credit": [{"name": "Ye"}],
                        "release-group": {"id": "rg-1", "primary-type": "Album"},
                        "media": [{"format": "Digital Media", "track-count": 13}, {"format": "Digital Media", "track-count": 7}],
                    },
                ]
            }
        if path == "/release/e398f34d-7f63-4b23-80d6-2d14942ea1b1":
            return {
                "id": "e398f34d-7f63-4b23-80d6-2d14942ea1b1",
                "title": "BULLY",
                "date": "2026-03-27",
                "artist-credit": [{"name": "Ye"}],
                "media": [
                    {
                        "position": 1,
                        "format": "Digital Media",
                        "tracks": [{"position": i, "title": f"Track {i}", "recording": {"id": f"rec-{i}", "title": f"Track {i}"}} for i in range(1, 21)],
                    }
                ],
            }
        return None

    async def fake_search_album(artist, album):
        return {"id": 936785561, "title": "BULLY - DELUXE", "nb_tracks": 20, "artist": {"name": "Kanye West"}}

    monkeypatch.setattr(mb_mod, "_mb_get", fake_mb_get)
    monkeypatch.setattr(deezer_client, "search_album", fake_search_album)

    res = await mb_mod.inspect_album_releases("Kanye West", "BULLY - DELUXE", force_refresh=True)
    winner = res.get("winner")

    assert captured_queries
    assert 'artist:"Kanye West" OR artist:"Ye"' in captured_queries[0]
    assert winner is not None
    assert winner["id"] == "e398f34d-7f63-4b23-80d6-2d14942ea1b1"
    assert winner["discs"][0]["track_count"] == 20


