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
