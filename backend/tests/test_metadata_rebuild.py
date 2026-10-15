import asyncio
from pathlib import Path

import pytest
from mutagen.id3 import COMM, ID3, TALB, TIT2, TPE1, TPE2, TPOS, TRCK

from backend.app.metadata_pipeline.providers import (
    ArtworkProvider, MusicBrainzReleaseProvider, artist_credit, normalize,
)
from backend.app.metadata_pipeline.service import (
    MetadataRebuildService,
    _preserve_cosmetic_spelling,
    _preserve_cosmetic_spellings,
)
from backend.app.metadata_pipeline.tags import read_track, write_track


@pytest.mark.parametrize(("local", "canonical", "expected"), [
    ("JAY-Z", "JAŸ-Z", "JAY-Z"),
    ("Beyonce", "Beyoncé", "Beyonce"),
    ("AC/DC", "AC-DC", "AC/DC"),
    ("Travi$ Scott", "Travis Scott", "Travis Scott"),
])
def test_cosmetic_artist_spelling_is_preserved_generically(local, canonical, expected):
    assert _preserve_cosmetic_spelling(local, canonical) == expected


def test_cosmetic_spelling_is_preserved_for_multi_artist_credits():
    assert _preserve_cosmetic_spellings(
        ["Beyonce", "JAY-Z"], ["Beyoncé", "JAŸ-Z", "Kanye West"]
    ) == ["Beyonce", "JAY-Z", "Kanye West"]


def test_joint_release_credit_preserves_local_jay_z_typography():
    assert _preserve_cosmetic_spelling(
        "Jay‐Z & Kanye West", "JAY-Z & Kanye West"
    ) == "Jay‐Z & Kanye West"


def _make_tagged_mp3(
    path: Path,
    *,
    title: str,
    artist: str,
    album: str,
    album_artist: str,
    track: int,
) -> None:
    tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=artist))
    tags.add(TPE2(encoding=3, text=album_artist))
    tags.add(TALB(encoding=3, text=album))
    tags.add(TRCK(encoding=3, text=f"{track}/2"))
    tags.add(TPOS(encoding=3, text="1/1"))
    tags.add(COMM(encoding=3, lang="eng", desc="source", text="keep me"))
    tags.save(path)


def _release() -> dict:
    return {
        "release_mbid": "release-1",
        "release_group_mbid": "group-1",
        "album": "Birds in the Trap Sing McKnight",
        "album_artist": "Travis Scott",
        "album_artists": [{"name": "Travis Scott", "mbid": "artist-1"}],
        "date": "2016-09-02",
        "country": "US",
        "status": "Official",
        "barcode": "",
        "labels": [],
        "disc_total": 1,
        "track_total": 2,
        "confidence": 0.98,
        "artwork_url": "",
        "tracks": [
            {
                "title": "the ends", "artist": "Travis Scott",
                "artists": [{"name": "Travis Scott", "mbid": "artist-1"}],
                "recording_mbid": "recording-1", "track": 1, "disc": 1,
                "duration": 0,
            },
            {
                "title": "goosebumps", "artist": "Travis Scott feat. Kendrick Lamar",
                "artists": [
                    {"name": "Travis Scott", "mbid": "artist-1"},
                    {"name": "Kendrick Lamar", "mbid": "artist-2"},
                ],
                "recording_mbid": "recording-2", "track": 2, "disc": 1,
                "duration": 0,
            },
        ],
    }


class FakeProvider:
    async def match_release(self, album, album_artist, local_tracks):
        return [_release()]

    async def release_details(self, release_mbid):
        return _release() if release_mbid == "release-1" else None


class NoArtwork:
    async def fetch(self, *args, **kwargs):
        return None, None, None


class FailingProvider:
    async def match_release(self, album, album_artist, local_tracks):
        raise RuntimeError("provider temporarily unavailable")


@pytest.mark.asyncio
async def test_musicbrainz_prefers_original_edition_and_release_group_date():
    def raw_release(release_id: str, release_date: str) -> dict:
        return {
            "id": release_id, "title": "Favourite Worst Nightmare", "date": release_date,
            "status": "Official", "country": "GB",
            "artist-credit": [{"artist": {"id": "arctic", "name": "Arctic Monkeys"}}],
            "release-group": {"id": "group", "first-release-date": "2007-04-18"},
            "media": [{"position": 1, "tracks": [{"position": 1, "recording": {
                "id": "recording", "title": "Brianstorm",
                "artist-credit": [{"artist": {"id": "arctic", "name": "Arctic Monkeys"}}],
            }}]}],
        }

    class Provider(MusicBrainzReleaseProvider):
        async def _get(self, path, params):
            if path == "/release":
                return {"releases": [
                    {"id": "reissue", "title": "Favourite Worst Nightmare", "date": "2022-01-01", "status": "Official",
                     "artist-credit": [{"artist": {"name": "Arctic Monkeys"}}]},
                    {"id": "original", "title": "Favourite Worst Nightmare", "date": "2007-04-18", "status": "Official",
                     "artist-credit": [{"artist": {"name": "Arctic Monkeys"}}]},
                ]}
            return raw_release(path.rsplit("/", 1)[-1], "2022-01-01" if path.endswith("reissue") else "2007-04-18")

    candidates = await Provider().match_release(
        "Favourite Worst Nightmare", "Arctic Monkeys", [{"title": "Brianstorm"}],
    )
    assert candidates[0]["release_mbid"] == "original"
    assert candidates[0]["date"] == "2007-04-18"
    assert candidates[0]["release_date"] == "2007-04-18"


@pytest.mark.asyncio
async def test_artwork_prefers_selected_release_before_digital_cover(monkeypatch):
    class Response:
        def __init__(self, status_code, content=b"", payload=None):
            self.status_code = status_code
            self.content = content
            self._payload = payload or {}

        def json(self):
            return self._payload

    class Client:
        async def get(self, url, **kwargs):
            if url == 'https://coverartarchive.org/release/release/front':
                return Response(200, b"\xff\xd8\xffedition")
            if "itunes.apple.com" in url:
                return Response(200, payload={"results": [{
                    "collectionName": "Scorpion", "artistName": "Drake",
                    "artworkUrl100": "https://art.example/100x100.jpg",
                }]})
            if "art.example" in url:
                return Response(200, b"\xff\xd8\xffdigital")
            raise AssertionError(f"CAA should not be used when an exact digital cover exists: {url}")

    async def client():
        return Client()

    monkeypatch.setattr("backend.app.metadata_pipeline.providers.get_http_client", client)
    data, mime, source = await ArtworkProvider().fetch("release", "group", "Drake", "Scorpion")
    assert data == b"\xff\xd8\xffedition"
    assert mime == "image/jpeg"
    assert source == "cover-art-archive:release"


class SlowProvider:
    async def match_release(self, album, album_artist, local_tracks):
        await asyncio.Event().wait()


def test_artist_credit_preserves_release_credit_and_stable_identity():
    display, entities = artist_credit([
        {"name": "Travi$ Scott", "artist": {"id": "a", "name": "Travis Scott"}, "joinphrase": " feat. "},
        {"name": "Kendrick Lamar", "artist": {"id": "b", "name": "Kendrick Lamar"}},
    ])
    assert display == "Travi$ Scott feat. Kendrick Lamar"
    assert [artist["name"] for artist in entities] == ["Travi$ Scott", "Kendrick Lamar"]
    assert [artist["canonical_name"] for artist in entities] == ["Travis Scott", "Kendrick Lamar"]
    assert [artist["mbid"] for artist in entities] == ["a", "b"]
    assert normalize("Travi$ Scott") == normalize("Travis Scott")


def test_renamed_artist_keeps_historical_release_credit_with_same_mbid():
    display, entities = artist_credit([{
        "name": "Kanye West",
        "artist": {"id": "164f0d73-1234-4e2c-8743-d77bf2191051", "name": "Ye"},
    }])

    assert display == "Kanye West"
    assert entities == [{
        "name": "Kanye West",
        "canonical_name": "Ye",
        "mbid": "164f0d73-1234-4e2c-8743-d77bf2191051",
    }]


def test_joint_historical_credit_keeps_jay_z_and_kanye_release_names():
    display, entities = artist_credit([
        {
            "name": "JAY-Z",
            "artist": {"id": "jay-z-mbid", "name": "JAŸ-Z"},
            "joinphrase": " & ",
        },
        {
            "name": "Kanye West",
            "artist": {"id": "ye-mbid", "name": "Ye"},
        },
    ])

    assert display == "JAY-Z & Kanye West"
    assert [artist["mbid"] for artist in entities] == ["jay-z-mbid", "ye-mbid"]
    assert [artist["canonical_name"] for artist in entities] == ["JAŸ-Z", "Ye"]


@pytest.mark.asyncio
async def test_rebuild_uses_historical_credit_and_stable_artist_mbid(tmp_path):
    ye_mbid = "164f0d73-1234-4e2c-8743-d77bf2191051"
    release = {
        "release_mbid": "graduation-release", "release_group_mbid": "graduation-group",
        "album": "Graduation", "album_artist": "Kanye West",
        "album_artists": [{"name": "Kanye West", "canonical_name": "Ye", "mbid": ye_mbid}],
        "date": "2007-09-11", "country": "US", "status": "Official",
        "barcode": "", "labels": [], "disc_total": 1, "track_total": 1,
        "confidence": 0.99, "artwork_url": "",
        "tracks": [{
            "title": "Stronger", "artist": "Kanye West",
            "artists": [{"name": "Kanye West", "canonical_name": "Ye", "mbid": ye_mbid}],
            "recording_mbid": "stronger-recording", "track": 1, "disc": 1, "duration": 0,
        }],
    }

    class RenamedArtistProvider:
        async def match_release(self, album, album_artist, local_tracks):
            return [release]

    album_dir = tmp_path / "Kanye West" / "Graduation"
    album_dir.mkdir(parents=True)
    track = album_dir / "01-Stronger.mp3"
    _make_tagged_mp3(
        track, title="Stronger", artist="Kanye West", album="Graduation",
        album_artist="Kanye West", track=1,
    )

    service = MetadataRebuildService(tmp_path / "state", provider=RenamedArtistProvider(), artwork=NoArtwork())
    plan = (await service.scan("user", tmp_path))[0]
    proposed = plan["tracks"][0]["proposed"]

    assert proposed["artist"] == "Kanye West"
    assert proposed["album_artist"] == "Kanye West"
    assert proposed["album_artist_mbid"] == ye_mbid


def test_writer_preserves_editorial_punctuation_and_joint_album_artists(tmp_path):
    path = tmp_path / "track.mp3"
    _make_tagged_mp3(
        path, title="old", artist="old", album="old", album_artist="old", track=1,
    )
    write_track(path, {
        "title": "Playlist .", "artist": "Drake & 21 Savage",
        "artists": ["Drake", "21 Savage"], "album": "Terrified .",
        "album_artist": "Drake & 21 Savage",
        "album_artists": ["Drake", "21 Savage"],
        "album_artist_mbid": "drake-mbid",
        "album_artist_mbids": ["drake-mbid", "21-mbid"],
        "track": 1, "track_total": 1, "disc": 1, "disc_total": 1,
        "date": "2026", "compilation": False,
    }, cover=None, replace_cover=False)
    written = read_track(path)
    assert written["title"] == "Playlist ."
    assert written["album"] == "Terrified ."
    assert written["album_artist"] == "Drake & 21 Savage"
    assert written["album_artists"] == ["Drake", "21 Savage"]
    assert written["album_artist_mbids"] == ["drake-mbid", "21-mbid"]


def test_persisted_active_status_is_reconciled_after_process_restart(tmp_path):
    service = MetadataRebuildService(tmp_path / "state", provider=FakeProvider())
    service.mark_queued("user")
    status = service.reconcile_orphaned("user", has_live_task=False)
    assert status["status"] == "interrupted"
    assert "start it again" in status["message"]

    service.mark_queued("user")
    assert service.reconcile_orphaned("user", has_live_task=True)["status"] == "queued"


@pytest.mark.asyncio
async def test_scan_is_read_only_and_builds_release_centric_plan(tmp_path):
    album_dir = tmp_path / "Travi$ Scott" / "Birds"
    album_dir.mkdir(parents=True)
    first = album_dir / "01-the-ends.mp3"
    second = album_dir / "02-goosebumps.mp3"
    _make_tagged_mp3(first, title="the ends", artist="Travi$ Scott", album="Birds in the Trap Sing McKnight", album_artist="Travi$ Scott", track=1)
    _make_tagged_mp3(second, title="goosebumps", artist="Travis Scott and Kendrick Lamar", album="Birds In The Trap Sing McKnight", album_artist="Travis Scott feat. Kendrick Lamar", track=2)
    before = {path: path.read_bytes() for path in (first, second)}

    service = MetadataRebuildService(tmp_path / "state", provider=FakeProvider(), artwork=NoArtwork())
    plans = await service.scan("user", tmp_path, aliases={"Travi$ Scott": "Travis Scott"})

    assert len(plans) == 1
    plan = plans[0]
    assert plan["selected_release"]["album_artist"] == "Travis Scott"
    assert plan["matched_tracks"] == plan["total_tracks"] == 2
    assert plan["tracks"][1]["proposed"]["artist"] == "Travis Scott feat. Kendrick Lamar"
    assert plan["tracks"][1]["proposed"]["artists"] == ["Travis Scott", "Kendrick Lamar"]
    assert plan["tracks"][1]["proposed"]["album_artists"] == ["Travis Scott"]
    assert all(path.read_bytes() == before[path] for path in (first, second))


@pytest.mark.asyncio
async def test_apply_preserves_unmanaged_tags_and_rollback_restores_managed_tags(tmp_path):
    album_dir = tmp_path / "album"
    album_dir.mkdir()
    first = album_dir / "01.mp3"
    second = album_dir / "02.mp3"
    _make_tagged_mp3(first, title="the ends", artist="Travi$ Scott", album="Birds in the Trap Sing McKnight", album_artist="Travi$ Scott", track=1)
    _make_tagged_mp3(second, title="goosebumps", artist="Travis Scott and Kendrick Lamar", album="Birds in the Trap Sing McKnight", album_artist="Travis Scott feat. Kendrick Lamar", track=2)
    playlist_dir = tmp_path / "playlists"
    playlist_dir.mkdir()
    linked_second = playlist_dir / "02.mp3"
    try:
        linked_second.hardlink_to(second)
    except OSError:
        linked_second = None

    service = MetadataRebuildService(tmp_path / "state", provider=FakeProvider(), artwork=NoArtwork())
    plan = (await service.scan("user", tmp_path, aliases={"Travi$ Scott": "Travis Scott"}))[0]
    result = await service.apply("user", plan["id"], tmp_path, include_artwork=False)

    assert result["updated_tracks"] == 2
    updated = read_track(second)
    assert updated["album_artist"] == "Travis Scott"
    assert updated["artist"] == "Travis Scott feat. Kendrick Lamar"
    assert updated["artists"] == ["Travis Scott", "Kendrick Lamar"]
    assert updated["release_mbid"] == "release-1"
    assert updated["recording_mbid"] == "recording-2"
    assert ID3(second).getall("COMM")[0].text == ["keep me"]
    if linked_second:
        assert read_track(linked_second)["album_artist"] == "Travis Scott"

    rollback = await service.rollback("user", plan["id"], tmp_path)
    assert rollback["restored_tracks"] == 2
    restored = read_track(second)
    assert restored["album_artist"] == "Travis Scott feat. Kendrick Lamar"
    assert restored["artist"] == "Travis Scott and Kendrick Lamar"
    assert ID3(second).getall("COMM")[0].text == ["keep me"]
    if linked_second:
        assert read_track(linked_second)["album_artist"] == "Travis Scott feat. Kendrick Lamar"


@pytest.mark.asyncio
async def test_provider_failure_is_isolated_to_album_plan(tmp_path):
    for number in (1, 2):
        album_dir = tmp_path / f"album-{number}"
        album_dir.mkdir()
        _make_tagged_mp3(
            album_dir / "track.mp3", title=f"track {number}", artist="Artist",
            album=f"Album {number}", album_artist="Artist", track=1,
        )
    service = MetadataRebuildService(tmp_path / "state", provider=FailingProvider(), artwork=NoArtwork())
    plans = await service.scan(7, tmp_path)
    assert len(plans) == 2
    assert all(plan["review"] == "unmatched" for plan in plans)
    assert all("provider temporarily unavailable" in plan["provider_error"] for plan in plans)
    assert service.status(7)["provider_errors"] == 2


@pytest.mark.asyncio
async def test_scan_reports_phases_and_can_be_cancelled_without_writing_media(tmp_path):
    album_dir = tmp_path / "album"
    album_dir.mkdir()
    track = album_dir / "track.mp3"
    _make_tagged_mp3(
        track, title="track", artist="Artist", album="Album",
        album_artist="Artist", track=1,
    )
    before = track.read_bytes()
    service = MetadataRebuildService(tmp_path / "state", provider=SlowProvider(), artwork=NoArtwork())
    task = asyncio.create_task(service.scan(9, tmp_path))
    for _ in range(100):
        status = service.status(9)
        if status.get("phase") == "resolving":
            break
        await asyncio.sleep(0.01)
    assert status["phase"] == "resolving"
    assert status["files_scanned"] == 1
    assert status["total"] == 1

    service.request_cancel(9)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.status(9)["status"] == "cancelled"
    assert track.read_bytes() == before


def test_index_inventory_reuses_unchanged_catalog_rows(tmp_path, monkeypatch):
    album_dir = tmp_path / "album"
    album_dir.mkdir()
    track = album_dir / "track.mp3"
    track.write_bytes(b"catalog-only fixture")
    stat = track.stat()
    row = {
        "filepath": str(track), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
        "title": "Track", "artist": "Artist", "artists_json": '["Artist"]',
        "album": "Album", "album_artist": "Artist", "year": "2020",
        "track_num": 1, "total_tracks": 1, "disc_num": 1, "total_discs": 1,
        "track_mbid": "recording", "album_mbid": "release", "duration": 123,
        "compilation": 0,
    }
    monkeypatch.setattr(
        "backend.app.metadata_pipeline.service.read_track",
        lambda path: (_ for _ in ()).throw(AssertionError("unchanged file was reparsed")),
    )
    service = MetadataRebuildService(tmp_path / "state", provider=FakeProvider(), artwork=NoArtwork())
    groups = service._inventory_from_index(tmp_path, {}, [row])
    assert groups[0]["tracks"][0]["release_mbid"] == "release"


def test_index_inventory_skips_unreadable_changed_file(tmp_path, monkeypatch):
    track = tmp_path / "unreadable.mp3"
    track.write_bytes(b"broken fixture")
    stat = track.stat()
    row = {
        "filepath": str(track), "size": stat.st_size - 1, "mtime_ns": stat.st_mtime_ns,
        "title": "Unreadable", "artist": "Artist", "artists_json": "[]",
        "album": "Album", "album_artist": "Artist",
    }
    monkeypatch.setattr(
        "backend.app.metadata_pipeline.service.read_track",
        lambda path: (_ for _ in ()).throw(OSError("Input/output error")),
    )
    service = MetadataRebuildService(tmp_path / "state", provider=FakeProvider(), artwork=NoArtwork())
    assert service._inventory_from_index(tmp_path, {}, [row]) == []


@pytest.mark.asyncio
async def test_musicbrainz_progressive_loading_and_persistent_cache():
    cache, calls = {}, []
    async def cache_get(key): return cache.get(key)
    async def cache_set(key, value, ttl): cache.__setitem__(key, value)

    class Provider(MusicBrainzReleaseProvider):
        async def _get(self, path, params):
            calls.append(path)
            if path == "/release":
                return {"releases": [{"id": "release-1", "title": "Album", "status": "Official",
                    "artist-credit": [{"artist": {"id": "artist", "name": "Artist"}}]}]}
            return {"id": "release-1", "title": "Album", "status": "Official", "date": "2020",
                "artist-credit": [{"artist": {"id": "artist", "name": "Artist"}}],
                "release-group": {"id": "group", "first-release-date": "2020"},
                "media": [{"position": 1, "tracks": [{"position": 1, "recording": {
                    "id": "recording", "title": "Track",
                    "artist-credit": [{"artist": {"id": "artist", "name": "Artist"}}]}}]}]}

    provider = Provider(cache_get, cache_set)
    local = [{"title": "Track", "artist": "Artist", "album": "Album"}]
    first = await provider.match_release("Album", "Artist", local)
    second = await provider.match_release("Album", "Artist", local)
    assert first[0]["confidence"] >= 0.9
    assert second[0]["release_mbid"] == "release-1"
    assert calls == ["/release", "/release/release-1"]


async def _rollback_fixture(tmp_path):
    album = tmp_path / "album"
    album.mkdir()
    tracks = [album / "01.mp3", album / "02.mp3"]
    for index, (path, title) in enumerate(zip(tracks, ("the ends", "goosebumps")), 1):
        _make_tagged_mp3(path, title=title, artist="Travi$ Scott", album="Birds in the Trap Sing McKnight", album_artist="Travi$ Scott", track=index)
    service = MetadataRebuildService(tmp_path / "state", provider=FakeProvider(), artwork=NoArtwork())
    plan = (await service.scan("user", album))[0]
    return service, plan, album, tracks


@pytest.mark.asyncio
async def test_apply_restores_current_file_when_writer_fails_after_modifying_it(tmp_path, monkeypatch):
    service, plan, album, tracks = await _rollback_fixture(tmp_path)
    snapshots = [read_track(path) for path in tracks]
    failed = False

    def fail_after_second_write(path, *args, **kwargs):
        nonlocal failed
        write_track(path, *args, **kwargs)
        if path == tracks[1] and not failed:
            failed = True
            raise OSError("simulated partial write failure")

    monkeypatch.setattr("backend.app.metadata_pipeline.service.write_track", fail_after_second_write)
    with pytest.raises(OSError, match="partial write"):
        await service.apply("user", plan["id"], album)
    assert [read_track(path)["artist"] for path in tracks] == [item["artist"] for item in snapshots]


@pytest.mark.asyncio
async def test_rollback_rejects_changes_made_after_apply(tmp_path):
    service, plan, album, tracks = await _rollback_fixture(tmp_path)
    await service.apply("user", plan["id"], album)
    changed = read_track(tracks[0])
    changed["title"] = "A newer manual correction"
    write_track(tracks[0], changed)
    with pytest.raises(RuntimeError, match="newer changes"):
        await service.rollback("user", plan["id"], album)
    assert read_track(tracks[0])["title"] == "A newer manual correction"


@pytest.mark.asyncio
async def test_cancelled_apply_finishes_writer_before_restoring_album(tmp_path, monkeypatch):
    import asyncio
    import threading
    service, plan, album, tracks = await _rollback_fixture(tmp_path)
    started, release = threading.Event(), threading.Event()
    first = True

    def slow_write(path, *args, **kwargs):
        nonlocal first
        if first:
            first = False
            started.set()
            assert release.wait(5)
        write_track(path, *args, **kwargs)

    monkeypatch.setattr("backend.app.metadata_pipeline.service.write_track", slow_write)
    task = asyncio.create_task(service.apply("user", plan["id"], album))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert read_track(tracks[0])["artist"] == "Travi$ Scott"
