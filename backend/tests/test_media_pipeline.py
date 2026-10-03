import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.clients.acoustid import AcoustIDClient
from backend.app.clients.musicbrainz import _parse_artist_credit
from backend.app.sync import find_downloaded_file, safe_copy_file, safe_move_file, extract_main_artist, embed_metadata
from backend.app.clients.deezer import DeezerClient, _result_matches
from backend.app.album_sync import match_file_to_official_track, official_position_for_file


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


def test_joint_album_artists_are_separate_clickable_entities(tmp_path):
    from mutagen.id3 import ID3

    path = tmp_path / "her-loss.mp3"
    path.write_bytes(b"")
    embed_metadata(
        str(path), "Drake & 21 Savage", "Rich Flex", "Her Loss",
        album_artist="Drake & 21 Savage",
        credited_artists=["Drake", "21 Savage"],
        album_artists=["Drake", "21 Savage"],
        album_artist_mbids=["drake-mbid", "21-mbid"],
    )
    tags = ID3(path)
    assert tags["TPE1"].text == ["Drake & 21 Savage"]
    assert tags["TPE2"].text == ["Drake & 21 Savage"]
    assert tags["TXXX:artists"].text == ["Drake", "21 Savage"]
    assert tags["TXXX:albumartists"].text == ["Drake", "21 Savage"]
    assert tags["TXXX:musicbrainz album artist id"].text == ["drake-mbid", "21-mbid"]


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


def test_album_completion_keeps_pre_download_official_position():
    tracks = [{"title": "Playlist .", "disk_number": 1, "track_position": 3}]
    # The final filename no longer contains the provider's trailing punctuation.
    assert official_position_for_file(
        {"_official_position": (1, 3)}, Path("Artist_Album_03_Playlist.mp3"), tracks
    ) == (1, 3)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("expected_artist,recording_artists,expected_valid", [
    ("Drake & 21 Savage", ["Drake", "21 Savage"], True),
    ("Drake & 21 Savage feat. Travis Scott", ["Drake", "21 Savage", "Travis Scott"], True),
    ("Drake and 21 Savage", ["Drake", "21 Savage"], True),
    ("Artist A with Artist B", ["Artist A", "Artist B"], True),
    ("Florence and the Machine", ["Florence and the Machine"], True),
    ("Drake & 21 Savage", ["Drake"], False),
    ("Drake & 21 Savage", ["Drake", "Future"], False),
])
async def test_acoustid_joint_artist_credit(monkeypatch, expected_artist, recording_artists, expected_valid):
    client = AcoustIDClient()
    monkeypatch.setattr(client, "get_api_key", lambda: "test")
    monkeypatch.setattr(client, "generate_fingerprint", lambda path: asyncio.sleep(0, result={"fingerprint": "abc", "duration": 180}))
    monkeypatch.setattr(client, "read_musicbrainz_recording_id", lambda path: None)

    async def lookup(*args):
        return {"results": [{"score": 0.95, "recordings": [{
            "title": "Major Distribution",
            "artists": [{"name": name} for name in recording_artists],
        }]}]}

    monkeypatch.setattr(client, "lookup_fingerprint", lookup)
    valid, _ = await client.verify_track_against_metadata(
        Path("song.mp3"), expected_artist, "Major Distribution"
    )
    assert valid is expected_valid


@pytest.mark.asyncio
async def test_single_track_never_keeps_acoustid_mismatch_at_retry_limit(monkeypatch, tmp_path):
    import backend.app.album_sync as album_sync
    import backend.app.sync as sync
    from backend.app.clients.acoustid import acoustid_client

    candidates = [
        {"username": "peer", "filename": f"track{i}.mp3", "size": 100}
        for i in (1, 2)
    ]
    for candidate in candidates:
        (tmp_path / candidate["filename"]).write_bytes(b"wrong audio")

    class FakeSlskd:
        def __init__(self, **kwargs):
            pass

        async def search_candidates(self, **kwargs):
            return candidates, None

        async def request_download(self, *args):
            return True

        async def get_download_progress(self, *args):
            return "succeeded", "file-id", 100

        async def delete_download(self, *args):
            pass

    class FakeClient:
        def __init__(self, **kwargs):
            pass

    async def metadata(*args):
        return {
            "artist": "Drake", "title": "Song", "album": "Album", "track_num": 1,
            "cover_bytes": None, "album_artist": "Drake", "date": None,
        }

    async def mismatch(*args, **kwargs):
        return False, "Different recording"

    monkeypatch.setattr(album_sync, "SlskdClient", FakeSlskd)
    monkeypatch.setattr(album_sync, "LrcLibClient", FakeClient)
    monkeypatch.setattr(album_sync, "DeezerClient", FakeClient)
    monkeypatch.setattr(album_sync, "fetch_track_metadata_with_fallback", metadata)
    monkeypatch.setattr(sync, "find_downloaded_file", lambda root, name, size: tmp_path / name)
    monkeypatch.setattr(acoustid_client, "verify_track_against_metadata", mismatch)

    config = SimpleNamespace(
        paths=SimpleNamespace(music_dir=str(tmp_path / "music"), navidrome_playlists_dir=str(tmp_path / "playlists")),
        listenbrainz=SimpleNamespace(active_playlists=[]),
        slskd=SimpleNamespace(base_url="http://slskd", api_key="", downloads_dir=str(tmp_path), audio_quality={}),
        lyrics=SimpleNamespace(base_url="http://lrclib"),
        timeouts=SimpleNamespace(http_seconds=5, search_seconds=5, download_seconds=5),
        schedule=SimpleNamespace(max_candidate_attempts=2),
        acoustid=SimpleNamespace(max_retries=2),
    )
    assert await album_sync.download_single_track_task("Drake", "Song", "Album", config, force=True) is False
    assert not list((tmp_path / "music").rglob("*.mp3"))
    assert not (tmp_path / "track1.mp3").exists()
    assert not (tmp_path / "track2.mp3").exists()


@pytest.mark.asyncio
async def test_explore_promotion_uses_metadata_fetched_on_app_event_loop(monkeypatch, tmp_path):
    import backend.app.sync as sync
    from backend.app.scheduler import _promote_track_sync

    source = tmp_path / "explore" / "Drake - Song.mp3"
    source.parent.mkdir()
    source.write_bytes(b"audio")
    destination_dir = tmp_path / "music" / "Drake" / "Album"
    tagged = []
    monkeypatch.setattr(sync, "resolve_album_dir", lambda *args, **kwargs: (destination_dir, "Drake", "Album"))
    monkeypatch.setattr(sync, "get_library_filename", lambda *args: "01 - Song.mp3")
    monkeypatch.setattr(sync, "embed_metadata", lambda **kwargs: tagged.append(kwargs))
    monkeypatch.setattr(sync, "update_m3u_references", lambda *args: None)

    destination = await asyncio.to_thread(
        _promote_track_sync, source, tmp_path / "music", "Drake", "Song", "Album",
        tmp_path / "playlists", None, {"artist": "Drake", "title": "Song", "album": "Album"},
    )
    assert destination == destination_dir / "01 - Song.mp3"
    assert destination.read_bytes() == b"audio"
    assert tagged[0]["artist"] == "Drake"
