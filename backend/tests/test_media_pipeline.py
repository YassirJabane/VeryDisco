import asyncio
import json
from pathlib import Path

import pytest

from backend.app.clients.acoustid import AcoustIDClient
from backend.app.clients.musicbrainz import _parse_artist_credit
from backend.app.sync import find_downloaded_file, safe_copy_file, safe_move_file, extract_main_artist, embed_metadata
from backend.app.clients.deezer import DeezerClient, _result_matches
from backend.app.album_sync import match_file_to_official_track


def test_musicbrainz_artist_credits_preserve_joinphrases():
    display, artists = _parse_artist_credit([
        {"name": "Artist A", "joinphrase": " feat. "},
        {"name": "Artist B", "joinphrase": ""},
    ])
    assert display == "Artist A feat. Artist B"
    assert artists == ["Artist A", "Artist B"]
    assert extract_main_artist("Florence and the Machine") == "Florence and the Machine"


def test_id3_preserves_display_and_explicit_artist_entities(tmp_path):
    from mutagen.id3 import ID3

    path = tmp_path / "song.mp3"
    path.write_bytes(b"")
    embed_metadata(
        str(path), "Artist A feat. Artist B", "Song", "Album",
        album_artist="Artist A", credited_artists=["Artist A", "Artist B"],
        cover_bytes=b"\x89PNG\r\n\x1a\n" + b"cover",
    )
    tags = ID3(path)
    assert tags["TPE1"].text == ["Artist A feat. Artist B"]
    assert tags["TPE2"].text == ["Artist A"]
    assert tags["TXXX:artists"].text == ["Artist A", "Artist B"]
    assert tags.getall("APIC")[0].mime == "image/png"


def test_explicit_title_feature_is_added_without_splitting_band_name(tmp_path):
    from mutagen.id3 import ID3

    path = tmp_path / "feature.mp3"
    path.write_bytes(b"")
    embed_metadata(
        str(path), "Florence and the Machine", "Song (feat. Guest)", "Album",
        album_artist="Florence and the Machine", credited_artists=["Florence and the Machine"],
    )
    tags = ID3(path)
    assert tags["TPE1"].text == ["Florence and the Machine feat. Guest"]
    assert tags["TPE2"].text == ["Florence and the Machine"]
    assert tags["TXXX:artists"].text == ["Florence and the Machine", "Guest"]


def test_deezer_metadata_requires_same_artist_and_title():
    assert _result_matches({"artist": {"name": "Artist"}, "title": "Song"}, "Artist", "Song")
    assert not _result_matches({"artist": {"name": "Other Artist"}, "title": "Song"}, "Artist", "Song")


def test_album_track_number_requires_correct_disc():
    tracks = [
        {"title": "Intro One", "disk_number": 1, "track_position": 1},
        {"title": "Intro Two", "disk_number": 2, "track_position": 1},
    ]
    assert match_file_to_official_track("2-01 Intro Two.flac", tracks) is tracks[1]
    assert match_file_to_official_track("01 unknown.flac", tracks) is None


@pytest.mark.asyncio
async def test_deezer_cover_skips_ambiguous_text_match(monkeypatch):
    client = DeezerClient()

    async def results(url):
        return {"data": [
            {"title": "Album", "artist": {"name": "Other Artist"}, "cover_xl": "wrong"},
            {"title": "Album", "artist": {"name": "Artist"}, "cover_xl": "right"},
        ]}

    async def cover(url):
        return url.encode()

    monkeypatch.setattr(client, "_request_json", results)
    monkeypatch.setattr(client, "download_cover_art", cover)
    assert await client.get_album_cover("Artist", "Album") == b"right"


@pytest.mark.asyncio
async def test_musicbrainz_rejects_unrelated_first_result(monkeypatch):
    import backend.app.clients.musicbrainz as mb

    async def response(path, params):
        if path == "/recording":
            return {"recordings": [{"title": "Song", "artist-credit": [{"name": "Other Artist"}]}]}
        return {"releases": [{"title": "Wrong Album"}]}

    monkeypatch.setattr(mb, "_mb_get", response)
    assert await mb.musicbrainz_client.search_recording("Artist", "Song") is None
    assert await mb.musicbrainz_client.search_release("Artist", "Album") is None


def test_download_match_requires_size_and_unambiguous_name(tmp_path):
    first = tmp_path / "user1" / "Song.mp3"
    first.parent.mkdir()
    first.write_bytes(b"a" * 100)
    assert find_downloaded_file(str(tmp_path), "Song.mp3", 100) == first
    assert find_downloaded_file(str(tmp_path), "Song.mp3", 200) is None
    second = tmp_path / "user2" / "Song.mp3"
    second.parent.mkdir()
    second.write_bytes(b"b" * 100)
    assert find_downloaded_file(str(tmp_path), "Song.mp3", 100) is None


def test_safe_copy_and_move_do_not_publish_partial_file(tmp_path, monkeypatch):
    source = tmp_path / "source.mp3"
    destination = tmp_path / "destination.mp3"
    source.write_bytes(b"good audio")
    destination.write_bytes(b"previous audio")
    import backend.app.sync as sync

    def broken_copy(src, dst):
        dst.write(b"partial")
        raise OSError("interrupted copy")

    monkeypatch.setattr(sync.shutil, "copyfileobj", broken_copy)
    with pytest.raises(OSError):
        safe_copy_file(source, destination)
    assert destination.read_bytes() == b"previous audio"
    assert source.read_bytes() == b"good audio"
    assert not list(tmp_path.glob(".verydisco-*"))

    monkeypatch.setattr(sync.os, "replace", lambda *args: (_ for _ in ()).throw(OSError(18, "cross device")))
    with pytest.raises(OSError):
        safe_move_file(source, destination)
    assert destination.read_bytes() == b"previous audio"
    assert source.exists()


@pytest.mark.asyncio
async def test_acoustid_fingerprint_json_and_strict_identity(monkeypatch, tmp_path):
    client = AcoustIDClient()
    client._fpcalc_checked = True
    client._fpcalc_ok = True

    class Process:
        returncode = 0

        async def communicate(self):
            return json.dumps({"fingerprint": "abc", "duration": 180}).encode(), b""

    async def fake_exec(*args, **kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await client.generate_fingerprint(tmp_path / "song.mp3") == {"fingerprint": "abc", "duration": 180}

    monkeypatch.setattr(client, "get_api_key", lambda: "test")
    monkeypatch.setattr(client, "generate_fingerprint", lambda path: asyncio.sleep(0, result={"fingerprint": "abc", "duration": 180}))

    async def lookup(*args):
        return {"results": [{"score": 0.95, "recordings": [{"id": "recording", "title": "Song", "artists": [{"name": "Other Artist"}]}]}]}

    monkeypatch.setattr(client, "lookup_fingerprint", lookup)
    monkeypatch.setattr(client, "read_musicbrainz_recording_id", lambda path: None)
    valid, _ = await client.verify_track_against_metadata(Path("song.mp3"), "Expected Artist", "Song")
    assert valid is False
