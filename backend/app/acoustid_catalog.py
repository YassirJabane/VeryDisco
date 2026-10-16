"""One library scope for AcoustID statistics, incremental scans and rechecks."""
from pathlib import Path
from backend.app.library_reader import AUDIO_SUFFIXES


def in_acoustid_library(row, music_dir=None, playlist_dir=None):
    path = Path(row['filepath'])
    if music_dir is not None:
        root = Path(music_dir).resolve()
        path = path.resolve()
        if not path.is_relative_to(root):
            return False
        parts = path.relative_to(root).parts[:-1]
    else:
        parts = path.parts[:-1]
    if playlist_dir is not None and path.resolve().is_relative_to(Path(playlist_dir).resolve()):
        return False
    excluded = {'explore', 'explore tracks', 'exploretracks', 'playlists', 'navidrome_playlists', 'staging'}
    if any(part.startswith('.') or part.casefold() in excluded for part in parts):
        return False
    return path.suffix.casefold() in AUDIO_SUFFIXES
