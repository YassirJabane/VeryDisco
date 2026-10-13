import pytest
from backend.app.sync import promote_playlist_staging, restore_previous_playlist, recover_playlist_backups


def test_promotion_keeps_previous_generation_for_rollback(tmp_path):
    output = tmp_path / "weekly"
    staging = tmp_path / ".staging_weekly"
    output.mkdir()
    staging.mkdir()
    (output / "old.mp3").write_bytes(b"old")
    (output / "cover.jpg").write_bytes(b"art")
    (staging / "new.mp3").write_bytes(b"new")

    backup = promote_playlist_staging(staging, output)
    assert backup is not None and (backup / "old.mp3").read_bytes() == b"old"
    assert (output / "new.mp3").read_bytes() == b"new"
    assert (output / "cover.jpg").read_bytes() == b"art"

    restore_previous_playlist(output, "test")
    assert (output / "old.mp3").read_bytes() == b"old"
    assert (tmp_path / ".weekly.interrupted_test" / "new.mp3").read_bytes() == b"new"


def test_promotion_of_new_playlist_has_no_backup(tmp_path):
    staging = tmp_path / ".staging_new"
    output = tmp_path / "new"
    staging.mkdir()
    (staging / "song.mp3").write_bytes(b"song")

    assert promote_playlist_staging(staging, output) is None
    assert (output / "song.mp3").read_bytes() == b"song"


def test_startup_recovery_restores_old_playlist(tmp_path):
    output = tmp_path / "weekly"
    staging = tmp_path / ".staging_weekly"
    output.mkdir()
    staging.mkdir()
    (output / "old.mp3").write_bytes(b"old")
    (staging / "new.mp3").write_bytes(b"new")
    promote_playlist_staging(staging, output)

    recover_playlist_backups([tmp_path])
    assert (output / "old.mp3").read_bytes() == b"old"
    assert list(tmp_path.glob(".weekly.interrupted_*"))


@pytest.mark.asyncio
async def test_save_playlist_artwork_and_m3u_names_and_sidecars(tmp_path, monkeypatch):
    from backend.app.sync import save_playlist_artwork_and_m3u

    # Create old source m3u
    playlist_dir = tmp_path / "spotify-spotify-playlist"
    playlist_dir.mkdir(parents=True)
    old_m3u = playlist_dir / "spotify-spotify-playlist.m3u"
    old_m3u.write_text("old content", encoding="utf-8")

    class MockResp:
        status_code = 200
        content = b"fake-jpg-content"

    class MockClient:
        async def get(self, url, timeout=15):
            return MockResp()

    async def mock_get_client():
        return MockClient()

    monkeypatch.setattr("backend.app.clients.http_client.get_http_client", mock_get_client)

    m3u_res = await save_playlist_artwork_and_m3u(
        playlists_dir=str(tmp_path),
        music_dir=str(tmp_path),
        playlist_source="spotify-spotify-playlist",
        playlist_name="This Is Vasco Rossi",
        artwork_url="https://example.com/vasco.jpg",
        tracks=[],
    )

    # 1. Output file must be named This Is Vasco Rossi.m3u
    assert m3u_res.name == "This Is Vasco Rossi.m3u"
    assert m3u_res.exists()

    # 2. Old source-named m3u must be cleaned up
    assert not old_m3u.exists()

    # 3. Sidecar cover images must be created
    assert (playlist_dir / "This Is Vasco Rossi.jpg").read_bytes() == b"fake-jpg-content"
    assert (playlist_dir / "cover.jpg").read_bytes() == b"fake-jpg-content"
    assert (playlist_dir / "folder.jpg").read_bytes() == b"fake-jpg-content"

    # 4. Header must have playlist title
    content = m3u_res.read_text(encoding="utf-8")
    assert "#PLAYLIST:This Is Vasco Rossi" in content
