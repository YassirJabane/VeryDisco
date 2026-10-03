from __future__ import annotations

import base64
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional


AUDIO_SUFFIXES = {".mp3", ".flac", ".m4a", ".mp4", ".ogg"}


def _first(value: Any, default: str = "") -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default
    return str(value) if value not in (None, "") else default


def _number(value: Any, default: int = 0) -> int:
    try:
        return int(str(value or "").split("/")[0])
    except (TypeError, ValueError):
        return default


def _pair(value: Any) -> tuple[int, int]:
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, tuple):
        return int(value[0] or 0), int(value[1] or 0)
    text = str(value or "")
    parts = text.split("/", 1)
    return _number(parts[0]), _number(parts[1]) if len(parts) > 1 else 0


def read_track(path: Path, include_cover: bool = False) -> dict[str, Any]:
    """Read only fields managed by the rebuild pipeline plus audio identity data."""
    suffix = path.suffix.lower()
    result: dict[str, Any] = {
        "path": str(path), "title": "", "artist": "", "artists": [], "album": "",
        "album_artist": "", "album_artists": [], "album_artist_mbid": "",
        "album_artist_mbids": [], "date": "", "track": 0,
        "track_total": 0, "disc": 1, "disc_total": 1, "release_mbid": "",
        "recording_mbid": "", "compilation": False, "duration": 0.0,
    }
    cover: Optional[bytes] = None
    if suffix == ".mp3":
        from mutagen.id3 import ID3, ID3NoHeaderError
        from mutagen.mp3 import MP3
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        result.update({
            "title": _first(tags.get("TIT2").text if tags.get("TIT2") else ""),
            "artist": _first(tags.get("TPE1").text if tags.get("TPE1") else ""),
            "album": _first(tags.get("TALB").text if tags.get("TALB") else ""),
            "album_artist": _first(tags.get("TPE2").text if tags.get("TPE2") else ""),
            "date": _first(tags.get("TDRL").text if tags.get("TDRL") else (tags.get("TDRC").text if tags.get("TDRC") else "")),
            "compilation": _first(tags.get("TCMP").text if tags.get("TCMP") else "0") == "1",
        })
        result["track"], result["track_total"] = _pair(_first(tags.get("TRCK").text if tags.get("TRCK") else ""))
        result["disc"], result["disc_total"] = _pair(_first(tags.get("TPOS").text if tags.get("TPOS") else "1/1"))
        for frame in tags.getall("TXXX"):
            desc = frame.desc.casefold().replace("_", " ")
            if desc == "artists":
                result["artists"] = [str(value) for value in frame.text]
            elif desc == "albumartists":
                result["album_artists"] = [str(value) for value in frame.text]
            elif desc == "musicbrainz album id":
                result["release_mbid"] = _first(frame.text)
            elif desc == "musicbrainz album artist id":
                result["album_artist_mbid"] = _first(frame.text)
                result["album_artist_mbids"] = [str(value) for value in frame.text]
            elif desc == "musicbrainz recording id":
                result["recording_mbid"] = _first(frame.text)
        ufid = tags.get("UFID:http://musicbrainz.org")
        if ufid:
            result["recording_mbid"] = ufid.data.decode("utf-8", errors="ignore")
        pictures = tags.getall("APIC")
        cover = pictures[0].data if pictures else None
        try:
            result["duration"] = round(float(MP3(path).info.length), 2)
        except Exception:
            pass
    elif suffix == ".flac":
        from mutagen.flac import FLAC
        audio = FLAC(path)
        result.update({
            "title": _first(audio.get("title")), "artist": _first(audio.get("artist")),
            "artists": list(audio.get("artists", [])), "album": _first(audio.get("album")),
            "album_artist": _first(audio.get("albumartist") or audio.get("album artist")),
            "album_artists": list(audio.get("albumartists", [])),
            "album_artist_mbid": _first(audio.get("musicbrainz_albumartistid")),
            "album_artist_mbids": list(audio.get("musicbrainz_albumartistid", [])),
            "date": _first(audio.get("releasedate") or audio.get("date")),
            "release_mbid": _first(audio.get("musicbrainz_albumid")),
            "recording_mbid": _first(audio.get("musicbrainz_trackid") or audio.get("musicbrainz_recordingid")),
            "compilation": _first(audio.get("compilation"), "0") == "1",
            "duration": round(float(audio.info.length), 2),
        })
        result["track"] = _number(_first(audio.get("tracknumber")))
        result["track_total"] = _number(_first(audio.get("tracktotal") or audio.get("totaltracks")))
        result["disc"] = _number(_first(audio.get("discnumber")), 1)
        result["disc_total"] = _number(_first(audio.get("disctotal") or audio.get("totaldiscs")), 1)
        cover = audio.pictures[0].data if audio.pictures else None
    elif suffix == ".ogg":
        from mutagen.flac import Picture
        from mutagen.oggvorbis import OggVorbis
        audio = OggVorbis(path)
        result.update({
            "title": _first(audio.get("title")), "artist": _first(audio.get("artist")),
            "artists": list(audio.get("artists", [])), "album": _first(audio.get("album")),
            "album_artist": _first(audio.get("albumartist") or audio.get("album artist")),
            "album_artists": list(audio.get("albumartists", [])),
            "album_artist_mbid": _first(audio.get("musicbrainz_albumartistid")),
            "album_artist_mbids": list(audio.get("musicbrainz_albumartistid", [])),
            "date": _first(audio.get("releasedate") or audio.get("date")),
            "release_mbid": _first(audio.get("musicbrainz_albumid")),
            "recording_mbid": _first(audio.get("musicbrainz_trackid") or audio.get("musicbrainz_recordingid")),
            "compilation": _first(audio.get("compilation"), "0") == "1",
            "duration": round(float(audio.info.length), 2),
        })
        result["track"] = _number(_first(audio.get("tracknumber")))
        result["track_total"] = _number(_first(audio.get("tracktotal") or audio.get("totaltracks")))
        result["disc"] = _number(_first(audio.get("discnumber")), 1)
        result["disc_total"] = _number(_first(audio.get("disctotal") or audio.get("totaldiscs")), 1)
        pictures = audio.get("metadata_block_picture", [])
        if pictures:
            try:
                cover = Picture(base64.b64decode(pictures[0])).data
            except Exception:
                cover = None
    elif suffix in {".m4a", ".mp4"}:
        from mutagen.mp4 import MP4
        audio = MP4(path)
        def freeform(key: str) -> list[str]:
            values = audio.get(key, [])
            return [bytes(v).decode("utf-8", errors="ignore") for v in values]
        result.update({
            "title": _first(audio.get("\xa9nam")), "artist": _first(audio.get("\xa9ART")),
            "artists": freeform("----:com.apple.iTunes:artists"), "album": _first(audio.get("\xa9alb")),
            "album_artist": _first(audio.get("aART")),
            "album_artists": freeform("----:com.apple.iTunes:albumartists"),
            "album_artist_mbid": _first(freeform("----:com.apple.iTunes:MusicBrainz Album Artist Id")),
            "album_artist_mbids": freeform("----:com.apple.iTunes:MusicBrainz Album Artist Id"),
            "date": _first(audio.get("\xa9day")),
            "release_mbid": _first(freeform("----:com.apple.iTunes:MusicBrainz Album Id")),
            "recording_mbid": _first(freeform("----:com.apple.iTunes:MusicBrainz Track Id")),
            "compilation": bool(_first(audio.get("cpil"), "0") in {"1", "True", "true"}),
            "duration": round(float(audio.info.length), 2),
        })
        result["track"], result["track_total"] = _pair(audio.get("trkn"))
        result["disc"], result["disc_total"] = _pair(audio.get("disk"))
        covers = audio.get("covr", [])
        cover = bytes(covers[0]) if covers else None
    if not result["title"]:
        result["title"] = path.stem
    if include_cover:
        result["cover"] = base64.b64encode(cover).decode("ascii") if cover else ""
    result["mtime_ns"] = path.stat().st_mtime_ns
    result["size"] = path.stat().st_size
    return result


def write_track(
    path: Path,
    values: dict[str, Any],
    cover: Optional[bytes] = None,
    replace_cover: bool = False,
) -> None:
    """Atomically update managed tags while preserving lyrics, comments and custom tags."""
    path = path.resolve()
    hardlink_count = path.stat().st_nlink
    fd, temp_name = tempfile.mkstemp(prefix=".verydisco-metadata-", suffix=path.suffix, dir=str(path.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    backup_path: Optional[Path] = None
    try:
        shutil.copy2(path, temp_path)
        if path.suffix.lower() == ".mp3":
            _write_mp3(temp_path, values, cover, replace_cover)
        elif path.suffix.lower() == ".flac":
            _write_flac(temp_path, values, cover, replace_cover)
        elif path.suffix.lower() == ".ogg":
            _write_ogg(temp_path, values, cover, replace_cover)
        elif path.suffix.lower() in {".m4a", ".mp4"}:
            _write_mp4(temp_path, values, cover, replace_cover)
        else:
            raise ValueError(f"Unsupported audio format: {path.suffix}")
        if hardlink_count > 1:
            # Replacing the inode would silently detach playlist hardlinks.
            # For linked media, preserve the inode and keep a full temporary
            # recovery copy in case the in-place copy fails.
            backup_fd, backup_name = tempfile.mkstemp(
                prefix=".verydisco-metadata-backup-", suffix=path.suffix, dir=str(path.parent)
            )
            os.close(backup_fd)
            backup_path = Path(backup_name)
            shutil.copy2(path, backup_path)
            try:
                with temp_path.open("rb") as source, path.open("wb") as target:
                    shutil.copyfileobj(source, target)
                    target.flush()
                    os.fsync(target.fileno())
                shutil.copystat(temp_path, path)
            except Exception:
                shutil.copy2(backup_path, path)
                raise
        else:
            os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
        if backup_path:
            backup_path.unlink(missing_ok=True)


def _write_mp3(path: Path, values: dict[str, Any], cover: Optional[bytes], replace_cover: bool) -> None:
    from mutagen.id3 import (
        APIC, ID3, ID3NoHeaderError, TALB, TCMP, TDRC, TDRL, TIT2, TPE1, TPE2,
        TPOS, TRCK, TXXX, UFID,
    )
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    for key in ("TIT2", "TPE1", "TPE2", "TALB", "TRCK", "TPOS", "TDRC", "TDRL", "TCMP"):
        tags.delall(key)
    for frame in list(tags.getall("TXXX")):
        if frame.desc.casefold().replace("_", " ") in {
            "artists", "albumartists", "musicbrainz album id", "musicbrainz album artist id", "musicbrainz recording id",
        }:
            tags.delall(f"TXXX:{frame.desc}")
    tags.delall("UFID:http://musicbrainz.org")
    tags.add(TIT2(encoding=3, text=values.get("title") or ""))
    tags.add(TPE1(encoding=3, text=values.get("artist") or ""))
    tags.add(TPE2(encoding=3, text=values.get("album_artist") or ""))
    tags.add(TALB(encoding=3, text=values.get("album") or ""))
    tags.add(TRCK(encoding=3, text=_format_pair(values.get("track"), values.get("track_total"))))
    tags.add(TPOS(encoding=3, text=_format_pair(values.get("disc", 1), values.get("disc_total", 1))))
    tags.add(TCMP(encoding=3, text="1" if values.get("compilation") else "0"))
    if values.get("date"):
        tags.add(TDRC(encoding=3, text=str(values["date"])))
        tags.add(TDRL(encoding=3, text=str(values["date"])))
    if values.get("artists"):
        tags.add(TXXX(encoding=3, desc="artists", text=list(values["artists"])))
    if values.get("album_artists"):
        tags.add(TXXX(encoding=3, desc="albumartists", text=list(values["album_artists"])))
    if values.get("release_mbid"):
        tags.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=[values["release_mbid"]]))
    album_artist_mbids = values.get("album_artist_mbids") or ([values["album_artist_mbid"]] if values.get("album_artist_mbid") else [])
    if album_artist_mbids:
        tags.add(TXXX(encoding=3, desc="MusicBrainz Album Artist Id", text=list(album_artist_mbids)))
    if values.get("recording_mbid"):
        tags.add(UFID(owner="http://musicbrainz.org", data=values["recording_mbid"].encode("utf-8")))
    if replace_cover:
        tags.delall("APIC")
        if cover:
            mime = "image/png" if cover.startswith(b"\x89PNG") else "image/jpeg"
            tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))
    tags.save(path, v2_version=4)


def _write_flac(path: Path, values: dict[str, Any], cover: Optional[bytes], replace_cover: bool) -> None:
    from mutagen.flac import FLAC, Picture
    audio = FLAC(path)
    managed = {
        "title", "artist", "artists", "album", "albumartist", "albumartists", "releasedate", "date",
        "tracknumber", "tracktotal", "discnumber", "disctotal", "compilation",
        "musicbrainz_albumid", "musicbrainz_albumartistid", "musicbrainz_trackid",
        "musicbrainz_recordingid",
    }
    for key in list(audio.keys()):
        if key.casefold() in managed:
            del audio[key]
    audio["title"] = [values.get("title") or ""]
    audio["artist"] = [values.get("artist") or ""]
    audio["albumartist"] = [values.get("album_artist") or ""]
    audio["album"] = [values.get("album") or ""]
    audio["tracknumber"] = [str(values.get("track") or 0)]
    audio["tracktotal"] = [str(values.get("track_total") or 0)]
    audio["discnumber"] = [str(values.get("disc") or 1)]
    audio["disctotal"] = [str(values.get("disc_total") or 1)]
    audio["compilation"] = ["1" if values.get("compilation") else "0"]
    if values.get("artists"):
        audio["artists"] = list(values["artists"])
    if values.get("album_artists"):
        audio["albumartists"] = list(values["album_artists"])
    if values.get("date"):
        audio["date"] = [str(values["date"])]
        audio["releasedate"] = [str(values["date"])]
    if values.get("release_mbid"):
        audio["musicbrainz_albumid"] = [values["release_mbid"]]
    album_artist_mbids = values.get("album_artist_mbids") or ([values["album_artist_mbid"]] if values.get("album_artist_mbid") else [])
    if album_artist_mbids:
        audio["musicbrainz_albumartistid"] = list(album_artist_mbids)
    if values.get("recording_mbid"):
        audio["musicbrainz_trackid"] = [values["recording_mbid"]]
    if replace_cover:
        audio.clear_pictures()
        if cover:
            picture = Picture()
            picture.type = 3
            picture.mime = "image/png" if cover.startswith(b"\x89PNG") else "image/jpeg"
            picture.desc = "Cover"
            picture.data = cover
            audio.add_picture(picture)
    audio.save()


def _write_ogg(path: Path, values: dict[str, Any], cover: Optional[bytes], replace_cover: bool) -> None:
    from mutagen.flac import Picture
    from mutagen.oggvorbis import OggVorbis
    audio = OggVorbis(path)
    managed = {
        "title", "artist", "artists", "album", "albumartist", "albumartists", "releasedate", "date",
        "tracknumber", "tracktotal", "discnumber", "disctotal", "compilation",
        "musicbrainz_albumid", "musicbrainz_albumartistid", "musicbrainz_trackid",
        "musicbrainz_recordingid",
    }
    for key in list(audio.keys()):
        if key.casefold() in managed:
            del audio[key]
    audio["title"] = [values.get("title") or ""]
    audio["artist"] = [values.get("artist") or ""]
    audio["albumartist"] = [values.get("album_artist") or ""]
    audio["album"] = [values.get("album") or ""]
    audio["tracknumber"] = [str(values.get("track") or 0)]
    audio["tracktotal"] = [str(values.get("track_total") or 0)]
    audio["discnumber"] = [str(values.get("disc") or 1)]
    audio["disctotal"] = [str(values.get("disc_total") or 1)]
    audio["compilation"] = ["1" if values.get("compilation") else "0"]
    if values.get("artists"):
        audio["artists"] = list(values["artists"])
    if values.get("album_artists"):
        audio["albumartists"] = list(values["album_artists"])
    if values.get("date"):
        audio["date"] = [str(values["date"])]
        audio["releasedate"] = [str(values["date"])]
    if values.get("release_mbid"):
        audio["musicbrainz_albumid"] = [values["release_mbid"]]
    album_artist_mbids = values.get("album_artist_mbids") or ([values["album_artist_mbid"]] if values.get("album_artist_mbid") else [])
    if album_artist_mbids:
        audio["musicbrainz_albumartistid"] = list(album_artist_mbids)
    if values.get("recording_mbid"):
        audio["musicbrainz_trackid"] = [values["recording_mbid"]]
    if replace_cover:
        audio.pop("metadata_block_picture", None)
        if cover:
            picture = Picture()
            picture.type = 3
            picture.mime = "image/png" if cover.startswith(b"\x89PNG") else "image/jpeg"
            picture.desc = "Cover"
            picture.data = cover
            audio["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]
    audio.save()


def _write_mp4(path: Path, values: dict[str, Any], cover: Optional[bytes], replace_cover: bool) -> None:
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
    audio = MP4(path)
    managed = {
        "\xa9nam", "\xa9art", "aart", "\xa9alb", "\xa9day", "trkn", "disk", "cpil",
        "----:com.apple.itunes:artists", "----:com.apple.itunes:musicbrainz album id",
        "----:com.apple.itunes:albumartists",
        "----:com.apple.itunes:musicbrainz album artist id", "----:com.apple.itunes:musicbrainz track id",
    }
    for key in list(audio.keys()):
        if key.casefold() in managed:
            del audio[key]
    audio["\xa9nam"] = [values.get("title") or ""]
    audio["\xa9ART"] = [values.get("artist") or ""]
    audio["aART"] = [values.get("album_artist") or ""]
    audio["\xa9alb"] = [values.get("album") or ""]
    audio["trkn"] = [(int(values.get("track") or 0), int(values.get("track_total") or 0))]
    audio["disk"] = [(int(values.get("disc") or 1), int(values.get("disc_total") or 1))]
    audio["cpil"] = bool(values.get("compilation"))
    if values.get("date"):
        audio["\xa9day"] = [str(values["date"])]
    if values.get("artists"):
        audio["----:com.apple.iTunes:artists"] = [MP4FreeForm(v.encode("utf-8")) for v in values["artists"]]
    if values.get("album_artists"):
        audio["----:com.apple.iTunes:albumartists"] = [MP4FreeForm(v.encode("utf-8")) for v in values["album_artists"]]
    mapping = {
        "release_mbid": "----:com.apple.iTunes:MusicBrainz Album Id",
        "album_artist_mbid": "----:com.apple.iTunes:MusicBrainz Album Artist Id",
        "recording_mbid": "----:com.apple.iTunes:MusicBrainz Track Id",
    }
    for field, key in mapping.items():
        if field == "album_artist_mbid":
            identifiers = values.get("album_artist_mbids") or ([values[field]] if values.get(field) else [])
            if identifiers:
                audio[key] = [MP4FreeForm(value.encode("utf-8")) for value in identifiers]
        elif values.get(field):
            audio[key] = [MP4FreeForm(values[field].encode("utf-8"))]
    if replace_cover:
        audio.pop("covr", None)
        if cover:
            fmt = MP4Cover.FORMAT_PNG if cover.startswith(b"\x89PNG") else MP4Cover.FORMAT_JPEG
            audio["covr"] = [MP4Cover(cover, imageformat=fmt)]
    audio.save()


def _format_pair(number: Any, total: Any) -> str:
    number = int(number or 0)
    total = int(total or 0)
    return f"{number}/{total}" if total else str(number)
