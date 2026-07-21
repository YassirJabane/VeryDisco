"""
fix_multidisc_albums.py

Scans the music library for multi-disc albums and reorganizes them into
Disc 01 / Disc 02 subdirectories using MusicBrainz as the authoritative source.

Strategy:
  1. Walk /music, collect album root directories (Artist/Album).
     - Skip directories named "Disc XX" — they are handled via their parent.
     - An album root is a directory that either:
       (a) contains audio files directly, or
       (b) contains "Disc XX" subdirectories with audio files.
  2. For each album root, fetch the full tracklist from MusicBrainz
     (artist + album → release with media+recordings).
  3. Match each local file to the correct disc/track by title fuzzy match.
  4. Move each file into Disc 01 / Disc 02 / ... subfolder and re-tag.
  5. Trigger Navidrome full rescan.
"""

import os
import re
import sys
import asyncio
from pathlib import Path
from typing import Optional, Dict, List, Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from backend.app.config import ConfigManager
from backend.app.logger import setup_logging
from backend.app.clients.deezer import DeezerClient
from backend.app.album_sync import fetch_track_metadata_with_fallback
from backend.app.sync import resolve_album_dir, get_library_filename, embed_metadata
from backend.app.clients.navidrome import NavidromeClient
from backend.app.clients.musicbrainz import _mb_get

logger = setup_logging("INFO")
deezer_client = DeezerClient()

_DISC_DIR_RE = re.compile(r"^disc\s*\d+$", re.IGNORECASE)
AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg"}


# ---------------------------------------------------------------------------
# MusicBrainz album tracklist lookup
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    return re.sub(r"[^\w]", "", s).lower()


def _fuzzy_title_match(a: str, b: str) -> bool:
    na, nb = _normalize(a), _normalize(b)
    return na == nb or na in nb or nb in na


def _score_release(r: dict, album: str) -> int:
    """
    Score a MusicBrainz release to prefer:
    - Official status (+20)
    - CD format (+15)
    - Country XW (worldwide) or US (+10)
    - Title exact match (+10)
    - Explicit or standard (not clean, not karaoke) (+5)
    Penalise:
    - Secondary types: Live, Compilation, Remix, DJ-mix, Spokenword (-20 each)
    - Disambiguation contains 'clean' or 'instrumental' (-15)
    - Format is 'Digital Media' only (no CD) (-5)
    """
    score = 0
    title = r.get("title", "")
    status = (r.get("status") or "").lower()
    country = r.get("country") or r.get("release-event-count", "")
    disambiguation = (r.get("disambiguation") or "").lower()
    media = r.get("media") or []
    formats = {(m.get("format") or "").lower() for m in media}
    rg = r.get("release-group") or {}
    secondary_types = [t.lower() for t in (rg.get("secondary-types") or [])]

    # Status
    if status == "official":
        score += 20

    # Format
    if "cd" in formats:
        score += 15
    elif "digital media" in formats and "cd" not in formats:
        score -= 5

    # Country
    if isinstance(country, str):
        if country.upper() == "XW":
            score += 10
        elif country.upper() in ("US", "GB"):
            score += 5

    # Title match
    if _normalize(title) == _normalize(album):
        score += 10
    elif _fuzzy_title_match(title, album):
        score += 5

    # Explicit or standard (not clean/karaoke)
    if "explicit" in disambiguation:
        score += 5
    if "clean" in disambiguation or "instrumental" in disambiguation or "karaoke" in disambiguation:
        score -= 15

    # Secondary types penalties
    for bad_type in ("live", "compilation", "remix", "dj-mix", "spokenword", "mixtape"):
        if bad_type in secondary_types:
            score -= 20

    return score


async def get_mb_album_tracklist(artist: str, album: str) -> Optional[List[Dict]]:
    """
    Returns the full MusicBrainz tracklist for artist+album.
    Selects the best release (prefers CD, XW/international, official, explicit).
    Each entry: {disc_num, disc_total, track_num, track_total, title, recording_id, release_mbid}
    Returns None if not found.
    """
    # Search for releases with media info included so we can score formats
    data = await _mb_get("/release", params={
        "query": f'release:"{album}" AND artist:"{artist}"',
        "limit": 20,
        "inc": "artist-credits+media+release-groups",
        "fmt": "json",
    })
    releases = data.get("releases", []) if data else []

    # Filter to only releases that fuzzy-match the album title
    candidates = [r for r in releases if _fuzzy_title_match(r.get("title", ""), album)]
    if not candidates:
        candidates = releases  # fall back to all if nothing fuzzy-matches

    if not candidates:
        return None

    # Score and pick best release
    best_release = max(candidates, key=lambda r: _score_release(r, album))
    release_mbid = best_release.get("id")
    if not release_mbid:
        return None

    logger.info(
        f"[MB] Best release for '{artist} - {album}': "
        f"'{best_release.get('title')}' "
        f"[{best_release.get('status')} | {best_release.get('country','?')} | "
        f"formats: {[m.get('format') for m in (best_release.get('media') or [])]}] "
        f"(score={_score_release(best_release, album)})"
    )

    # Fetch full release with media+recordings to get disc/track positions
    full = await _mb_get(f"/release/{release_mbid}", params={
        "inc": "media+recordings",
        "fmt": "json",
    })
    if not full:
        return None

    media_list = full.get("media", [])
    if not media_list:
        return None

    disc_total = len(media_list)
    tracklist = []
    for m in media_list:
        disc_num = m.get("position", 1)
        tracks = m.get("tracks", [])
        track_total = len(tracks)
        for t in tracks:
            try:
                track_num = int(t.get("position") or t.get("number") or 0)
            except (ValueError, TypeError):
                track_num = 0
            rec = t.get("recording") or {}
            tracklist.append({
                "disc_num": disc_num,
                "disc_total": disc_total,
                "track_num": track_num,
                "track_total": track_total,
                "title": rec.get("title") or t.get("title", ""),
                "recording_id": rec.get("id"),
                "release_mbid": release_mbid,
            })

    return tracklist if tracklist else None



def match_file_to_tracklist(
    file_title: str, tracklist: List[Dict]
) -> Optional[Dict]:
    """Find the best matching tracklist entry for a given file title."""
    # Exact match first
    for entry in tracklist:
        if _normalize(entry["title"]) == _normalize(file_title):
            return entry
    # Fuzzy match
    for entry in tracklist:
        if _fuzzy_title_match(entry["title"], file_title):
            return entry
    return None


# ---------------------------------------------------------------------------
# Tag reading from audio files
# ---------------------------------------------------------------------------

def read_file_tags(file_path: Path) -> dict:
    """Read artist, title, album, album_artist, track_num, disc_num from file."""
    ext = file_path.suffix.lower()
    res = {"artist": None, "title": None, "album": None, "album_artist": None,
           "track_num": None, "disc_num": 1, "disc_total": 1}
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3
            tags = ID3(str(file_path))
            if "TPE1" in tags: res["artist"] = str(tags["TPE1"].text[0])
            if "TIT2" in tags: res["title"] = str(tags["TIT2"].text[0])
            if "TALB" in tags: res["album"] = str(tags["TALB"].text[0])
            if "TPE2" in tags: res["album_artist"] = str(tags["TPE2"].text[0])
            if "TRCK" in tags:
                try: res["track_num"] = int(str(tags["TRCK"].text[0]).split("/")[0])
                except: pass
            if "TPOS" in tags:
                try:
                    parts = str(tags["TPOS"].text[0]).split("/")
                    res["disc_num"] = int(parts[0])
                    if len(parts) > 1: res["disc_total"] = int(parts[1])
                except: pass
        elif ext in [".flac", ".ogg"]:
            from mutagen.flac import FLAC
            audio = FLAC(str(file_path))
            res["artist"] = (audio.get("artist") or [None])[0]
            res["title"] = (audio.get("title") or [None])[0]
            res["album"] = (audio.get("album") or [None])[0]
            res["album_artist"] = (audio.get("albumartist") or audio.get("album artist") or [None])[0]
            try: res["track_num"] = int(audio["tracknumber"][0].split("/")[0])
            except: pass
            try: res["disc_num"] = int(audio["discnumber"][0].split("/")[0])
            except: pass
            try: res["disc_total"] = int(audio["disctotal"][0])
            except: pass
        elif ext in [".m4a", ".mp4"]:
            from mutagen.mp4 import MP4
            audio = MP4(str(file_path))
            res["artist"] = (audio.get("\xa9ART") or [None])[0]
            res["title"] = (audio.get("\xa9nam") or [None])[0]
            res["album"] = (audio.get("\xa9alb") or [None])[0]
            res["album_artist"] = (audio.get("aART") or [None])[0]
            try: res["track_num"] = audio["trkn"][0][0]
            except: pass
            try:
                res["disc_num"] = audio["disk"][0][0]
                res["disc_total"] = audio["disk"][0][1] or 1
            except: pass
    except Exception as e:
        logger.warning(f"Failed to read tags from {file_path}: {e}")
    return res


# ---------------------------------------------------------------------------
# Album directory collection
# ---------------------------------------------------------------------------

def collect_album_roots(music_dir: Path) -> List[Path]:
    """
    Walk music_dir and return a deduplicated list of album root directories.
    - Skips explore/playlists paths.
    - If a directory is named "Disc XX", adds its PARENT instead.
    - Deduplicates so each album root appears only once.
    """
    skip_parts = {"explore", "playlists", "navidrome_playlists", "staging"}
    roots_seen = set()
    album_roots = []

    for dirpath, dirnames, filenames in os.walk(music_dir):
        current = Path(dirpath)

        # Skip blacklisted dirs
        if any(p.lower() in skip_parts for p in current.parts):
            continue

        audio_files = [f for f in filenames if Path(f).suffix.lower() in AUDIO_EXTS]
        if not audio_files:
            continue

        # If this directory is named "Disc XX", add parent as the album root
        if _DISC_DIR_RE.match(current.name):
            album_root = current.parent
        else:
            album_root = current

        if album_root not in roots_seen:
            roots_seen.add(album_root)
            album_roots.append(album_root)

    return album_roots


def collect_all_audio_in_album_root(album_root: Path) -> List[Path]:
    """Collect all audio files under an album root (including inside Disc XX subdirs)."""
    audio_files = []
    for dirpath, _, filenames in os.walk(album_root):
        for f in filenames:
            p = Path(dirpath) / f
            if p.suffix.lower() in AUDIO_EXTS:
                audio_files.append(p)
    return sorted(audio_files, key=lambda p: p.name.lower())


# ---------------------------------------------------------------------------
# Core migration
# ---------------------------------------------------------------------------

async def fix_multidisc_library(music_dir: Path):
    if not music_dir.exists():
        logger.error(f"Music directory does not exist: {music_dir}")
        return

    logger.info(f"Scanning '{music_dir}' for multi-disc albums...")
    album_roots = collect_album_roots(music_dir)
    logger.info(f"Found {len(album_roots)} album roots to inspect.")

    albums_fixed = 0
    tracks_moved = 0

    for album_root in album_roots:
        audio_files = collect_all_audio_in_album_root(album_root)
        if not audio_files:
            continue

        # Read tags from all files to detect duplicates track numbers → multi-disc
        tag_list = []
        track_nums = []
        for f in audio_files:
            tags = read_file_tags(f)
            tags["file_path"] = f
            tag_list.append(tags)
            if tags["track_num"] is not None:
                track_nums.append(tags["track_num"])

        has_disc_tags = any(t["disc_num"] > 1 or t["disc_total"] > 1 for t in tag_list)
        has_dup_tracks = len(track_nums) > len(set(track_nums))

        if not has_disc_tags and not has_dup_tracks:
            continue

        # Determine artist + album name from tags or directory structure
        artist = next((t["artist"] for t in tag_list if t["artist"]), None) or album_root.parent.name
        album_name = next((t["album"] for t in tag_list if t["album"]), None) or album_root.name
        album_artist = next((t["album_artist"] for t in tag_list if t["album_artist"]), None) or artist

        logger.info(f"[MULTIDISC] '{artist} - {album_name}' ({len(audio_files)} tracks) in '{album_root}'")

        # Fetch the full album tracklist from MusicBrainz (one call per album, not per track)
        tracklist = await get_mb_album_tracklist(artist, album_name)
        if not tracklist:
            # Fallback: try with main artist only (no feat.)
            main_artist = re.split(r'\b(?:feat|ft|featuring|&|and)\b', artist, flags=re.IGNORECASE)[0].strip()
            if main_artist != artist:
                tracklist = await get_mb_album_tracklist(main_artist, album_name)

        if not tracklist:
            logger.warning(f"[SKIP] Could not find MusicBrainz tracklist for '{artist} - {album_name}'. Skipping.")
            continue

        disc_total = tracklist[0]["disc_total"]
        if disc_total < 2:
            logger.info(f"[SKIP] MusicBrainz says '{album_name}' has only {disc_total} disc(s). Skipping.")
            continue

        logger.info(f"[MB] '{album_name}' → {disc_total} discs, {len(tracklist)} tracks total")

        # Match each file to its correct disc/track via MusicBrainz title
        albums_fixed += 1
        for file_tags in tag_list:
            f_path = file_tags["file_path"]
            file_title = file_tags["title"] or f_path.stem

            mb_entry = match_file_to_tracklist(file_title, tracklist)
            if not mb_entry:
                logger.warning(f"[NO MATCH] Could not match '{file_title}' to MusicBrainz tracklist. Keeping in place.")
                continue

            disc_num = mb_entry["disc_num"]
            track_num = mb_entry["track_num"]
            track_total = mb_entry["track_total"]
            recording_id = mb_entry["recording_id"]
            release_mbid = mb_entry["release_mbid"]

            # Fetch full track metadata for cover/date/artist string
            try:
                meta_res = await fetch_track_metadata_with_fallback(
                    deezer_client, file_tags["artist"] or artist, file_title, album_name
                )
                dz_artist = meta_res.get("artist") or file_tags["artist"] or artist
                dz_album_artist = meta_res.get("album_artist") or album_artist
                dz_album = meta_res.get("album") or album_name
                dz_title = meta_res.get("title") or file_title
                cover_bytes = meta_res.get("cover_bytes")
                dz_date = meta_res.get("date")
                mbid_album = meta_res.get("mbid_album") or release_mbid
                mbid_recording = meta_res.get("mbid_recording") or recording_id
            except Exception as e:
                logger.error(f"Metadata fetch failed for '{file_title}': {e}")
                dz_artist = file_tags["artist"] or artist
                dz_album_artist = album_artist
                dz_album = album_name
                dz_title = file_title
                cover_bytes = None
                dz_date = None
                mbid_album = release_mbid
                mbid_recording = recording_id

            # Resolve target folder (creates Disc 01 / Disc 02 structure)
            target_folder, safe_artist, safe_album = resolve_album_dir(
                music_dir, dz_artist, dz_album, dz_album_artist,
                disc_num=disc_num, disc_total=disc_total
            )
            safe_filename = get_library_filename(dz_artist, safe_album, track_num, dz_title, f_path.suffix)
            dest_path = target_folder / safe_filename

            # Read existing lyrics sidecar
            lyrics_text = None
            lrc_path = f_path.with_suffix(".lrc")
            if lrc_path.exists():
                try:
                    lyrics_text = lrc_path.read_text(encoding="utf-8")
                except Exception:
                    pass

            # Move file (skip if already in correct place)
            if f_path.resolve() != dest_path.resolve():
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                f_path.rename(dest_path)
                if lrc_path.exists():
                    lrc_path.rename(dest_path.with_suffix(".lrc"))
                logger.info(f"Moved  → {dest_path.relative_to(music_dir)}")
            else:
                logger.info(f"In place → {dest_path.relative_to(music_dir)}")

            # Embed corrected metadata
            embed_metadata(
                file_path=str(dest_path),
                artist=dz_artist,
                title=dz_title,
                album=dz_album,
                track_num=track_num,
                track_total=track_total,
                cover_bytes=cover_bytes,
                lyrics_text=lyrics_text,
                album_artist=dz_album_artist,
                date=dz_date,
                disc_num=disc_num,
                disc_total=disc_total,
                is_explore=False,
                mbid_album=mbid_album,
                mbid_recording=mbid_recording,
            )
            tracks_moved += 1

        # Clean up empty Disc XX dirs left behind
        for subdir in album_root.iterdir():
            if subdir.is_dir() and _DISC_DIR_RE.match(subdir.name):
                try:
                    subdir.rmdir()  # only removes if empty
                except OSError:
                    pass  # not empty, leave it

    logger.info(f"Done. Fixed {albums_fixed} albums, re-tagged {tracks_moved} tracks.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    config_path = os.getenv(
        "CONFIG_PATH",
        "/data/config.yml" if os.path.exists("/data/config.yml") else "config.yml"
    )
    cfg_mgr = ConfigManager(config_path)
    cfg = cfg_mgr.config
    music_dir = Path(getattr(cfg.paths, "music_dir", "/music"))

    await fix_multidisc_library(music_dir)

    if cfg.navidrome.url and cfg.navidrome.username:
        logger.info("Triggering Navidrome full rescan...")
        nd_client = NavidromeClient(
            url=cfg.navidrome.url,
            username=cfg.navidrome.username,
            password=cfg.navidrome.password,
        )
        await nd_client.trigger_rescan(full_scan=True)


if __name__ == "__main__":
    asyncio.run(main())
