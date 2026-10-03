from __future__ import annotations

import json
from pathlib import Path
from typing import Any


AUDIO_SUFFIXES = {".mp3", ".flac", ".m4a", ".mp4", ".ogg"}


def _first(value: Any, default: str = "") -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default
    return str(value) if value not in (None, "") else default


def _number(value: Any, default: int = 0) -> int:
    try:
        return int(str(value or "").split("/", 1)[0])
    except (TypeError, ValueError):
        return default


def _pair(value: Any, default: int = 0) -> tuple[int, int]:
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, tuple):
        return int(value[0] or default), int(value[1] or 0)
    parts = str(value or "").split("/", 1)
    return _number(parts[0], default), _number(parts[1]) if len(parts) > 1 else 0


def _freeform(values: Any) -> list[str]:
    return [bytes(value).decode("utf-8", errors="ignore") for value in (values or [])]


def read_library_file(path: Path) -> dict[str, Any]:
    """Read every catalogued field while opening a media container only once."""
    path = Path(path)
    stat = path.stat()
    suffix = path.suffix.lower()
    result: dict[str, Any] = {
        "filepath": str(path), "size": stat.st_size, "mtime": stat.st_mtime,
        "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns,
        "device": stat.st_dev, "inode": stat.st_ino, "artist": "", "artists": [],
        "album": "", "title": "", "album_artist": "", "album_artists": [], "year": "0000",
        "date": "", "track_num": 0, "total_tracks": 0, "disc_num": 1,
        "disc_total": 1, "quality_desc": suffix.lstrip(".").upper(),
        "bitrate": 0, "bit_depth": 0, "sample_rate": 0, "duration": 0,
        "track_mbid": "", "album_mbid": "", "album_artist_mbid": "", "album_artist_mbids": [],
        "embedded_cover": False, "has_comment": False, "compilation": False,
        "cache_version": 2,
    }
    if suffix == ".mp3":
        from mutagen.mp3 import MP3
        audio = MP3(path)
        tags = audio.tags
        result.update({
            "duration": int(audio.info.length),
            "bitrate": int((audio.info.bitrate or 0) / 1000),
            "sample_rate": int(audio.info.sample_rate or 0),
            "title": _first(tags.get("TIT2").text if tags and tags.get("TIT2") else ""),
            "artist": _first(tags.get("TPE1").text if tags and tags.get("TPE1") else ""),
            "album": _first(tags.get("TALB").text if tags and tags.get("TALB") else ""),
            "album_artist": _first(tags.get("TPE2").text if tags and tags.get("TPE2") else ""),
            "date": _first(tags.get("TDRL").text if tags and tags.get("TDRL") else (tags.get("TDRC").text if tags and tags.get("TDRC") else "")),
            "embedded_cover": bool(tags and tags.getall("APIC")),
            "has_comment": bool(tags and tags.getall("COMM")),
            "compilation": _first(tags.get("TCMP").text if tags and tags.get("TCMP") else "0") == "1",
        })
        result["track_num"], result["total_tracks"] = _pair(_first(tags.get("TRCK").text if tags and tags.get("TRCK") else ""))
        result["disc_num"], result["disc_total"] = _pair(_first(tags.get("TPOS").text if tags and tags.get("TPOS") else "1/1"), 1)
        if tags:
            for frame in tags.getall("TXXX"):
                desc = frame.desc.casefold().replace("_", " ")
                if desc == "artists": result["artists"] = [str(v) for v in frame.text]
                elif desc == "albumartists": result["album_artists"] = [str(v) for v in frame.text]
                elif desc == "musicbrainz album id": result["album_mbid"] = _first(frame.text)
                elif desc == "musicbrainz album artist id":
                    result["album_artist_mbid"] = _first(frame.text)
                    result["album_artist_mbids"] = [str(v) for v in frame.text]
                elif desc == "musicbrainz recording id": result["track_mbid"] = _first(frame.text)
            ufid = tags.get("UFID:http://musicbrainz.org")
            if ufid: result["track_mbid"] = ufid.data.decode("utf-8", errors="ignore")
    elif suffix in {".flac", ".ogg"}:
        if suffix == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(path)
            embedded = bool(audio.pictures)
            bit_depth = int(audio.info.bits_per_sample or 0)
        else:
            from mutagen.oggvorbis import OggVorbis
            audio = OggVorbis(path)
            embedded = bool(audio.get("metadata_block_picture"))
            bit_depth = 0
        result.update({
            "duration": int(audio.info.length), "bitrate": int((audio.info.bitrate or 0) / 1000),
            "bit_depth": bit_depth, "sample_rate": int(audio.info.sample_rate or 0),
            "title": _first(audio.get("title")), "artist": _first(audio.get("artist")),
            "artists": list(audio.get("artists", [])), "album": _first(audio.get("album")),
            "album_artist": _first(audio.get("albumartist") or audio.get("album artist")),
            "album_artists": list(audio.get("albumartists", [])),
            "date": _first(audio.get("releasedate") or audio.get("date")),
            "track_mbid": _first(audio.get("musicbrainz_trackid") or audio.get("musicbrainz_recordingid")),
            "album_mbid": _first(audio.get("musicbrainz_albumid")),
            "album_artist_mbid": _first(audio.get("musicbrainz_albumartistid")),
            "album_artist_mbids": list(audio.get("musicbrainz_albumartistid", [])),
            "embedded_cover": embedded, "has_comment": bool(audio.get("comment")),
            "compilation": _first(audio.get("compilation"), "0") == "1",
        })
        result["track_num"] = _number(_first(audio.get("tracknumber")))
        result["total_tracks"] = _number(_first(audio.get("tracktotal") or audio.get("totaltracks")))
        result["disc_num"] = _number(_first(audio.get("discnumber")), 1)
        result["disc_total"] = _number(_first(audio.get("disctotal") or audio.get("totaldiscs")), 1)
    elif suffix in {".m4a", ".mp4"}:
        from mutagen.mp4 import MP4
        audio = MP4(path)
        result.update({
            "duration": int(audio.info.length), "bitrate": int((audio.info.bitrate or 0) / 1000),
            "sample_rate": int(audio.info.sample_rate or 0), "title": _first(audio.get("\xa9nam")),
            "artist": _first(audio.get("\xa9ART")), "album": _first(audio.get("\xa9alb")),
            "album_artist": _first(audio.get("aART")), "date": _first(audio.get("\xa9day")),
            "artists": _freeform(audio.get("----:com.apple.iTunes:artists")),
            "album_artists": _freeform(audio.get("----:com.apple.iTunes:albumartists")),
            "track_mbid": _first(_freeform(audio.get("----:com.apple.iTunes:MusicBrainz Track Id"))),
            "album_mbid": _first(_freeform(audio.get("----:com.apple.iTunes:MusicBrainz Album Id"))),
            "album_artist_mbid": _first(_freeform(audio.get("----:com.apple.iTunes:MusicBrainz Album Artist Id"))),
            "album_artist_mbids": _freeform(audio.get("----:com.apple.iTunes:MusicBrainz Album Artist Id")),
            "embedded_cover": bool(audio.get("covr")), "has_comment": bool(audio.get("\xa9cmt")),
            "compilation": _first(audio.get("cpil"), "0").casefold() in {"1", "true"},
        })
        result["track_num"], result["total_tracks"] = _pair(audio.get("trkn"))
        result["disc_num"], result["disc_total"] = _pair(audio.get("disk"), 1)
    else:
        raise ValueError(f"Unsupported audio format: {suffix}")
    result["year"] = result["date"][:4] if result["date"] else "0000"
    result["title"] = result["title"] or path.stem
    result["artist"] = result["artist"] or "Unknown Artist"
    result["album"] = result["album"] or "Unknown Album"
    if not result["artists"] and result["artist"]:
        result["artists"] = [result["artist"]]
    if suffix == ".flac":
        result["quality_desc"] += f" ({result['bit_depth']}bit/{result['sample_rate']/1000:.1f}kHz)"
    elif result["bitrate"]:
        result["quality_desc"] += f" ({result['bitrate']}kbps)"
    result["artists_json"] = json.dumps(result["artists"], ensure_ascii=False)
    result["album_artists_json"] = json.dumps(result["album_artists"], ensure_ascii=False)
    return result
