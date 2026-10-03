import asyncio
from pathlib import Path

import pytest
from mutagen.id3 import COMM, ID3, TALB, TIT2, TPE1, TPE2, TPOS, TRCK

from backend.app.metadata_pipeline.providers import artist_credit, normalize
from backend.app.metadata_pipeline.service import MetadataRebuildService
from backend.app.metadata_pipeline.tags import read_track


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


class SlowProvider:
    async def match_release(self, album, album_artist, local_tracks):
        await asyncio.Event().wait()


def test_artist_credit_preserves_join_phrase_and_canonical_entities():
    display, entities = artist_credit([
        {"name": "Travi$ Scott", "artist": {"id": "a", "name": "Travis Scott"}, "joinphrase": " feat. "},
        {"name": "Kendrick Lamar", "artist": {"id": "b", "name": "Kendrick Lamar"}},
    ])
    assert display == "Travis Scott feat. Kendrick Lamar"
    assert [artist["name"] for artist in entities] == ["Travis Scott", "Kendrick Lamar"]
    assert normalize("Travi$ Scott") == normalize("Travis Scott")


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
