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
