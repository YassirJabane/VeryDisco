"""Useful source tags survive cleaning; provider values take precedence when known.

Never manufacture a genre, ISRC or original release date when providers lack it.
"""
from pathlib import Path
from typing import Any
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import functools
import os
import shutil
import tempfile


def atomic_tag_update(function):
    """Finish the complete wipe/rebuild on a private copy before publishing it."""
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        path = Path(args[0] if args else kwargs["file_path"])
        fd, name = tempfile.mkstemp(prefix=".verydisco-tags-", suffix=path.suffix, dir=path.parent)
        os.close(fd)
        staged = Path(name)
        backup = None
        try:
            shutil.copy2(path, staged)
            if args:
                function(str(staged), *args[1:], **kwargs)
            else:
                function(**{**kwargs, "file_path": str(staged)})
            if path.stat().st_nlink > 1:
                fd, name = tempfile.mkstemp(prefix=".verydisco-backup-", suffix=path.suffix, dir=path.parent)
                os.close(fd)
                backup = Path(name)
                shutil.copy2(path, backup)
                try:
                    shutil.copyfile(staged, path)
                    os.utime(path, None)
                except BaseException:
                    try:
                        shutil.copy2(backup, path)
                    except BaseException as restore_error:
                        recovery_path, backup = backup, None
                        raise RuntimeError(f'Tag restoration failed; recovery copy retained at {recovery_path}') from restore_error
                    raise
            else:
                os.replace(staged, path)
        finally:
            staged.unlink(missing_ok=True)
            if backup:
                backup.unlink(missing_ok=True)
    return wrapped

# Vorbis, ID3, MP4 mappings. Artist credits and release identity use the core writer.
FIELDS = {
    "genre": ("genre", "TCON", "©gen"),
    "bpm": ("bpm", "TBPM", "tmpo"),
    "mood": ("mood", "TMOO", "----:com.apple.iTunes:MOOD"),
    "isrc": ("isrc", "TSRC", "----:com.apple.iTunes:ISRC"),
    "label": ("label", "TPUB", "----:com.apple.iTunes:LABEL"),
    "original_date": ("originaldate", "TDOR", "----:com.apple.iTunes:ORIGINALDATE"),
    "release_group_mbid": ("musicbrainz_releasegroupid", "MusicBrainz Release Group Id", "----:com.apple.iTunes:MusicBrainz Release Group Id"),
    "artist_mbids": ("musicbrainz_artistid", "MusicBrainz Artist Id", "----:com.apple.iTunes:MusicBrainz Artist Id"),
    "album_artist_mbids": ("musicbrainz_albumartistid", "MusicBrainz Album Artist Id", "----:com.apple.iTunes:MusicBrainz Album Artist Id"),
    "barcode": ("barcode", "BARCODE", "----:com.apple.iTunes:BARCODE"),
    "instrumental": ("instrumental", "INSTRUMENTAL", "----:com.apple.iTunes:INSTRUMENTAL"),
    "composer": ("composer", "TCOM", "©wrt"),
    "lyricist": ("lyricist", "TEXT", "----:com.apple.iTunes:LYRICIST"),
    "conductor": ("conductor", "TPE3", "----:com.apple.iTunes:CONDUCTOR"),
    "copyright": ("copyright", "TCOP", "cprt"),
    "language": ("language", "TLAN", "----:com.apple.iTunes:LANGUAGE"),
    "disc_subtitle": ("discsubtitle", "TSST", "----:com.apple.iTunes:DISCSUBTITLE"),
    "replaygain_track_gain": ("replaygain_track_gain", "REPLAYGAIN_TRACK_GAIN", "----:com.apple.iTunes:replaygain_track_gain"),
    "replaygain_track_peak": ("replaygain_track_peak", "REPLAYGAIN_TRACK_PEAK", "----:com.apple.iTunes:replaygain_track_peak"),
    "replaygain_album_gain": ("replaygain_album_gain", "REPLAYGAIN_ALBUM_GAIN", "----:com.apple.iTunes:replaygain_album_gain"),
    "replaygain_album_peak": ("replaygain_album_peak", "REPLAYGAIN_ALBUM_PEAK", "----:com.apple.iTunes:replaygain_album_peak"),
}


def _values(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        value = [value]
    return [v.decode("utf-8") if isinstance(v, bytes) else str(v) for v in value if v not in (None, "")]


def normalize_extras(values: dict) -> dict:
    """Reject invalid tempo without replacing an existing useful source value.

    BPM is single-valued. Keep fractional precision in text tags; only MP4's
    unsigned integer tmpo atom requires rounding. Empty values still mean delete
    to the explicit extras writer, but an absent/invalid provider value does not.
    """
    result = dict(values)
    if "bpm" in result:
        data = _values(result["bpm"])
        if not data:
            result["bpm"] = []
        else:
            try:
                tempo = Decimal(data[0].strip())
                if len(data) != 1 or not tempo.is_finite() or not 1 <= tempo <= 65535:
                    raise ValueError("Invalid BPM")
                result["bpm"] = [format(tempo.normalize(), "f")]
            except (InvalidOperation, ValueError):
                result.pop("bpm")
    return result


def read_extras(path: str | Path) -> dict:
    from mutagen import File
    from mutagen.id3 import ID3
    suffix = Path(path).suffix.lower()
    tags = ID3(path) if suffix == ".mp3" else File(path)
    result = {}
    for field, (vorbis, id3, mp4) in FIELDS.items():
        if suffix == ".mp3":
            frame = tags.get(id3 if len(id3) == 4 else f"TXXX:{id3}")
            if frame is None and len(id3) != 4:
                frame = next((f for f in tags.getall('TXXX') if f.desc.casefold().replace('_', ' ') == id3.casefold().replace('_', ' ')), None)
            value = getattr(frame, "text", [])
        else:
            value = tags.get(mp4 if suffix in {".m4a", ".mp4"} else vorbis, []) if tags else []
            if field == "mood" and not value and tags and suffix in {".m4a", ".mp4"}:
                # Both MOOD and mood freeform spellings occur in the wild;
                # Navidrome normalizes atom names case-insensitively too.
                value = next((tags[key] for key in tags.keys() if key.casefold() == mp4.casefold()), [])
        result[field] = _values(value)
    return {**result, "bpm": normalize_extras(result).get("bpm", [])}


def write_extras(path: str | Path, values: dict) -> None:
    from mutagen import File
    from mutagen import id3
    from mutagen.id3 import ID3, TXXX
    from mutagen.mp4 import MP4FreeForm
    suffix = Path(path).suffix.lower()
    tags = ID3(path) if suffix == ".mp3" else File(path)
    if tags is None:
        raise ValueError(f"Unsupported audio format: {suffix}")
    values = normalize_extras(values)
    for field, (vorbis, frame_name, mp4) in FIELDS.items():
        if field not in values:
            continue
        data = _values(values.get(field))
        if suffix in {".m4a", ".mp4"} and field == "mood":
            for key in list(tags.keys()):
                if key.casefold() == mp4.casefold():
                    del tags[key]
        if not data:
            if suffix == '.mp3':
                tags.delall(frame_name if len(frame_name) == 4 and hasattr(id3, frame_name) else f'TXXX:{frame_name}')
            else:
                tags.pop(mp4 if suffix in {'.m4a', '.mp4'} else vorbis, None)
            continue
        if suffix == ".mp3":
            if len(frame_name) == 4 and hasattr(id3, frame_name):
                tags.add(getattr(id3, frame_name)(encoding=3, text=data))
            else:
                tags.add(TXXX(encoding=3, desc=frame_name, text=data))
        elif suffix in {".m4a", ".mp4"}:
            if field == "bpm":
                tags[mp4] = [int(Decimal(data[0]).quantize(Decimal("1"), rounding=ROUND_HALF_UP))]
            else:
                tags[mp4] = [MP4FreeForm(v.encode("utf-8")) for v in data] if mp4.startswith("----") else data
        else:
            tags[vorbis] = data
    tags.save(path, v2_version=4) if suffix == ".mp3" else tags.save()
