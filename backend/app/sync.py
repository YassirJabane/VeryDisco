import os
import re
import shutil
import tempfile
import errno
import asyncio
import time
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional, Union
from datetime import datetime

import json
import backend.app.logger as app_logger
from backend.app.config import AppConfig
from backend.app.database import Database
from backend.app.clients.listenbrainz import ListenBrainzClient
from backend.app.clients.slskd import SlskdClient
from backend.app.clients.lrclib import LrcLibClient
from backend.app.clients.deezer import DeezerClient

logger = app_logger.get_logger()


def safe_move_file(src: Any, dst: Any):
    """Atomically publish a download, including when Docker mounts differ."""
    src_path = Path(src)
    dst_path = Path(dst)
    if src_path.resolve() == dst_path.resolve():
        return
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src_path, dst_path)
        return
    except OSError as e:
        if e.errno not in (errno.EXDEV, errno.EIO):
            raise

    fd, temporary_name = tempfile.mkstemp(prefix=".verydisco-", suffix=dst_path.suffix, dir=dst_path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as fdst, open(src_path, "rb") as fsrc:
            shutil.copyfileobj(fsrc, fdst)
            fdst.flush()
            os.fsync(fdst.fileno())
        if temporary_path.stat().st_size != src_path.stat().st_size:
            raise IOError(f"Incomplete copy from {src_path} to {temporary_path}")
        os.replace(temporary_path, dst_path)
        src_path.unlink()
    finally:
        temporary_path.unlink(missing_ok=True)

def safe_copy_file(src: Any, dst: Any):
    """Copy to a sibling temporary file before replacing the destination."""
    src_path = Path(src)
    dst_path = Path(dst)
    if src_path.resolve() == dst_path.resolve():
        return
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".verydisco-", suffix=dst_path.suffix, dir=dst_path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as fdst, open(src_path, "rb") as fsrc:
            shutil.copyfileobj(fsrc, fdst)
            fdst.flush()
            os.fsync(fdst.fileno())
        if temporary_path.stat().st_size != src_path.stat().st_size:
            raise IOError(f"Incomplete copy from {src_path} to {temporary_path}")
        shutil.copystat(src_path, temporary_path)
        os.replace(temporary_path, dst_path)
    finally:
        temporary_path.unlink(missing_ok=True)

# Global state tracker for real-time dashboard updates
is_syncing = False
current_run_id: Optional[int] = None
current_sync_task: Optional[asyncio.Task] = None
sync_progress: Dict[str, Any] = {
    "status": "idle",
    "tracks_found": 0,
    "tracks_downloaded": 0,
    "tracks_skipped": 0,
    "tracks_failed": 0,
    "started_at": None
}

# Module-level lock (prevents concurrent sync runs)
_sync_lock = asyncio.Lock()

SEARCHES_FILE = "/data/playlist_searches.json"

def _get_searches_file_path() -> str:
    dir_name = os.path.dirname(SEARCHES_FILE)
    if not os.path.exists(dir_name) and dir_name != "":
        try:
            os.makedirs(dir_name, exist_ok=True)
            return SEARCHES_FILE
        except Exception:
            return "./playlist_searches.json"
    return SEARCHES_FILE

def load_playlist_searches() -> Dict[str, List[str]]:
    path = _get_searches_file_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load playlist searches: {e}")
    return {}

def save_playlist_searches(data: Dict[str, List[str]]):
    path = _get_searches_file_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save playlist searches: {e}")

async def clear_old_playlist_searches(slskd_client: SlskdClient, playlist_source: str):
    data = load_playlist_searches()
    searches = data.get(playlist_source, [])
    if searches:
        logger.info(f"Clearing {len(searches)} old searches from slskd for playlist '{playlist_source}'...")
        for search_id in searches:
            try:
                await slskd_client.delete_search(search_id)
            except Exception as e:
                logger.warning(f"Failed to delete search '{search_id}': {e}")
        data[playlist_source] = []
        save_playlist_searches(data)

def add_playlist_search_ids(playlist_source: str, search_ids: List[str]):
    data = load_playlist_searches()
    if playlist_source not in data:
        data[playlist_source] = []
    data[playlist_source].extend(search_ids)
    save_playlist_searches(data)

# Regex for matching trailing features like (feat. ...), [ft. ...], (con ...), or unbracketed feat. ...
FEAT_TAIL_RE = re.compile(
    r'(?i)\s*(?:[\(\[\{]\s*(?:feat\.?|featuring|ft\.?|with|con|prod\.?)\b[^\)\]\}]*[\)\]\}]|(?:[-–—]\s*)?\b(?:feat\.?|featuring|ft\.?)\s+.+|[-–—]\s*(?:with|con)\s+.+)\s*$'
)
REMASTER_TAIL_RE = re.compile(
    r'(?i)\s*(?:[-–—]\s*(?:.*\b(?:remaster(?:ed)?|live|version|radio\s+edit|edit|mono|stereo)\b.*|\d{4}\s*remaster(?:ed)?)|[\(\[\{][^\)\]\}]*\b(?:remaster(?:ed)?|live|version|radio\s+edit)\b[^\)\]\}]*[\)\]\}])$'
)

def clean_search_title(title: str) -> str:
    if not title:
        return ""
    cleaned = title
    for _ in range(2):
        cleaned = FEAT_TAIL_RE.sub('', cleaned).strip()
        cleaned = REMASTER_TAIL_RE.sub('', cleaned).strip()
    return cleaned or title.strip()


def get_file_audio_info(file_path: Any) -> Tuple[str, int, int, int]:
    """Get file extension, bitrate (kbps), bit_depth, and sample_rate (Hz) using mutagen."""
    file_path = Path(file_path)
    ext = file_path.suffix.lower().strip(".")
    bitrate = 0
    bit_depth = 0
    sample_rate = 0
    
    try:
        if ext == "mp3":
            from mutagen.mp3 import MP3
            audio = MP3(file_path)
            bitrate = int(audio.info.bitrate / 1000) if audio.info.bitrate else 0
            sample_rate = audio.info.sample_rate or 0
        elif ext == "flac":
            from mutagen.flac import FLAC
            audio = FLAC(file_path)
            bitrate = 0 # Lossless
            bit_depth = audio.info.bits_per_sample or 0
            sample_rate = audio.info.sample_rate or 0
        elif ext in ["m4a", "mp4"]:
            from mutagen.mp4 import MP4
            audio = MP4(file_path)
            bitrate = int(audio.info.bitrate / 1000) if audio.info.bitrate else 0
            sample_rate = audio.info.sample_rate or 0
        elif ext == "ogg":
            from mutagen.oggvorbis import OggVorbis
            audio = OggVorbis(file_path)
            bitrate = int(audio.info.bitrate / 1000) if audio.info.bitrate else 0
            sample_rate = audio.info.sample_rate or 0
    except Exception:
        pass
        
    return ext, bitrate, bit_depth, sample_rate

def get_best_target_profile(config: AppConfig) -> Optional[dict]:
    q_cfg = config.slskd.audio_quality
    preset = q_cfg.preset
    if preset == "lossless":
        from backend.app.config import LOSSLESS_PRESETS_DEFAULT
        return LOSSLESS_PRESETS_DEFAULT[0]
    elif preset == "storage_saver":
        from backend.app.config import STORAGE_SAVER_PRESETS_DEFAULT
        return STORAGE_SAVER_PRESETS_DEFAULT[0]
    elif preset == "custom":
        active_profiles = [
            p if isinstance(p, dict) else p.model_dump()
            for p in q_cfg.custom_profiles
        ]
        return active_profiles[0] if active_profiles else None
    return None

def check_quality_status(existing_ext: str, existing_bitrate: int, existing_depth: int, existing_sr: int, config: AppConfig) -> str:
    """
    Compare existing file quality against best target configuration.
    Returns:
      'worse'  - if existing file can be upgraded (e.g. existing is MP3, target is FLAC)
      'same'   - if existing file is of equal quality
      'better' - if existing file is of better quality (e.g. existing is FLAC, target is MP3)
    """
    best_profile = get_best_target_profile(config)
    if not best_profile:
        return 'same' # No profiles configured, assume same
        
    target_ext = best_profile.get("format", "").lower().strip(".")
    target_min_br = best_profile.get("min_bitrate", 0) or 0
    
    # Check lossless status
    existing_is_lossless = existing_ext.lower().strip(".") in ["flac", "wav", "alac", "ape"]
    target_is_lossless = target_ext in ["flac", "wav", "alac", "ape"]
    
    if existing_is_lossless and not target_is_lossless:
        return 'better'
    if not existing_is_lossless and target_is_lossless:
        return 'worse'
        
    if existing_is_lossless and target_is_lossless:
        # Both are lossless. Let's compare bit depth and sample rate if specified
        target_depth = best_profile.get("bit_depth", 0) or 0
        target_sr = best_profile.get("sample_rate", 0) or 0
        
        is_worse = False
        if target_depth > 0 and existing_depth < target_depth:
            is_worse = True
        if target_sr > 0 and existing_sr < target_sr:
            is_worse = True
            
        return 'worse' if is_worse else 'same'
        
    # Both are lossy
    if existing_bitrate > 0 and target_min_br > 0:
        if existing_bitrate < target_min_br:
            return 'worse'
        elif existing_bitrate > target_min_br:
            return 'better'
            
    return 'same'

def sanitize_filename(name: str) -> str:
    """Sanitize strings for safe cross-platform filenames."""
    sanitized = re.sub(r'[\\/*?":<>|]', "_", name)
    return sanitized.strip('. ')

def get_folder_artist_name(artist: str, album_artist: str = "", album: str = "") -> str:
    """
    Returns a clean primary or collaborative artist name for top-level folder structure under /music/{user}/.
    Strips featuring artists (feat./ft.), but preserves joint album artists or group names.
    Handles supergroup / collaboration overrides like Huncho Jack.
    """
    if (album and "huncho jack" in album.lower()) or (artist and "huncho jack" in artist.lower()) or (album_artist and "huncho jack" in album_artist.lower()):
        return "Huncho Jack"

    candidate = album_artist or artist
    if not candidate:
        return "Unknown Artist"

    primary = re.split(r'[\(\[]?\s*(?:\b(?:feat|ft|featuring)\.?\s+)', candidate, flags=re.IGNORECASE)[0].strip()
    primary = re.sub(r'[\(\[\)\]]', '', primary).strip()

    return primary or candidate

async def fix_directory_tags_and_rescan(
    dir_path: Union[str, Path],
    target_artist: Optional[str] = None,
    target_album: Optional[str] = None,
    config: Optional[Any] = None,
    music_dir: Optional[Union[str, Path]] = None,
    return_details: bool = False,
) -> Any:
    """
    Scans all audio files in dir_path, updates their embedded metadata (Artist,
    Album Artist, Album, plus canonical MusicBrainz release/track metadata if available),
    relocates/renames the files to the canonical Artist/Album folder when music_dir
    is provided, cleans up empty source directories, and triggers a Navidrome scan.
    """
    from backend.app.metadata_pipeline.tags import AUDIO_SUFFIXES, read_track, write_track

    path = Path(dir_path)
    logger.info(f"fix_directory_tags_and_rescan starting for path: {path}")

    # Case-insensitive / fuzzy fallback if path doesn't exist directly
    if not path.exists() and path.parent.exists():
        try:
            for child in path.parent.iterdir():
                if child.name.lower() == path.name.lower():
                    path = child
                    break
        except Exception:
            pass

    if not path.exists() or not path.is_dir():
        logger.warning(f"fix_directory_tags_and_rescan: Directory not found: '{dir_path}'")
        if return_details:
            return {
                "updated_count": 0,
                "old_paths": [],
                "new_paths": [],
                "new_folder_path": str(path),
                "release_mbid": None,
                "official_tracks": [],
                "artist": target_artist or "",
                "album": target_album or "",
            }
        return 0

    folder_album = (target_album or path.name).strip()
    folder_artist = (target_artist or path.parent.name).strip()

    release_mbid = ""
    release_date = ""
    official_tracks: List[Dict[str, Any]] = []
    by_disc_track: Dict[Tuple[int, int], Dict[str, Any]] = {}
    by_norm_title: Dict[str, Dict[str, Any]] = {}
    disc_totals: Dict[int, int] = {}
    max_disc = 1

    try:
        from backend.app.clients.musicbrainz import musicbrainz_client, _is_matching_release_artist
        mb_album = await musicbrainz_client.get_album_tracklist(folder_artist, folder_album)
        if mb_album and _is_matching_release_artist(
            {"artist-credit": [{"name": mb_album.get("album_artist", "")}]}, folder_artist
        ):
            release_mbid = mb_album.get("release_mbid") or ""
            release_date = mb_album.get("release_date") or ""
            official_tracks = mb_album.get("tracks") or []
            max_disc = int(mb_album.get("nb_discs") or 1)
            for tr in official_tracks:
                d_num = int(tr.get("disc_num") or tr.get("disk_number") or tr.get("disc_number") or 1)
                t_num = int(tr.get("track_position") or tr.get("track_number") or 0)
                max_disc = max(max_disc, d_num)
                disc_totals[d_num] = max(disc_totals.get(d_num, 0), t_num)
                if t_num > 0:
                    by_disc_track[(d_num, t_num)] = tr
                t_norm = re.sub(r"[^\w]", "", (tr.get("title") or "").casefold())
                if t_norm and t_norm not in by_norm_title:
                    by_norm_title[t_norm] = tr
    except Exception as mb_err:
        logger.debug(f"MusicBrainz lookup skipped/failed during folder fix for '{folder_artist} - {folder_album}': {mb_err}")

    updated_count = 0
    old_paths: List[str] = []
    new_paths: List[str] = []
    new_album_root: Optional[Path] = None

    audio_files = sorted(
        f for f in path.rglob("*")
        if f.is_file() and f.suffix.lower() in AUDIO_SUFFIXES
    )

    for f_path in audio_files:
        try:
            old_paths.append(str(f_path))
            current = await asyncio.to_thread(read_track, f_path)
            curr_title = current.get("title") or f_path.stem
            curr_track = int(current.get("track") or 0)
            curr_disc = int(current.get("disc") or 1)

            norm_title = re.sub(r"[^\w]", "", curr_title.casefold())
            mb_track = by_norm_title.get(norm_title)
            if not mb_track and curr_track > 0:
                mb_track = by_disc_track.get((curr_disc, curr_track)) or by_disc_track.get((1, curr_track))

            new_title = (mb_track.get("title") if mb_track and mb_track.get("title") else curr_title)
            track_num = int(
                (mb_track.get("track_position") or mb_track.get("track_number") if mb_track else None)
                or curr_track
                or 0
            )
            disc_num = int(
                (mb_track.get("disc_num") or mb_track.get("disk_number") or mb_track.get("disc_number") if mb_track else None)
                or curr_disc
                or 1
            )
            disc_total = int(max_disc if official_tracks else (current.get("disc_total") or 1))
            track_total = int(
                disc_totals.get(disc_num, len(official_tracks))
                if official_tracks
                else (current.get("track_total") or 0)
            )
            recording_mbid = (
                (mb_track.get("id") or mb_track.get("recording_mbid") if mb_track else "")
                or current.get("recording_mbid")
                or ""
            )

            values = {
                "title": new_title,
                "artist": folder_artist,
                "artists": [folder_artist],
                "album": folder_album,
                "album_artist": folder_artist,
                "album_artists": [folder_artist],
                "album_artist_mbid": "",
                "album_artist_mbids": [],
                "date": release_date or current.get("date") or "",
                "track": track_num,
                "track_total": track_total,
                "disc": disc_num,
                "disc_total": disc_total,
                "release_mbid": release_mbid or current.get("release_mbid") or "",
                "recording_mbid": recording_mbid,
                "compilation": bool(current.get("compilation", False)),
            }

            await asyncio.to_thread(write_track, f_path, values, None, False)

            final_path = f_path
            if music_dir is not None:
                dest_dir, _, _ = resolve_album_dir(
                    music_dir,
                    folder_artist,
                    folder_album,
                    album_artist=folder_artist,
                    disc_num=disc_num,
                    disc_total=disc_total,
                )
                album_root_candidate = dest_dir.parent if (disc_total > 1 and disc_num > 0) else dest_dir
                new_album_root = album_root_candidate

                new_filename = get_library_filename(
                    folder_artist, folder_album, track_num if track_num > 0 else None, new_title, f_path.suffix
                )
                dest_path = dest_dir / new_filename
                if dest_path.resolve() != f_path.resolve():
                    old_lrc = f_path.with_suffix(".lrc")
                    safe_move_file(f_path, dest_path)
                    if old_lrc.is_file():
                        safe_move_file(old_lrc, dest_path.with_suffix(".lrc"))
                    final_path = dest_path

            new_paths.append(str(final_path))
            updated_count += 1
            logger.info(
                f"Re-tagged embedded metadata for '{final_path.name}' -> Artist: '{folder_artist}', Album: '{folder_album}'"
            )
        except Exception as e:
            logger.warning(f"Failed to update tags for '{f_path}': {e}")

    if music_dir is not None and new_album_root is not None and new_album_root.resolve() != path.resolve():
        try:
            for img in list(path.glob("*")):
                if img.is_file() and img.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                    dst_img = new_album_root / img.name
                    if not dst_img.exists():
                        safe_move_file(img, dst_img)
                    else:
                        img.unlink(missing_ok=True)
            for sub in sorted(path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if sub.is_dir() and not any(sub.iterdir()):
                    sub.rmdir()
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
            music_root = Path(music_dir).resolve()
            parent = path.parent
            if parent.resolve() != music_root and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except Exception as cleanup_err:
            logger.debug(f"Could not clean up old album directory '{path}': {cleanup_err}")

    logger.info(f"fix_directory_tags_and_rescan completed. Updated {updated_count} files.")

    # Trigger Navidrome scan
    if config and config.navidrome.url:
        try:
            from backend.app.clients.navidrome import NavidromeClient
            nd_client = NavidromeClient(
                url=config.navidrome.url,
                username=config.navidrome.username,
                password=config.navidrome.password
            )
            await nd_client.trigger_scan()
            logger.info(f"Triggered Navidrome scan for url '{config.navidrome.url}'.")
        except Exception as nd_err:
            logger.warning(f"Triggering Navidrome scan failed: {nd_err}")

    if return_details:
        return {
            "updated_count": updated_count,
            "old_paths": old_paths,
            "new_paths": new_paths,
            "new_folder_path": str(new_album_root or path),
            "release_mbid": release_mbid or None,
            "official_tracks": official_tracks,
            "artist": folder_artist,
            "album": folder_album,
        }
    return updated_count

def get_clean_album_folder(album: str, artist: str = "") -> str:
    """
    Returns a clean album folder name.
    Strips featuring artist info from album titles (e.g. '3500 (feat. Future & 2 Chainz)' -> '3500').
    If the album title equals or contains the artist name with feat. (e.g. 'Travis Scott feat. Quavo'),
    falls back to 'Singles'.
    """
    if not album:
        return "Singles"
    clean = re.sub(r'[\(\[]?\s*(?:\b(?:feat|ft|featuring)\.?\s+.*[\)\]]?)', '', album, flags=re.IGNORECASE).strip()
    if re.search(r'\b(?:feat|ft|featuring)\b', album, flags=re.IGNORECASE) and (not clean or clean.lower() == artist.lower()):
        return "Singles"
    if clean.lower() in ["unknown", "unknown album", ""]:
        return "Singles"
    return clean or album

EDITION_SUFFIX_RE = re.compile(
    r'(?i)\s*(?:[\(\[\{]\s*(?:(?:\d+(?:th|st|nd|rd)?\s+)?(?:anniversary|deluxe|edition|version|remaster(?:ed)?)|deluxe|fomo|bonus(?:\s+track)?|expanded|extended|special|collector\'?s?|complete|super\s+deluxe|international|repack|digital|remaster(?:ed)?|tour|reissue|platinum|diamond|explicit).*?[\)\]\}]|\s*[-–—]\s*(?:(?:\d+(?:th|st|nd|rd)?\s+)?(?:anniversary|deluxe|edition|version)|deluxe|fomo|bonus(?:\s+track)?|expanded|extended|special|edition|version|digital|remaster(?:ed)?|reissue|platinum).*$)'
)
TRAILING_YEAR_RE = re.compile(r'\s*[\(\[]\s*\d{4}\s*[\)\]]$')

def extract_base_album_name(album_name: str) -> str:
    """
    Strips edition tags, deluxe markers, year tags, etc. to get the root/base album name.
    E.g. 'Habibti (FOMO)' -> 'Habibti'
         'ABBA Gold (Anniversary Edition) (1992)' -> 'ABBA Gold'
    """
    if not album_name:
        return ""
    clean = album_name
    for _ in range(2):
        clean = EDITION_SUFFIX_RE.sub('', clean).strip()
        clean = TRAILING_YEAR_RE.sub('', clean).strip()
    return clean or album_name.strip()

def normalize_album_name(name: str) -> str:
    cleaned = extract_base_album_name(name)
    cleaned = re.sub(r'[\(\[]?\s*(?:\b(?:feat|ft|featuring)\.?\s+.*[\)\]]?)', '', cleaned, flags=re.IGNORECASE)
    return re.sub(r'[^\w]', '', cleaned).lower()

# Alias for backwards compatibility
normalize_album = normalize_album_name

def find_existing_album_folder(artist_dir: Path, target_album: str) -> Optional[Path]:
    """
    Looks under artist_dir for an existing album directory that matches target_album.
    Matches case-insensitively, and handles variations like '[Explicit]', etc.
    Avoids collapsing an upgraded edition (e.g. 'Habibti (FOMO)') into a base album ('Habibti').
    """
    if not artist_dir.exists() or not artist_dir.is_dir():
        return None

    target_clean = get_clean_album_folder(target_album).lower()
    target_alphanumeric = re.sub(r'[^\w]', '', target_clean)
    target_norm = normalize_album_name(target_album)

    try:
        children = [c for c in artist_dir.iterdir() if c.is_dir()]
    except Exception:
        return None

    # 1. Exact case-insensitive match
    for child in children:
        if child.name.lower() == target_clean:
            return child

    # 2. Match ignoring punctuation / whitespace
    if target_alphanumeric:
        for child in children:
            if re.sub(r'[^\w]', '', child.name.lower()) == target_alphanumeric:
                return child

    # 3. Normalized base match ONLY IF target is NOT an upgraded edition compared to child
    is_target_edition = bool(EDITION_SUFFIX_RE.search(target_album))
    if target_norm:
        for child in children:
            is_child_edition = bool(EDITION_SUFFIX_RE.search(child.name))
            # If target has an edition tag (e.g. FOMO) but child does not, do not reuse child
            if is_target_edition and not is_child_edition:
                continue
            child_norm = normalize_album_name(child.name)
            if child_norm and child_norm == target_norm:
                return child

    return None

def find_superseded_album_folders(artist_dir: Path, target_album: str) -> List[Path]:
    """
    Finds sibling folders in artist_dir that represent a base or older edition of target_album.
    E.g. If target is 'Habibti (fomo)', finds 'Habibti'.
    """
    if not artist_dir.exists() or not artist_dir.is_dir():
        return []

    target_clean = get_clean_album_folder(target_album).lower()
    target_norm_base = normalize_album_name(target_album)
    if not target_norm_base:
        return []

    superseded = []
    try:
        for child in artist_dir.iterdir():
            if not child.is_dir():
                continue
            if child.name.lower() == target_clean:
                continue
            
            child_norm_base = normalize_album_name(child.name)
            if child_norm_base and child_norm_base == target_norm_base:
                superseded.append(child)
    except Exception:
        pass

    return superseded

def harmonize_album_directory(
    album_dir: Union[str, Path],
    target_album: Optional[str] = None,
    target_album_artist: Optional[str] = None,
    target_mbid: Optional[str] = None,
    target_cover_bytes: Optional[bytes] = None,
    target_date: Optional[str] = None,
) -> int:
    """
    Ensures all audio tracks in an album directory have unified, consistent tags:
    - Same album name
    - Same album artist
    - Same MusicBrainz Release ID (mbid_album)
    - Embedded cover art (and saves cover.jpg / folder.jpg)
    - Same release date / year
    Returns the number of files updated.
    """
    from backend.app.metadata_pipeline.tags import read_track
    dir_path = Path(album_dir)
    if not dir_path.exists() or not dir_path.is_dir():
        return 0

    audio_files = [
        p for p in dir_path.rglob("*")
        if p.is_file() and p.suffix.lower() in [".mp3", ".flac", ".m4a", ".ogg"]
    ]
    if not audio_files:
        return 0

    # 1. Discover metadata from cover files or existing tracks
    if not target_cover_bytes:
        for img_name in ("cover.jpg", "cover.png", "folder.jpg", "folder.png"):
            img_path = dir_path / img_name
            if img_path.exists():
                try:
                    target_cover_bytes = img_path.read_bytes()
                    break
                except Exception:
                    pass

    files_info = []
    for f in audio_files:
        try:
            info = read_track(f, include_cover=True)
            files_info.append((f, info))
            if not target_cover_bytes and info.get("cover"):
                target_cover_bytes = info["cover"]
            if not target_mbid and info.get("release_mbid"):
                target_mbid = info["release_mbid"]
            if not target_date and info.get("date"):
                target_date = info["date"]
            if not target_album_artist and info.get("album_artist"):
                target_album_artist = info["album_artist"]
            if not target_album and info.get("album"):
                target_album = info["album"]
        except Exception:
            files_info.append((f, {}))

    if not target_album:
        target_album = dir_path.name
    if not target_album_artist:
        target_album_artist = dir_path.parent.name

    if target_cover_bytes and not (dir_path / "cover.jpg").exists():
        try:
            (dir_path / "cover.jpg").write_bytes(target_cover_bytes)
        except Exception:
            pass

    updated_count = 0
    for f, info in files_info:
        needs_update = False
        if info.get("album") and info.get("album") != target_album:
            needs_update = True
        elif not info.get("album"):
            needs_update = True

        if target_album_artist and info.get("album_artist") != target_album_artist:
            needs_update = True
        if target_mbid and info.get("release_mbid") != target_mbid:
            needs_update = True
        if target_date and not info.get("date"):
            needs_update = True
        if target_cover_bytes and not info.get("cover"):
            needs_update = True

        if needs_update:
            lyrics_text = None
            lrc_path = f.with_suffix(".lrc")
            if lrc_path.exists():
                try:
                    lyrics_text = lrc_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    pass

            track_num = info.get("track")
            disc_num = info.get("disc") or 1
            track_artist = info.get("artist") or target_album_artist
            title = info.get("title") or f.stem

            try:
                embed_metadata(
                    file_path=str(f),
                    artist=track_artist,
                    title=title,
                    album=target_album,
                    track_num=track_num,
                    track_total=info.get("track_total"),
                    cover_bytes=target_cover_bytes,
                    lyrics_text=lyrics_text,
                    album_artist=target_album_artist,
                    date=target_date or info.get("date"),
                    disc_num=disc_num,
                    disc_total=info.get("disc_total") or 1,
                    is_explore=False,
                    mbid_album=target_mbid or info.get("release_mbid"),
                    mbid_recording=info.get("recording_mbid"),
                )
                updated_count += 1
            except Exception as e:
                logger.warning(f"Failed to harmonize metadata for {f.name}: {e}")

    if updated_count > 0:
        logger.info(f"Harmonized metadata for {updated_count} track(s) in '{dir_path.name}'.")
    return updated_count

def upgrade_or_merge_album_folders(
    music_dir: Union[str, Path],
    artist: str,
    target_album: str,
    official_album_tracks: Optional[List[Dict[str, Any]]] = None,
    official_album_cover_bytes: Optional[bytes] = None,
    official_mb_release_mbid: Optional[str] = None,
    official_album_date: Optional[str] = None,
    playlists_dir: Optional[Union[str, Path]] = None,
    target_album_artist: Optional[str] = None,
) -> List[Tuple[Dict[str, Any], Path]]:
    """
    When downloading/upgrading an album (e.g. 'Habibti (fomo)' when 'Habibti' exists),
    migrates tracks from the superseded base album folder(s) into the upgraded folder,
    retags them with the upgraded album metadata, removes the superseded folder,
    and returns the migrated file records.
    """
    from backend.app.metadata_pipeline.tags import read_track
    from backend.app.album_sync import match_file_to_official_track, extract_track_num_from_filename

    music_path = Path(music_dir)
    clean_artist = get_folder_artist_name(artist, target_album_artist)
    safe_artist = sanitize_filename(clean_artist)
    artist_dir = music_path / safe_artist

    if not artist_dir.exists() and music_path.exists():
        for child in music_path.iterdir():
            if child.is_dir() and child.name.lower() == safe_artist.lower():
                artist_dir = child
                safe_artist = child.name
                break

    if not artist_dir.exists() or not artist_dir.is_dir():
        return []

    superseded_folders = find_superseded_album_folders(artist_dir, target_album)
    if not superseded_folders:
        return []

    clean_album = get_clean_album_folder(target_album, clean_artist)
    safe_album = sanitize_filename(clean_album)
    target_album_dir = artist_dir / safe_album
    target_album_dir.mkdir(parents=True, exist_ok=True)

    # Discover existing metadata from target_album_dir or cover files
    if not official_album_cover_bytes:
        for img_name in ("cover.jpg", "cover.png", "folder.jpg", "folder.png"):
            img_path = target_album_dir / img_name
            if img_path.exists():
                try:
                    official_album_cover_bytes = img_path.read_bytes()
                    break
                except Exception:
                    pass

    existing_target_files = [
        p for p in target_album_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in [".mp3", ".flac", ".m4a", ".ogg"]
    ]
    for ef in existing_target_files:
        try:
            ef_info = read_track(ef, include_cover=True)
            if not official_album_cover_bytes and ef_info.get("cover"):
                official_album_cover_bytes = ef_info["cover"]
            if not official_mb_release_mbid and ef_info.get("release_mbid"):
                official_mb_release_mbid = ef_info["release_mbid"]
            if not official_album_date and ef_info.get("date"):
                official_album_date = ef_info["date"]
            if not target_album_artist and ef_info.get("album_artist"):
                target_album_artist = ef_info["album_artist"]
        except Exception:
            pass

    if official_album_cover_bytes and not (target_album_dir / "cover.jpg").exists():
        try:
            (target_album_dir / "cover.jpg").write_bytes(official_album_cover_bytes)
        except Exception:
            pass

    disc_total = 1
    track_total = len(official_album_tracks) if official_album_tracks else None
    if official_album_tracks:
        disc_total = max(
            (t.get("disk_number") or t.get("disc_num") or 1 for t in official_album_tracks),
            default=1
        )

    migrated_results: List[Tuple[Dict[str, Any], Path]] = []

    for old_dir in superseded_folders:
        logger.info(
            f"Found superseded album folder '{old_dir.name}' for upgraded album '{target_album}'. "
            f"Merging tracks into '{target_album_dir.name}'..."
        )
        old_files = [
            p for p in old_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in [".mp3", ".flac", ".m4a"]
        ]

        for old_file in old_files:
            matched = (
                match_file_to_official_track(old_file.name, official_album_tracks)
                if official_album_tracks else None
            )

            if matched:
                disc_num = matched.get("disk_number") or matched.get("disc_num") or 1
                track_num = matched.get("track_position") or matched.get("position")
                title_tag = matched.get("title") or old_file.stem
                track_artist = matched.get("artist") or artist
            else:
                try:
                    t_info = read_track(old_file)
                    track_num = t_info.get("track_number")
                    disc_num = t_info.get("disc_number") or 1
                    title_tag = t_info.get("title")
                    track_artist = t_info.get("artist") or artist
                except Exception:
                    track_num = None
                    disc_num = 1
                    title_tag = None
                    track_artist = artist

                if track_num is None:
                    track_num = extract_track_num_from_filename(old_file.name)
                if not title_tag or title_tag == old_file.stem:
                    stem = old_file.stem
                    if " - " in stem:
                        title_tag = stem.split(" - ")[-1].strip()
                    elif "_" in stem:
                        title_tag = stem.split("_")[-1].strip()
                    else:
                        title_tag = stem

            if disc_total > 1 and disc_num > 0:
                dest_dir = target_album_dir / f"Disc {disc_num:02d}"
                dest_dir.mkdir(parents=True, exist_ok=True)
            else:
                dest_dir = target_album_dir

            clean_filename = get_library_filename(
                track_artist, safe_album, track_num, title_tag, old_file.suffix
            )
            dest_path = dest_dir / clean_filename

            if dest_path.resolve() == old_file.resolve():
                continue

            if dest_path.exists():
                # Compare audio quality
                try:
                    ext_old, br_old, bd_old, sr_old = get_file_audio_info(old_file)
                    ext_new, br_new, bd_new, sr_new = get_file_audio_info(dest_path)
                    old_score = (1 if ext_old == "flac" else 0, br_old, bd_old, sr_old)
                    new_score = (1 if ext_new == "flac" else 0, br_new, bd_new, sr_new)
                except Exception:
                    old_score = (0, 0, 0, 0)
                    new_score = (0, 0, 0, 0)

                if new_score >= old_score:
                    try:
                        old_file.unlink()
                        old_lrc = old_file.with_suffix(".lrc")
                        if old_lrc.exists():
                            old_lrc.unlink()
                    except Exception as e:
                        logger.warning(f"Failed to remove duplicate old file {old_file}: {e}")
                    continue
                else:
                    try:
                        dest_path.unlink()
                        dest_lrc = dest_path.with_suffix(".lrc")
                        if dest_lrc.exists():
                            dest_lrc.unlink()
                    except Exception:
                        pass

            try:
                safe_move_file(old_file, dest_path)
                old_lrc = old_file.with_suffix(".lrc")
                if old_lrc.exists():
                    safe_move_file(old_lrc, dest_path.with_suffix(".lrc"))
            except Exception as move_err:
                logger.error(f"Failed to move superseded file {old_file} to {dest_path}: {move_err}")
                continue

            lyrics_text = None
            dest_lrc = dest_path.with_suffix(".lrc")
            if dest_lrc.exists():
                try:
                    lyrics_text = dest_lrc.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    pass

            try:
                embed_metadata(
                    file_path=str(dest_path),
                    artist=track_artist,
                    title=title_tag,
                    album=target_album,
                    track_num=track_num,
                    track_total=track_total,
                    cover_bytes=official_album_cover_bytes,
                    lyrics_text=lyrics_text,
                    album_artist=target_album_artist or clean_artist,
                    date=official_album_date,
                    disc_num=disc_num,
                    disc_total=disc_total,
                    is_explore=False,
                    mbid_album=official_mb_release_mbid,
                )
            except Exception as tag_err:
                logger.warning(f"Failed to retag upgraded track {dest_path}: {tag_err}")

            if playlists_dir and Path(playlists_dir).exists():
                try:
                    update_m3u_references(Path(playlists_dir), old_file.name, dest_path)
                except Exception as m3u_err:
                    logger.debug(f"Failed to update M3U references for {old_file.name}: {m3u_err}")

            mock_f = {
                "filename": str(dest_path),
                "track_num": track_num,
                "title": title_tag,
                "_official_position": (disc_num, track_num) if track_num is not None else None,
                "download_status": "succeeded"
            }
            migrated_results.append((mock_f, dest_path))

        # Check if old_dir has any remaining audio files
        remaining = [
            p for p in old_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in [".mp3", ".flac", ".m4a"]
        ]
        if not remaining:
            logger.info(f"Superseded album directory '{old_dir}' has no remaining audio tracks. Removing folder.")
            try:
                shutil.rmtree(old_dir, ignore_errors=True)
            except Exception as rm_err:
                logger.warning(f"Failed to remove empty superseded directory '{old_dir}': {rm_err}")

    # Harmonize the entire target album folder so all tracks share identical MBID, cover art, and album tags
    try:
        harmonize_album_directory(
            album_dir=target_album_dir,
            target_album=target_album,
            target_album_artist=target_album_artist or clean_artist,
            target_mbid=official_mb_release_mbid,
            target_cover_bytes=official_album_cover_bytes,
            target_date=official_album_date,
        )
    except Exception as harm_err:
        logger.warning(f"Failed to harmonize album directory '{target_album_dir}': {harm_err}")

    return migrated_results

def consolidate_all_split_albums(
    music_dir: Union[str, Path],
    playlists_dir: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """
    Scans music_dir for split album editions (e.g. 'Habibti' and 'Habibti (FOMO)' under the same artist).
    Merges tracks from the base edition into the upgraded edition and removes the empty base edition.
    Also harmonizes album tags and artwork across all album folders.
    """
    music_path = Path(music_dir)
    if not music_path.exists() or not music_path.is_dir():
        return []

    consolidated_summary = []
    try:
        for artist_dir in music_path.iterdir():
            if not artist_dir.is_dir():
                continue

            album_folders = [c for c in artist_dir.iterdir() if c.is_dir()]
            if not album_folders:
                continue

            # 1. Check for split editions across multiple folders
            if len(album_folders) >= 2:
                groups: Dict[str, List[Path]] = {}
                for folder in album_folders:
                    base_norm = normalize_album_name(folder.name)
                    if not base_norm:
                        continue
                    groups.setdefault(base_norm, []).append(folder)

                for base_norm, folders in groups.items():
                    if len(folders) < 2:
                        continue

                    def _edition_priority(folder: Path):
                        has_edition = bool(EDITION_SUFFIX_RE.search(folder.name))
                        audio_count = len([
                            p for p in folder.rglob("*")
                            if p.is_file() and p.suffix.lower() in [".mp3", ".flac", ".m4a"]
                        ])
                        return (has_edition, audio_count, len(folder.name))

                    sorted_folders = sorted(folders, key=_edition_priority, reverse=True)
                    target_folder = sorted_folders[0]
                    target_album_name = target_folder.name

                    migrated = upgrade_or_merge_album_folders(
                        music_dir=music_path,
                        artist=artist_dir.name,
                        target_album=target_album_name,
                        playlists_dir=playlists_dir,
                    )
                    if migrated:
                        consolidated_summary.append({
                            "artist": artist_dir.name,
                            "target_album": target_album_name,
                            "migrated_tracks": len(migrated),
                        })

            # 2. Harmonize tags inside each album folder (fixes internal tag inconsistency)
            remaining_folders = [c for c in artist_dir.iterdir() if c.is_dir()]
            for folder in remaining_folders:
                harmonized_count = harmonize_album_directory(folder)
                if harmonized_count > 0:
                    existing_entry = next(
                        (item for item in consolidated_summary if item["artist"] == artist_dir.name and item["target_album"] == folder.name),
                        None
                    )
                    if existing_entry:
                        existing_entry["harmonized_tracks"] = harmonized_count
                    else:
                        consolidated_summary.append({
                            "artist": artist_dir.name,
                            "target_album": folder.name,
                            "harmonized_tracks": harmonized_count,
                        })
    except Exception as e:
        logger.error(f"Error during consolidate_all_split_albums: {e}")

    return consolidated_summary

def resolve_album_dir(music_dir: Union[str, Path], artist: str, album: str, album_artist: str = "", disc_num: int = 1, disc_total: int = 1) -> Tuple[Path, str, str]:
    """
    Resolves the target album directory under music_dir/Artist/Album (or music_dir/Artist/Album/Disc 0X if disc_total > 1).
    Reuses an existing album directory if a case-insensitive or normalized match exists.
    Returns (target_folder_path, safe_artist_name, safe_album_name).
    """
    clean_folder_artist = get_folder_artist_name(artist, album_artist)
    clean_folder_album = get_clean_album_folder(album, clean_folder_artist)
    
    safe_artist = sanitize_filename(clean_folder_artist)
    safe_album = sanitize_filename(clean_folder_album)
    
    music_path = Path(music_dir)
    artist_dir = music_path / safe_artist
    
    if not artist_dir.exists() and music_path.exists():
        try:
            for child in music_path.iterdir():
                if child.is_dir() and child.name.lower() == safe_artist.lower():
                    artist_dir = child
                    safe_artist = child.name
                    break
        except Exception:
            pass

    album_dir = artist_dir / safe_album
    if artist_dir.exists() and artist_dir.is_dir():
        existing_album_dir = find_existing_album_folder(artist_dir, clean_folder_album)
        if existing_album_dir:
            album_dir = existing_album_dir
            safe_album = existing_album_dir.name

    if disc_total > 1 and disc_num > 0:
        target = album_dir / f"Disc {disc_num:02d}"
    else:
        target = album_dir
    
    target.mkdir(parents=True, exist_ok=True)
    return target, safe_artist, safe_album

def get_safe_filename(artist: str, title: str, ext: str) -> str:
    return f"{sanitize_filename(artist)} - {sanitize_filename(title)}{ext}"

def get_library_filename(artist: str, album: str, track_num: Optional[int], title: str, ext: str) -> str:
    """Format library track filename as Artist_Album_TrackNum_Title.ext."""
    safe_artist = sanitize_filename(artist)
    safe_album = sanitize_filename(album)
    safe_title = sanitize_filename(title)
    
    if track_num and track_num > 0:
        track_str = f"{track_num:02d}"
        return f"{safe_artist}_{safe_album}_{track_str}_{safe_title}{ext}"
    else:
        return f"{safe_artist}_{safe_album}_{safe_title}{ext}"

def sanitize_artist_name(artist_str: str) -> str:
    """
    Clean artist strings by splitting on slashes/semicolons, removing empty strings,
    and joining with clean delimiters. Eliminates trailing slashes like 'Drake/' or 'Drake//'.
    """
    if not artist_str:
        return ""
    parts = re.split(r'[\/\\;\x00]+', str(artist_str))
    cleaned = [p.strip() for p in parts if p and p.strip()]
    if not cleaned:
        return ""
    return " / ".join(cleaned)

def strip_scene_tags(text: str) -> str:
    """
    Strip release group scene tags and quality descriptors from titles or basenames.
    E.g. "Intro (PMEDIA)" -> "Intro", "Rich Flex [PMEDIA] ⭐️" -> "Rich Flex".
    """
    if not text:
        return ""
    s = text
    s = re.sub(r'(?i)[\(\[]?\b(PMEDIA|320kbps|320|FLAC|MP3|WEB|Explicit|HQ|CD|V0|V2|PMedia|PMedia1)\b[\)\]]?', '', s)
    s = re.sub(r'[⭐️⭐★✨]', '', s)
    s = re.sub(r'\s+', ' ', s).strip(' -_./\\')
    return s if s else text


def strip_promotional_text(text: Optional[str]) -> str:
    """Remove distributor links from metadata supplied by downloaded files."""
    if not text:
        return ""
    cleaned = re.sub(
        r'(?i)\b(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/[\w.-]+',
        '', text,
    )
    cleaned = re.sub(r'(?i)\b(?:www\.)?pmedia(?:_music|1)?\b', '', cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip(' -_.,;:/\\')

def embed_metadata(
    file_path: str,
    artist: str,
    title: str,
    album: Optional[str] = None,
    track_num: Optional[int] = None,
    track_total: Optional[int] = None,
    cover_bytes: Optional[bytes] = None,
    lyrics_text: Optional[str] = None,
    album_artist: Optional[str] = None,
    date: Optional[str] = None,
    disc_num: int = 1,
    disc_total: int = 1,
    is_explore: bool = False,
    mbid_album: Optional[str] = None,
    mbid_recording: Optional[str] = None,
    genre: Optional[str] = None,
    credited_artists: Optional[List[str]] = None,
    album_artists: Optional[List[str]] = None,
    album_artist_mbids: Optional[List[str]] = None,
):
    """Embed metadata, cover art, and lyrics directly into the audio file metadata.

    Navidrome tag strategy (per official mappings.yaml and tagging guidelines):
    - TPE1 / ARTIST       : full display name incl. feat. ("Drake feat. Jay-Z")
    - TPE2 / ALBUMARTIST  : exact album-credit display string.
    - TXXX:artists and TXXX:albumartists: authoritative multi-value entities,
      each becoming a separately clickable artist page in Navidrome.
    - ID3v2.4 is used for MP3 to support true multi-value frames.
    - All pre-existing tags are wiped before writing to eliminate scene-release junk.
    """
    ext = os.path.splitext(file_path)[1].lower()

    # Provider display strings are editorial data. Preserve punctuation and
    # join phrases verbatim; normalization is only for matching/search.
    clean_artist = strip_promotional_text(str(artist or "").strip()) or "Unknown Artist"
    clean_title = strip_scene_tags(strip_promotional_text(str(title or "").strip())) or "Unknown Title"
    clean_genre = strip_promotional_text(genre)
    title_feature = re.search(r'(?i)[\(\[]\s*(?:feat\.?|ft\.?|featuring)\s+([^\)\]]+)[\)\]]', clean_title)
    featured_from_title = title_feature.group(1).strip() if title_feature else ""
    if featured_from_title and featured_from_title.casefold() not in clean_artist.casefold():
        clean_artist = f"{clean_artist} feat. {featured_from_title}"

    if is_explore:
        final_album = "Explore Tracks"
        final_album_artist = "Various Artists"
        compilation_val = "1"
    else:
        final_album = strip_promotional_text(album) or f"{clean_title} - Single"
        final_album_artist = strip_promotional_text(
            str(album_artist or artist or clean_artist).strip()
        ) or clean_artist
        compilation_val = "0"

    final_album_artists = list(dict.fromkeys(
        value.strip() for value in (
            ["Various Artists"] if is_explore else (album_artists or [final_album_artist])
        ) if value and value.strip()
    ))
    final_album_artist_mbids = list(dict.fromkeys(
        value.strip() for value in (album_artist_mbids or []) if value and value.strip()
    ))

    # ── Build individual artist list for Navidrome ARTISTS multi-value tag ──────
    # Splitting the display-artist string yields the individually linkable artists.
    # This is used for TXXX:artists (MP3), artists Vorbis tag (FLAC), and
    # ----:com.apple.iTunes:artists (M4A). See Navidrome mappings.yaml.
    feat_artists: list[str] = []
    if not is_explore:
        if credited_artists:
            feat_artists = list(dict.fromkeys(a.strip() for a in credited_artists if a and a.strip()))
        else:
            # 'and' and 'with' can be part of a band's name: never invent
            # artist entities from those words without provider credits.
            normalized_credit = re.sub(r'(?i)[\(\[]\s*(feat\.?|ft\.?|featuring)\s+', r' \1 ', clean_artist)
            normalized_credit = re.sub(r'[\)\]]\s*$', '', normalized_credit)
            feat_artists = [a.strip() for a in re.split(r'(?i)\s+(?:feat\.?|ft\.?|featuring)\s+', normalized_credit) if a.strip()]
        if not feat_artists:
            feat_artists = [clean_artist]
        if featured_from_title and not any(featured_from_title.casefold() == name.casefold() for name in feat_artists):
            feat_artists.append(featured_from_title)

    cover_format = None
    if cover_bytes:
        if cover_bytes.startswith(b'\xff\xd8\xff'):
            cover_format = 'jpeg'
        elif cover_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
            cover_format = 'png'
        else:
            logger.warning(f"Skipping unsupported cover format for {file_path}")

    try:
        if ext == ".mp3":
            from mutagen.id3 import (
                ID3, ID3NoHeaderError,
                TIT2, TPE1, TPE2, TALB, TRCK, TPOS, TCMP, TCON,
                TDRC, TDRL, TYER,
                TXXX, UFID,
                USLT, APIC
            )
            # ── Full tag wipe: erase ID3v1 + ID3v2 from disk, start fresh ──────
            try:
                tags = ID3(file_path)
                tags.delete(file_path)   # removes both v1 and v2 from disk
            except ID3NoHeaderError:
                pass
            tags = ID3()             # clean in-memory tag set

            # ── Core track tags ───────────────────────────────────────────────
            tags.add(TIT2(encoding=3, text=clean_title))
            # TPE1 = display name (full feat. string) shown in the player
            tags.add(TPE1(encoding=3, text=clean_artist))
            # TPE2 = album artist for grouping — main artist only, no duplicates
            tags.add(TPE2(encoding=3, text=final_album_artist))
            tags.add(TALB(encoding=3, text=final_album))
            tags.add(TCMP(encoding=3, text=compilation_val))
            if clean_genre:
                tags.add(TCON(encoding=3, text=clean_genre))

            if is_explore:
                tags.add(TPOS(encoding=3, text="1/1"))
            else:
                disc_str = f"{disc_num}/{disc_total}" if disc_total else str(disc_num)
                tags.add(TPOS(encoding=3, text=disc_str))
                if date:
                    # TDRC → recordingdate (Navidrome mappings.yaml: tdrc alias)
                    tags.add(TDRC(encoding=3, text=date))
                    # TDRL → releasedate (Navidrome mappings.yaml: tdrl alias)
                    tags.add(TDRL(encoding=3, text=date))
                    if len(date) >= 4:
                        tags.add(TYER(encoding=3, text=date[:4]))

                # ── Multi-value ARTISTS (Navidrome: txxx:artists) ─────────────
                # When both TPE1 and TXXX:artists exist, Navidrome uses TPE1 as
                # display-only and TXXX:artists as the authoritative artist list.
                # No splitting of TPE1 occurs; each feat. artist gets their own page.
                if feat_artists:
                    tags.add(TXXX(encoding=3, desc="artists", text=feat_artists))
                if final_album_artists:
                    tags.add(TXXX(encoding=3, desc="albumartists", text=final_album_artists))
                if final_album_artist_mbids:
                    tags.add(TXXX(encoding=3, desc="musicbrainz album artist id", text=final_album_artist_mbids))

                # ── MusicBrainz IDs (exact frame names from Navidrome mappings.yaml) ─
                if mbid_recording:
                    # musicbrainz_recordingid → ufid:http://musicbrainz.org
                    tags.add(UFID(owner="http://musicbrainz.org",
                                  data=mbid_recording.encode('utf-8')))
                if mbid_album:
                    # musicbrainz_albumid → txxx:musicbrainz album id
                    tags.add(TXXX(encoding=3, desc="musicbrainz album id",
                                  text=[mbid_album]))

            if track_num:
                trck_str = (f"{track_num}/{track_total}"
                            if track_total and not is_explore
                            else str(track_num))
                tags.add(TRCK(encoding=3, text=trck_str))

            if lyrics_text:
                # USLT desc='' matches Navidrome alias: uslt:description
                tags.add(USLT(encoding=3, lang='eng', desc='', text=lyrics_text))
            if cover_format:
                tags.add(APIC(encoding=3, mime=f'image/{cover_format}',
                               type=3, desc='Cover', data=cover_bytes))

            # Save as ID3v2.4 — required for true multi-value frame support
            tags.save(file_path, v2_version=4)
            
        elif ext in [".flac", ".ogg"]:
            from mutagen.flac import FLAC, Picture
            audio = FLAC(file_path)

            # ── Full tag wipe ──────────────────────────────────────────────────
            audio.clear()
            audio.clear_pictures()

            # ── Core Vorbis comment tags ───────────────────────────────────────
            # ARTIST = display name (Navidrome: tpe1/artist alias)
            audio["artist"] = [clean_artist]
            # ALBUMARTIST = album grouping artist (Navidrome: tpe2/albumartist alias)
            audio["albumartist"] = [final_album_artist]
            audio["album"] = [final_album]
            audio["title"] = [clean_title]
            audio["compilation"] = [compilation_val]
            if clean_genre:
                audio["genre"] = [clean_genre]

            if is_explore:
                audio["discnumber"] = ["1"]
                audio["disctotal"] = ["1"]
            else:
                audio["discnumber"] = [str(disc_num)]
                audio["disctotal"] = [str(disc_total)]
                if date:
                    # DATE → recordingdate in Navidrome (mappings.yaml: date alias)
                    audio["date"] = [date]
                    # RELEASEDATE → releasedate in Navidrome
                    audio["releasedate"] = [date]
                    if len(date) >= 4:
                        audio["year"] = [date[:4]]

                # ── Multi-value ARTISTS (Navidrome mappings.yaml: artists Vorbis tag) ─
                # Mutagen writes each list entry as a separate Vorbis comment line,
                # giving Navidrome a clean individual artist list with no splitting needed.
                if feat_artists:
                    audio["artists"] = feat_artists
                if final_album_artists:
                    audio["albumartists"] = final_album_artists
                if final_album_artist_mbids:
                    audio["musicbrainz_albumartistid"] = final_album_artist_mbids

                # ── MusicBrainz IDs (exact Vorbis tag names from Navidrome mappings.yaml) ──
                if mbid_recording:
                    # musicbrainz_recordingid → musicbrainz_trackid (Vorbis)
                    audio["musicbrainz_trackid"] = [mbid_recording]
                if mbid_album:
                    # musicbrainz_albumid → musicbrainz_albumid (Vorbis)
                    audio["musicbrainz_albumid"] = [mbid_album]

            if track_num:
                audio["tracknumber"] = [str(track_num)]
                if track_total and not is_explore:
                    audio["tracktotal"] = [str(track_total)]

            if lyrics_text:
                # lyrics → Navidrome mappings.yaml: unsyncedlyrics/lyrics alias
                audio["lyrics"] = [lyrics_text]
            if cover_format:
                pic = Picture()
                pic.type = 3
                pic.mime = f"image/{cover_format}"
                pic.desc = "Cover"
                pic.data = cover_bytes
                audio.add_picture(pic)
            audio.save()

        elif ext in [".m4a", ".mp4"]:
            from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
            audio = MP4(file_path)

            # ── Full tag wipe ──────────────────────────────────────────────────
            audio.clear()

            # ── Core MP4 atom tags ─────────────────────────────────────────────
            # ©ART → Navidrome mappings.yaml: ©art alias for artist (display name)
            audio["©ART"] = [clean_artist]
            # aART → Navidrome mappings.yaml: aart alias for albumartist
            audio["aART"] = [final_album_artist]
            audio["©nam"] = [clean_title]
            audio["©alb"] = [final_album]
            audio["cpil"] = is_explore
            if clean_genre:
                audio["©gen"] = [clean_genre]

            if is_explore:
                audio["disk"] = [(1, 1)]
            else:
                audio["disk"] = [(disc_num, disc_total)]
                if date:
                    # ©day → releasedate in Navidrome (mappings.yaml: ©day alias)
                    audio["©day"] = [date]

                # ── Multi-value ARTISTS (Navidrome: ----:com.apple.itunes:artists) ─
                if feat_artists:
                    audio["----:com.apple.iTunes:artists"] = [
                        MP4FreeForm(a.encode("utf-8")) for a in feat_artists
                    ]
                if final_album_artists:
                    audio["----:com.apple.iTunes:albumartists"] = [
                        MP4FreeForm(a.encode("utf-8")) for a in final_album_artists
                    ]
                if final_album_artist_mbids:
                    audio["----:com.apple.iTunes:MusicBrainz Album Artist Id"] = [
                        MP4FreeForm(value.encode("utf-8")) for value in final_album_artist_mbids
                    ]

                # ── MusicBrainz IDs (exact atom names from Navidrome mappings.yaml) ──
                if mbid_recording:
                    # musicbrainz_recordingid → ----:com.apple.itunes:musicbrainz track id
                    audio["----:com.apple.iTunes:MusicBrainz Track Id"] = [
                        MP4FreeForm(mbid_recording.encode("utf-8"))
                    ]
                if mbid_album:
                    # musicbrainz_albumid → ----:com.apple.itunes:musicbrainz album id
                    audio["----:com.apple.iTunes:MusicBrainz Album Id"] = [
                        MP4FreeForm(mbid_album.encode("utf-8"))
                    ]

            if track_num:
                audio["trkn"] = [(int(track_num),
                                   int(track_total or 0) if not is_explore else 0)]
            if lyrics_text:
                audio["©lyr"] = [lyrics_text]
            if cover_format:
                image_format = MP4Cover.FORMAT_PNG if cover_format == 'png' else MP4Cover.FORMAT_JPEG
                audio["covr"] = [MP4Cover(cover_bytes, imageformat=image_format)]
            audio.save()

        # Touch mtime so Navidrome's file watcher immediately notices changes
        try:
            os.utime(file_path, None)
        except Exception:
            pass

        logger.info(f"Embedded metadata and cover art into: {file_path}")
    except Exception as e:
        logger.error(f"Failed to embed metadata into {file_path}: {e}")
        raise

def extract_main_artist(artist_name: str) -> str:
    """
    Extracts the primary main artist from a joint or featured artist string.
    Handles 'ASAP Rocky and Tim Burton', 'A$AP Rocky x Tim Burton', 'A$AP Rocky & Tim Burton',
    'A$AP Rocky feat. Tim Burton', 'Vasco Rossi, Marracash', etc.
    """
    if not artist_name:
        return ""
    # Normalize $ -> S (e.g. A$AP -> ASAP, Ke$ha -> Kesha)
    art = re.sub(r'\$', 'S', artist_name)
    # Remove parenthetical extras
    art = re.sub(r'[\(\[].*?[\)\]]', '', art).strip()
    # Split on feature/joint separators
    parts = re.split(r'(?i)\b(?:feat\.?|ft\.?|featuring)\b', art)
    main = parts[0].strip() if parts else art
    # Handle Spotify/AcoustID comma-separated multi-artist credits (e.g. 'Vasco Rossi, Marracash')
    # while preserving single artists like 'Tyler, The Creator' or 'Earth, Wind & Fire'.
    if "," in main and not re.search(r'(?i),\s*the\s+creator\b', main) and "&" not in main:
        comma_parts = [p.strip() for p in main.split(",") if p.strip()]
        if comma_parts:
            main = comma_parts[0]
    return main or art

def get_artist_aliases(artist_name: str) -> list[str]:
    """Get list of normalized aliases and individual artist segments to handle joint/featured artists."""
    if not artist_name:
        return []

    aliases = set()

    # 1. Convert $ to s / S so A$AP becomes ASAP
    normalized_art = re.sub(r'\$', 's', artist_name, flags=re.IGNORECASE)
    parts = re.split(r'(?i)\b(?:feat\.?|ft\.?|featuring)\b|,|&', normalized_art)
    for p in parts:
        clean_p = re.sub(r'[^\w]', '', p).lower().strip()
        if clean_p:
            aliases.add(clean_p)
            stop_words = {"the", "and", "for"}
            words = [w for w in re.findall(r'\w+', p.lower()) if len(w) > 3 and w not in stop_words]
            for w in words:
                aliases.add(w)

    full_clean = re.sub(r'[^\w]', '', normalized_art).lower().strip()
    if full_clean:
        aliases.add(full_clean)

    # 2. Also keep raw parts without $ conversion (e.g. A$AP -> aap)
    raw_parts = re.split(r'(?i)\b(?:feat\.?|ft\.?|featuring)\b|,|&', artist_name)
    for rp in raw_parts:
        clean_rp = re.sub(r'[^\w]', '', rp).lower().strip()
        if clean_rp:
            aliases.add(clean_rp)

    # Ensure raw symbol artist names (e.g. ¥$, !!!) are included if alphanumeric stripping emptied them
    symbol_art = re.sub(r'\s+', '', artist_name).lower().strip()
    if symbol_art:
        aliases.add(symbol_art)

    # Special artist aliases
    if any(k in aliases for k in ["kanyewest", "ye", "kanye"]):
        aliases.update(["kanyewest", "ye", "kanye"])
    if any(a in aliases for a in ["asaprocky", "aaprocky", "asap"]):
        aliases.update(["asaprocky", "aaprocky", "asap", "rocky"])
    if "¥$" in artist_name or any(k in aliases for k in ["¥$", "ys"]):
        aliases.update(["¥$", "ys", "kanyewest", "tydolla", "tydollasign", "ye", "kanyewesttydollasign"])
    elif any(k in aliases for k in ["kanyewest", "ye"]) and any(k in aliases for k in ["tydolla", "tydollasign"]):
        aliases.update(["¥$", "ys"])

    return list(aliases)

def extract_audio_mbids(file_path: Union[str, Path]) -> Tuple[Optional[str], Optional[str]]:
    """Extracts (musicbrainz_trackid, musicbrainz_albumid) from audio file metadata using Mutagen."""
    p = Path(file_path)
    ext = p.suffix.lower()
    track_mbid = None
    album_mbid = None
    
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, UFID, TXXX
            tags = ID3(file_path)
            for ufid in tags.getall("UFID"):
                if ufid.owner in ("http://musicbrainz.org", "musicbrainz.org"):
                    track_mbid = ufid.data.decode("utf-8", errors="ignore")
                    break
            if not track_mbid:
                for txxx in tags.getall("TXXX"):
                    if txxx.desc.lower() in ("musicbrainz track id", "musicbrainz_trackid"):
                        track_mbid = txxx.text[0] if txxx.text else None
                        break
            for txxx in tags.getall("TXXX"):
                if txxx.desc.lower() in ("musicbrainz album id", "musicbrainz_albumid", "musicbrainz release group id"):
                    album_mbid = txxx.text[0] if txxx.text else None
                    break

        elif ext in (".flac", ".ogg"):
            from mutagen.flac import FLAC
            audio = FLAC(file_path)
            track_mbid = audio.get("musicbrainz_trackid", [None])[0] or audio.get("musicbrainz_releasetrackid", [None])[0]
            album_mbid = audio.get("musicbrainz_albumid", [None])[0] or audio.get("musicbrainz_releasegroupid", [None])[0]

        elif ext in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4
            audio = MP4(file_path)
            t_data = audio.get("----:com.apple.iTunes:MusicBrainz Track Id")
            if t_data and t_data[0]:
                track_mbid = t_data[0].decode("utf-8", errors="ignore") if isinstance(t_data[0], bytes) else str(t_data[0])
            a_data = audio.get("----:com.apple.iTunes:MusicBrainz Album Id")
            if a_data and a_data[0]:
                album_mbid = a_data[0].decode("utf-8", errors="ignore") if isinstance(a_data[0], bytes) else str(a_data[0])
    except Exception as e:
        logger.debug(f"Failed to extract MBID tags from {file_path}: {e}")

    return track_mbid, album_mbid

def clean_track_filename(filename: str, target_artist: str = "") -> str:
    """Strips audio extensions, artist prefixes, album+tracknum prefixes, track/disc numbers, and audio format tags."""
    base = os.path.splitext(filename)[0] if any(filename.lower().endswith(ext) for ext in ['.mp3', '.flac', '.m4a', '.wav', '.ogg', '.lrc']) else filename
    if target_artist:
        main_art = extract_main_artist(target_artist)
        candidate_prefixes = [
            target_artist,
            sanitize_filename(target_artist),
            main_art,
            sanitize_filename(main_art),
            *get_artist_aliases(target_artist),
        ]
        for art_alias in sorted({p for p in candidate_prefixes if p}, key=len, reverse=True):
            pattern = rf'(?i)^{re.escape(art_alias)}\s*[-_]+\s*'
            stripped = re.sub(pattern, '', base).strip(' -_./\\')
            if stripped != base and stripped:
                base = stripped
                break
    # Handle library naming convention: {Artist}_{Album}_{TrackNum:02d}_{Title} (or {Album}_{TrackNum:02d}_{Title})
    lib_match = re.match(r'^.+?_\d{1,3}_(.+)$', base)
    if lib_match and lib_match.group(1).strip():
        base = lib_match.group(1).strip()
    base = re.sub(r'^(?:\d+[-._\s]+|\d+\.\s*|[a-z]\d+[\s._-]+|\d+_\d+[\s._-]+)', '', base, flags=re.IGNORECASE).strip()
    base = re.sub(r'(?i)\s*[\(\[](?:remastered|remaster|live|explicit|clean|audio|bonus|deluxe|version|edit|hd|hq)[\)\]]', '', base).strip()
    return base

def match_track_titles(target_title: str, candidate_filename: str, target_artist: str = "") -> bool:
    """Precision matching between target track title and candidate audio filename."""
    clean_candidate = clean_track_filename(candidate_filename, target_artist)
    
    def norm(txt: str):
        t = txt.lower().replace('_', ' ').replace('&', 'and')
        return re.sub(r'[^\w]', '', t)
        
    target_variants = {norm(target_title), norm(clean_search_title(target_title))}
    candidate_variants = {norm(clean_candidate), norm(clean_search_title(clean_candidate))}
    target_variants.discard("")
    candidate_variants.discard("")
    
    if not target_variants or not candidate_variants:
        return False

    if target_variants & candidate_variants:
        return True

    raw_target = re.sub(r'[^\w]', '', target_title.lower().replace('_', ''))
    raw_candidate = re.sub(r'[^\w]', '', clean_candidate.lower().replace('_', ''))
    if raw_target and raw_target == raw_candidate:
        return True

    return False

def find_existing_track_file(
    music_dir: str, 
    playlist_dir: str, 
    staging_dir: str, 
    artist: str, 
    title: str, 
    library_index: Optional[Any] = None,
    target_mbid: Optional[str] = None
) -> Tuple[Optional[Path], Optional[Path]]:
    """Check if track audio and lyrics already exist in staging, playlist dir, or broader music library."""
    
    # Primary Strategy: Match by exact MusicBrainz Track ID (MBID) if available
    mbid_map = {}
    filename_map = {}
    if isinstance(library_index, dict):
        mbid_map = library_index.get("mbid_index", {})
        filename_map = library_index.get("filename_index", library_index)
    elif library_index is not None:
        filename_map = library_index

    if target_mbid and target_mbid in mbid_map:
        audio_path = mbid_map[target_mbid]
        if audio_path.is_file():
            lrc = audio_path.with_suffix(".lrc")
            return audio_path, lrc if lrc.is_file() else None

    safe_basename = f"{sanitize_filename(artist)} - {sanitize_filename(title)}"
    
    # Check staging dir first
    staging_path = Path(staging_dir)
    if staging_path.exists() and staging_path.is_dir():
        for ext in [".mp3", ".flac", ".m4a"]:
            test_path = staging_path / f"{safe_basename}{ext}"
            if test_path.is_file():
                lyrics = test_path.with_suffix(".lrc")
                return test_path, lyrics if lyrics.is_file() else None

    playlist_path = Path(playlist_dir)
    
    if playlist_path.exists() and playlist_path.is_dir():
        for ext in [".mp3", ".flac", ".m4a"]:
            test_path = playlist_path / f"{safe_basename}{ext}"
            if test_path.is_file():
                lyrics = test_path.with_suffix(".lrc")
                return test_path, lyrics if lyrics.is_file() else None

    # Check explore master folder (under playlists directory)
    explore_path = playlist_path.parent / "explore"
    if explore_path.exists() and explore_path.is_dir():
        for ext in [".mp3", ".flac", ".m4a"]:
            test_path = explore_path / f"{safe_basename}{ext}"
            if test_path.is_file():
                lyrics = test_path.with_suffix(".lrc")
                return test_path, lyrics if lyrics.is_file() else None

    norm_artists = get_artist_aliases(artist)

    # Check local directories loosely (staging, playlist output, and explore master)
    local_dirs = []
    if staging_path.exists() and staging_path.is_dir():
        local_dirs.append(staging_path)
    if playlist_path.exists() and playlist_path.is_dir():
        local_dirs.append(playlist_path)
    explore_path = playlist_path.parent / "explore"
    if explore_path.exists() and explore_path.is_dir():
        local_dirs.append(explore_path)
        
    for l_dir in local_dirs:
        try:
            for f in l_dir.iterdir():
                if f.is_file() and f.suffix.lower() in [".mp3", ".flac", ".m4a"]:
                    norm_f = re.sub(r'[^\w]', '', f.name).lower()
                    if any(a in norm_f for a in norm_artists):
                        if match_track_titles(title, f.name, artist):
                            lrc = f.with_suffix(".lrc")
                            return f, lrc if lrc.is_file() else None
        except Exception:
            pass

    # Use pre-built library index if available for O(1) in-memory scanning
    if filename_map:
        for norm_f, audio_path in filename_map.items():
            if any(a in norm_f for a in norm_artists):
                if match_track_titles(title, audio_path.name, artist):
                    lrc = audio_path.with_suffix(".lrc")
                    return audio_path, lrc if lrc.is_file() else None
        return None, None

    # Fallback to recursively checking music_dir using os.walk
    music_path = Path(music_dir)
    if not music_path.exists() or not music_path.is_dir():
        return None, None

    for root, _, files in os.walk(music_dir):
        for f in files:
            if f.lower().endswith((".mp3", ".flac", ".m4a")):
                rel_path = os.path.relpath(os.path.join(root, f), music_dir)
                norm_f = re.sub(r'[^\w]', '', rel_path).lower()
                if any(a in norm_f for a in norm_artists):
                    if match_track_titles(title, f, artist):
                        audio_path = Path(root) / f
                        lrc = audio_path.with_suffix(".lrc")
                        return audio_path, lrc if lrc.is_file() else None
                    
    return None, None

def find_downloaded_file(downloads_dir: str, target_filename: str, target_size: int) -> Optional[Path]:
    """Locate one completed file without guessing among unrelated downloads."""
    import re

    basename = os.path.basename(target_filename.replace("\\", "/"))
    
    downloads_path = Path(downloads_dir)
    if not downloads_path.exists():
        logger.error(f"slskd downloads directory does not exist: {downloads_dir}")
        return None

    audio_exts = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".alac"}

    candidates = []
    try:
        rglob_results = list(downloads_path.rglob("*"))
        for path in rglob_results:
            if path.is_file() and path.suffix.lower() in audio_exts:
                if any("incomplete" in part.lower() for part in path.parts):
                    continue
                candidates.append(path)
    except Exception as e:
        logger.error(f"Error scanning downloads directory {downloads_dir}: {e}")
        return None

    # slskd reports the expected size. Never fall back to an unrelated size.
    def valid_size(path: Path) -> bool:
        try:
            actual_size = path.stat().st_size
            return actual_size > 0 and (target_size <= 0 or abs(actual_size - target_size) / target_size < 0.05)
        except OSError:
            return False

    def norm_name(s: str) -> str:
        name_no_ext = os.path.splitext(s)[0]
        return re.sub(r'[^\w]', '', name_no_ext).lower()

    exact = [p for p in candidates if p.name.lower() == basename.lower() and valid_size(p)]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        logger.warning(f"Ambiguous slskd download: {basename} has {len(exact)} matching files")
        return None

    target_norm = norm_name(basename)
    normalized = [p for p in candidates if p.suffix.lower() == Path(basename).suffix.lower()
                  and norm_name(p.name) == target_norm and valid_size(p)] if target_norm else []
    if len(normalized) == 1:
        return normalized[0]
    if len(normalized) > 1:
        logger.warning(f"Ambiguous normalized slskd download: {basename}")

    return None

async def relocate_and_tag_download(
    lrclib_client: LrcLibClient,
    deezer_client: DeezerClient,
    remote_filename: str,
    size: int,
    artist: str,
    title: str,
    downloads_dir: str,
    music_dir: str,
    dest_dir: Optional[str] = None,
    album: Optional[str] = None,
    album_override: Optional[str] = None
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Relocate a downloaded file and embed metadata, cover art, and lyrics.

    If dest_dir is provided, the file is placed directly in that directory
    with a flat 'Artist - Title.ext' filename (used for explore-only tracks).
    Otherwise the file is placed under music_dir/Artist/Album/ (library structure).
    """
    # Locate downloaded file recursively
    downloaded_file = find_downloaded_file(downloads_dir, remote_filename, size)
    if not downloaded_file:
        logger.error(f"Download succeeded but file not found in slskd downloads: '{remote_filename}' size={size}")
        return "failed", None, None

    # Get extension
    ext = os.path.splitext(remote_filename)[1]
    if not ext:
        ext = ".mp3"

    # Clean title and artist strings (strip trailing hex hashes, replace underscores)
    title_clean = re.sub(r'[-_][a-f0-9]{6,8}$', '', title)
    title_clean = title_clean.replace("_", " ").strip()
    
    artist_clean = re.sub(r'[-_][a-f0-9]{6,8}$', '', artist)
    artist_clean = artist_clean.replace("_", " ").strip()

    # Fetch metadata using MusicBrainz primary, Deezer fallback
    from backend.app.album_sync import fetch_track_metadata_with_fallback
    meta_result = await fetch_track_metadata_with_fallback(deezer_client, artist_clean, title_clean, album)
    
    dz_title = meta_result.get("title") or title_clean
    dz_artist = meta_result.get("artist") or artist_clean
    dz_album_artist = meta_result.get("album_artist") or artist_clean
    dz_album = album_override or meta_result.get("album") or album or f"{title_clean} - Single"
    track_num = meta_result.get("track_num")
    track_total = meta_result.get("track_total")
    disc_num = meta_result.get("disc_num", 1)
    disc_total = meta_result.get("disc_total", 1)
    mbid_album = meta_result.get("mbid_album")
    mbid_recording = meta_result.get("mbid_recording")
    cover_bytes = meta_result.get("cover_bytes")
    dz_date = meta_result.get("date")

    # If cover art wasn't retrieved yet, try Deezer get_album_cover
    if not cover_bytes:
        try:
            cover_bytes = await deezer_client.get_album_cover(artist_clean, dz_album)
        except Exception:
            pass

    is_explore = dest_dir is not None

    if dest_dir:
        # Explore-only: flat file in dest_dir as 'Artist - Title.ext'
        target_folder = Path(dest_dir)
        target_folder.mkdir(parents=True, exist_ok=True)
        safe_audio_name = get_safe_filename(dz_artist, dz_title, ext)
    else:
        # Library: resolve target directory under main music_dir (handling multi-disc)
        target_folder, safe_artist, safe_album = resolve_album_dir(
            music_dir, dz_artist, dz_album, dz_album_artist, disc_num=disc_num, disc_total=disc_total
        )
        safe_audio_name = get_library_filename(dz_artist, safe_album, track_num, dz_title, ext)

    dest_audio_path = target_folder / safe_audio_name

    # Relocate
    try:
        await asyncio.to_thread(safe_move_file, downloaded_file, dest_audio_path)
        logger.info(f"Moved downloaded track to library: '{dest_audio_path}'")
    except Exception as e:
        logger.error(f"Failed to relocate file to library: {e}")
        return "failed", None, None

    # Fetch and save lyrics
    lyrics_status = "none"
    lyrics_content = None
    
    # 1. Check staged lyrics first
    staged_key = f"{re.sub(r'[^\w]', '', artist_clean).lower()}_{re.sub(r'[^\w]', '', title_clean).lower()}"
    cache_file = Path("/data/staged_lyrics.json")
    
    def _read_staged_lyrics():
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    staged = json.load(f)
                    return staged.get(staged_key)
            except Exception:
                pass
        return None

    try:
        staged_entry = await asyncio.to_thread(_read_staged_lyrics)
        if staged_entry and staged_entry.get("lyrics"):
            lyrics_content = staged_entry["lyrics"]
            lyrics_status = staged_entry.get("status", "synced")
            logger.info(f"Using staged {lyrics_status} lyrics for '{artist_clean} - {title_clean}'")
    except Exception as e:
        logger.warning(f"Failed reading staged lyrics: {e}")

    # 2. If no staged lyrics, fetch from LRCLIB
    if not lyrics_content and lrclib_client:
        logger.info(f"Searching lyrics for '{artist_clean} - {title_clean}' on LRCLIB...")
        try:
            search_art = extract_main_artist(artist_clean)
            lyrics_text, l_type = await lrclib_client.get_lyrics(search_art, title_clean, dz_album)
            if not lyrics_text and search_art != artist_clean:
                lyrics_text, l_type = await lrclib_client.get_lyrics(artist_clean, title_clean, dz_album)
                
            if lyrics_text and l_type != "missing":
                lyrics_content = lyrics_text
                lyrics_status = l_type
                logger.info(f"Found {lyrics_status} lyrics for '{artist_clean} - {title_clean}'")
            else:
                logger.info(f"No lyrics found on LRCLIB for '{artist_clean} - {title_clean}'")
                lyrics_status = "missing"
        except Exception as lyrics_err:
            logger.warning(f"Could not retrieve lyrics for '{artist_clean} - {title_clean}': {lyrics_err}")
            lyrics_status = "missing"

    if lyrics_content:
        def _write_lyrics():
            dest_lyrics_path = dest_audio_path.with_suffix(".lrc")
            with open(dest_lyrics_path, "w", encoding="utf-8") as lf:
                lf.write(lyrics_content)
        try:
            await asyncio.to_thread(_write_lyrics)
            logger.info(f"Saved {lyrics_status} lyrics sidecar for '{artist_clean} - {title_clean}'")
        except Exception as file_err:
            logger.error(f"Failed to write lyrics file: {file_err}")

    # Embed all metadata together
    await asyncio.to_thread(
        embed_metadata,
        file_path=str(dest_audio_path),
        artist=dz_artist,
        title=dz_title,
        album=dz_album,
        track_num=track_num,
        track_total=track_total,
        cover_bytes=cover_bytes,
        lyrics_text=lyrics_content,
        album_artist=dz_album_artist,
        date=dz_date,
        disc_num=disc_num,
        disc_total=disc_total,
        is_explore=is_explore,
        mbid_album=mbid_album,
        mbid_recording=mbid_recording,
        credited_artists=meta_result.get("credited_artists")
    )

    return "downloaded", str(dest_audio_path), lyrics_status

def wildcard_artist(artist: str) -> str:
    """Implement Explo's wildcard artist logic."""
    artist = artist.strip()
    prefix = ""
    if len(artist) >= 4 and artist[:4].lower() == "the ":
        prefix = artist[:4]
        artist = artist[4:].strip()
    
    if len(artist) < 3:
        return artist
        
    return prefix + "*" + artist[1:]


def wildcard_artist_variants(artist: str) -> list[str]:
    """Return progressively broader artist spellings for Soulseek queries."""
    clean = re.sub(r"\s+", " ", artist or "").strip()
    if not clean:
        return []

    words = clean.split()
    variants = [clean]
    if len(clean) >= 3:
        variants.append(wildcard_artist(clean))

    # Some peers index multi-word artist folders inconsistently. Wildcarding
    # each word separately catches those folders without making the first
    # query unnecessarily broad.
    if len(words) > 1:
        for index, word in enumerate(words):
            if len(word) >= 3:
                wildcard_words = words.copy()
                wildcard_words[index] = "*" + word[1:]
                variants.append(" ".join(wildcard_words))
        if any(len(word) >= 3 for word in words):
            variants.append(" ".join("*" + word[1:] if len(word) >= 3 else word for word in words))

    return list(dict.fromkeys(variants))


def wildcard_text_variants(text: str) -> list[str]:
    """Expand title words so blocked/exact tokens can still be searched."""
    clean = re.sub(r"\s+", " ", re.sub(r"[^\w\s*]", " ", text or "")).strip()
    if not clean:
        return []

    words = clean.split()
    variants = [clean]
    for index, word in enumerate(words):
        if len(word) >= 3 and word[0].isalpha():
            wildcard_words = words.copy()
            wildcard_words[index] = "*" + word[1:]
            variants.append(" ".join(wildcard_words))
    if len(words) > 1 and any(len(word) >= 3 and word[0].isalpha() for word in words):
        variants.append(" ".join("*" + word[1:] if len(word) >= 3 and word[0].isalpha() else word for word in words))
    return list(dict.fromkeys(variants))


def build_album_wildcard_queries(artist: str, album: str) -> list[str]:
    """Build concise title+artist wildcard queries for album-directory searches, starting with full wildcard."""
    clean_album = re.sub(r"\s+", " ", re.sub(r"[^\w\s*]", " ", album or "")).strip()
    clean_artist = re.sub(r"\s+", " ", re.sub(r"[^\w\s*]", " ", extract_main_artist(artist) or "")).strip()
    artist_variants = wildcard_artist_variants(clean_artist)[1:]
    album_variants = wildcard_text_variants(clean_album)[1:]
    full_wild_album = album_variants[-1] if album_variants else clean_album
    full_wild_artist = artist_variants[-1] if artist_variants else clean_artist

    queries: list[str] = []

    def add(q: str) -> None:
        normalized = re.sub(r"\s+", " ", q).strip()
        if normalized and normalized not in queries:
            queries.append(normalized)

    # 1. Full wildcard album + full wildcard artist (e.g. "*ULLY *ELUXE *anye *est" or "*urple *ain *rince")
    if full_wild_album and full_wild_artist:
        add(f"{full_wild_album} {full_wild_artist}")

    # 2. Clean album + full wildcard artist (e.g. "BULLY DELUXE *anye *est" or "Purple Rain *rince")
    if clean_album and full_wild_artist:
        add(f"{clean_album} {full_wild_artist}")

    # 3. Full wildcard album + clean artist (e.g. "*ULLY *ELUXE Kanye West")
    if full_wild_album and clean_artist:
        add(f"{full_wild_album} {clean_artist}")

    return queries


def build_track_search_queries(artist: str, title: str, album: str = "") -> list[str]:
    """Build concise slskd queries without '-' exclusion syntax, starting with full wildcard."""
    def query_part(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s*]", " ", value or "")).strip()

    clean_title = clean_search_title(title)
    main_artist = extract_main_artist(artist)
    clean_artist = re.sub(r"[\(\[].*?[\)\]]", "", artist or "").strip()
    clean_artist = re.sub(r"\$", "S", clean_artist)
    clean_album = re.sub(r"[\(\[].*?[\)\]]", "", album or "").strip()

    lead_artist = ""
    backing_match = re.search(r"(?i)^(.+?)\s+(?:and|&)\s+the\s+", clean_artist or "")
    if backing_match:
        lead_artist = backing_match.group(1).strip()

    title_variants = wildcard_text_variants(clean_title)
    artist_variants = wildcard_artist_variants(main_artist)[1:]
    lead_variants = (
        wildcard_artist_variants(lead_artist)[1:]
        if lead_artist and lead_artist != main_artist
        else []
    )

    full_wild_title = title_variants[-1] if len(title_variants) > 1 else (title_variants[0] if title_variants else clean_title)
    full_wild_main = artist_variants[-1] if artist_variants else main_artist
    full_wild_lead = lead_variants[-1] if lead_variants else lead_artist

    queries: list[str] = []
    queries_to_add: list[str] = []

    # 1. Full wildcard title + full wildcard artist (lead artist first for backing bands, then main artist)
    if lead_artist and lead_artist != main_artist and full_wild_lead:
        queries_to_add.append(query_part(f"{full_wild_title} {full_wild_lead}"))
    if full_wild_main:
        queries_to_add.append(query_part(f"{full_wild_title} {full_wild_main}"))

    # 2. Clean title + full wildcard artist (covers Soulseek artist blocks when peer requires exact title)
    if lead_artist and lead_artist != main_artist and full_wild_lead:
        queries_to_add.append(query_part(f"{clean_title} {full_wild_lead}"))
    if full_wild_main and full_wild_main != main_artist:
        queries_to_add.append(query_part(f"{clean_title} {full_wild_main}"))

    # 3. Clean title + clean artist
    if lead_artist and lead_artist not in (main_artist, clean_artist):
        queries_to_add.append(query_part(f"{clean_title} {lead_artist}"))
    queries_to_add.append(query_part(f"{clean_title} {main_artist}"))
    if "," not in clean_artist and clean_artist != main_artist:
        queries_to_add.append(query_part(f"{clean_title} {clean_artist}"))

    # 4. Clean title + clean artist + album (if distinct)
    if clean_album and clean_album.casefold() not in {
        clean_title.casefold(), clean_artist.casefold(), "unknown album", "single", "ep"
    }:
        queries_to_add.append(query_part(f"{clean_title} {main_artist} {clean_album}"))

    # 5. Clean title only fallback
    queries_to_add.append(query_part(clean_title))

    # 6. Fallback to album queries when individual track title searches yield 0 results
    # (Enables finding single tracks inside shared album folders on Soulseek)
    generic_albums = {"unknown album", "single", "singles", "ep", "album", "lp", "greatest hits", "the best of", "best of"}
    album_with_edition = query_part(re.sub(r"[^\w\s*]", " ", album or ""))
    album_base = query_part(clean_album)
    if album_with_edition and album_with_edition.casefold() not in generic_albums:
        if album_with_edition.casefold() not in {clean_title.casefold(), clean_artist.casefold()}:
            if main_artist:
                queries_to_add.append(query_part(f"{album_with_edition} {main_artist}"))
            queries_to_add.append(query_part(album_with_edition))
    if album_base and album_base.casefold() != album_with_edition.casefold() and album_base.casefold() not in generic_albums:
        if album_base.casefold() not in {clean_title.casefold(), clean_artist.casefold()}:
            if main_artist:
                queries_to_add.append(query_part(f"{album_base} {main_artist}"))
            queries_to_add.append(query_part(album_base))

    for query in queries_to_add:
        if query and query not in queries:
            queries.append(query)
    return queries

async def delete_searches_delayed(slskd_client, search_ids: list):
    """Delayed deletion of searches to prevent slskd DB concurrency errors."""
    await asyncio.sleep(15)
    for sid in search_ids:
        try:
            await slskd_client.delete_search(sid)
        except Exception as e:
            logger.warning(f"Failed to delete search {sid} during delayed cleanup: {e}")

async def process_track_skipped(db, run_id, artist, title, safe_audio_name, lyrics_status):
    await db.add_track(
        run_id=run_id,
        artist=artist,
        title=title,
        status="skipped",
        filename=safe_audio_name,
        lyrics_status=lyrics_status
    )
        
def cleanup_explore_master(playlists_dir: Path):
    """Deletes explore-only tracks that are no longer referenced by any active playlist M3U."""
    explore_dir = playlists_dir / "explore"
    if not explore_dir.exists() or not explore_dir.is_dir():
        return

    # Find all filenames currently referenced in active playlist M3U files
    active_filenames = set()
    for item in playlists_dir.iterdir():
        if item.is_dir() and item.name != "explore" and not item.name.startswith("."):
            for f in item.iterdir():
                if f.is_file() and f.suffix.lower() == ".m3u":
                    try:
                        with open(f, 'r', encoding='utf-8') as mf:
                            for line in mf:
                                line = line.strip()
                                if line and not line.startswith("#"):
                                    basename = os.path.basename(line.replace("\\", "/"))
                                    active_filenames.add(basename)
                    except Exception as e:
                        logger.warning(f"Failed to read M3U file {f}: {e}")

    for f in explore_dir.iterdir():
        if f.is_file() and f.suffix.lower() in [".mp3", ".flac", ".m4a"]:
            try:
                # If the file is not referenced in any active playlist M3U, delete it from explore only.
                # We never delete from the main library — explore is a secondary location.
                if f.name not in active_filenames:
                    logger.info(f"Cleaning up unreferenced explore master track: {f.name}")
                    f.unlink()
                    explore_lrc = f.with_suffix(".lrc")
                    if explore_lrc.exists():
                        explore_lrc.unlink()
            except Exception as e:
                logger.warning(f"Failed to cleanup explore track {f.name}: {e}")


def update_m3u_references(playlists_dir: Path, explore_filename: str, library_file_path: Path):
    """
    Finds all M3U files in playlists_dir and updates references to explore_filename
    with the relative path to library_file_path.
    Also unlinks the explore_filename and its LRC sidecar from explore and any playlist folders.
    """
    import os
    explore_name_lower = explore_filename.lower()
    
    # 1. Walk playlists_dir and update M3U files
    for root, _, files in os.walk(str(playlists_dir)):
        for f in files:
            if f.lower().endswith(".m3u"):
                m3u_path = Path(root) / f
                try:
                    lines = []
                    modified = False
                    with open(m3u_path, "r", encoding="utf-8") as mf:
                        lines = mf.readlines()
                    
                    new_lines = []
                    for line in lines:
                        stripped = line.strip()
                        if stripped and not stripped.startswith("#"):
                            line_base = os.path.basename(stripped.replace("\\", "/"))
                            if line_base.lower() == explore_name_lower:
                                rel_path = os.path.relpath(str(library_file_path), str(m3u_path.parent)).replace("\\", "/")
                                new_lines.append(rel_path + "\n")
                                modified = True
                            else:
                                new_lines.append(line)
                        else:
                            new_lines.append(line)
                            
                    if modified:
                        with open(m3u_path, "w", encoding="utf-8") as mf:
                            mf.writelines(new_lines)
                        logger.info(f"Updated M3U playlist '{m3u_path.name}' to point '{explore_filename}' to library path: {library_file_path}")
                except Exception as e:
                    logger.error(f"Failed to update M3U file '{m3u_path}': {e}")

    # 2. Delete the explore file and any copies of it in playlists_dir
    explore_master = playlists_dir / "explore" / explore_filename
    try:
        if explore_master.exists():
            explore_master.unlink()
            logger.info(f"Deleted explore master file: {explore_master}")
        explore_lrc = explore_master.with_suffix(".lrc")
        if explore_lrc.exists():
            explore_lrc.unlink()
    except Exception as e:
        logger.warning(f"Failed to delete explore master file {explore_master}: {e}")

    # copies in playlist folders
    for item in playlists_dir.iterdir():
        if item.is_dir() and item.name != "explore" and not item.name.startswith("."):
            p_file = item / explore_filename
            try:
                if p_file.exists():
                    p_file.unlink()
                    logger.info(f"Deleted playlist file copy: {p_file}")
                p_lrc = p_file.with_suffix(".lrc")
                if p_lrc.exists():
                    p_lrc.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete playlist copy {p_file}: {e}")

def cleanup_album_explore_tracks(playlists_dir: Path, music_dir: Path, artist: str, album: str):
    """
    For a given artist and album, scans the library directory to find all downloaded files,
    then scans the explore/playlist directories for any files that have the same artist/title tags,
    and updates their M3U references and unlinks the explore/playlist copies.
    """
    import os, re
    from backend.app.sync import sanitize_filename, get_folder_artist_name
    from backend.app.main import read_basic_tags

    album_dir, safe_artist, safe_album = resolve_album_dir(music_dir, artist, album)
    if not album_dir.exists() or not album_dir.is_dir():
        return
        
    library_tracks = []
    try:
        for f in album_dir.iterdir():
            if f.is_file() and f.suffix.lower() in [".mp3", ".flac", ".m4a"]:
                try:
                    meta = read_basic_tags(f)
                    library_tracks.append({
                        "title": meta["title"],
                        "artist": meta["artist"],
                        "path": f
                    })
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Failed to read library tracks for cleanup check: {e}")
        return

    if not library_tracks:
        return
        
    # Scan explore/playlist dirs
    try:
        for root, _, files in os.walk(str(playlists_dir)):
            for f in files:
                if f.lower().endswith((".mp3", ".flac", ".m4a")):
                    f_path = Path(root) / f
                    try:
                        meta = read_basic_tags(f_path)
                        f_artist = meta["artist"]
                        f_title = meta["title"]
                        
                        norm_artist = re.sub(r'[^\w]', '', f_artist).lower()
                        norm_title = re.sub(r'[^\w]', '', f_title).lower()
                        
                        for lt in library_tracks:
                            lt_norm_artist = re.sub(r'[^\w]', '', lt["artist"]).lower()
                            lt_norm_title = re.sub(r'[^\w]', '', lt["title"]).lower()
                            
                            if (norm_artist in lt_norm_artist or lt_norm_artist in norm_artist) and norm_title == lt_norm_title:
                                logger.info(f"Cleaning up duplicate explore/playlist track '{f_artist} - {f_title}' since it is now in library: {lt['path']}")
                                update_m3u_references(playlists_dir, f, lt["path"])
                                break
                    except Exception as track_err:
                        logger.debug(f"Failed to check duplicate playlist track '{f}': {track_err}")
    except Exception as walk_err:
        logger.error(f"Failed to walk playlists dir for explore cleanup: {walk_err}")

def _build_library_index(music_dir: str) -> Dict[str, Any]:
    filename_index = {}
    mbid_index = {}
    music_path = Path(music_dir)
    if music_path.exists() and music_path.is_dir():
        for root, dirs, files in os.walk(music_dir):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if f.lower().endswith((".mp3", ".flac", ".m4a")):
                    full_path = Path(root) / f
                    rel_path = os.path.relpath(full_path, music_dir)
                    norm = re.sub(r'[^\w]', '', rel_path).lower()
                    filename_index[norm] = full_path
                    
                    try:
                        mbid_track, _ = extract_audio_mbids(full_path)
                        if mbid_track:
                            mbid_index[mbid_track] = full_path
                    except Exception:
                        pass

    return {"filename_index": filename_index, "mbid_index": mbid_index}

_library_index_cache: Dict[str, Any] = {"timestamp": 0.0, "music_dir": "", "data": None}
_library_index_lock = asyncio.Lock()

async def get_cached_library_index(music_dir: str, max_age_seconds: int = 60) -> Dict[str, Any]:
    """Thread-safe retrieval or building of cached in-memory library index with TTL."""
    global _library_index_cache
    now = time.time()
    if (
        _library_index_cache["data"] is not None
        and _library_index_cache["music_dir"] == music_dir
        and (now - _library_index_cache["timestamp"]) < max_age_seconds
    ):
        return _library_index_cache["data"]

    async with _library_index_lock:
        now = time.time()
        if (
            _library_index_cache["data"] is not None
            and _library_index_cache["music_dir"] == music_dir
            and (now - _library_index_cache["timestamp"]) < max_age_seconds
        ):
            return _library_index_cache["data"]

        data = await asyncio.to_thread(_build_library_index, music_dir)
        _library_index_cache = {
            "timestamp": time.time(),
            "music_dir": music_dir,
            "data": data,
        }
        return data

def invalidate_library_index_cache():
    """Invalidate the cached library index so it refreshes on the next lookup."""
    global _library_index_cache
    _library_index_cache = {"timestamp": 0.0, "music_dir": "", "data": None}

async def enrich_library_index_mbids(user_id: str, db: "Database") -> None:
    """Background task: look up MusicBrainz for library_index rows missing track_mbid.

    Runs entirely in the background after the disk scan completes.
    Respects the MusicBrainz 1-req/sec rate limit via the shared _rate_lock.
    Never raises — errors are logged and skipped.
    """
    from backend.app.clients.musicbrainz import musicbrainz_client
    try:
        rows = await db.get_library_rows_missing_mbid(user_id)
    except Exception as e:
        logger.error(f"MBID enrichment: failed to fetch rows for user {user_id}: {e}")
        return

    logger.info(f"⚡ [MBID Enrichment] Queued {len(rows)} tracks missing MusicBrainz IDs for background lookup...")
    enriched = 0
    total_to_check = len(rows)
    for idx, row in enumerate(rows, 1):
        try:
            rec = await musicbrainz_client.search_recording(
                row["artist"], row["title"], row.get("album") or ""
            )
            if rec:
                track_mbid = rec.get("id")
                releases = rec.get("releases") or []
                album_mbid = releases[0].get("id") if releases else None
                if track_mbid:
                    await db.mark_track_mbid(row["filepath"], track_mbid, album_mbid)
                    enriched += 1
                    logger.info(f"✨ [MBID Enrichment] ({idx}/{total_to_check}) Matched '{row['artist']} - {row['title']}' -> Track MBID: {track_mbid}")
                else:
                    logger.debug(f"ℹ️ [MBID Enrichment] ({idx}/{total_to_check}) No MBID match for '{row['artist']} - {row['title']}'")
        except Exception as e:
            logger.debug(f"⚠️ [MBID Enrichment] ({idx}/{total_to_check}) Error looking up {row.get('filepath')}: {e}")

    logger.info(f"✅ [MBID Enrichment Summary] Finished background enrichment: {enriched}/{total_to_check} tracks updated with MusicBrainz IDs.")


def restore_previous_playlist(output_path: Path, interrupted_id: str) -> None:
    """Recover an interrupted promotion without discarding either generation."""
    backup = output_path.with_name(f".{output_path.name}.previous")
    if not backup.exists():
        return
    if output_path.exists():
        interrupted = output_path.with_name(f".{output_path.name}.interrupted_{interrupted_id}")
        os.replace(output_path, interrupted)
    os.replace(backup, output_path)


def recover_playlist_backups(roots: List[Path]) -> None:
    """Restore any previous generation left by a process crash on startup."""
    from uuid import uuid4
    for root in roots:
        if not root.is_dir():
            continue
        for backup in root.glob(".*.previous"):
            source = backup.name[1:-len(".previous")]
            if backup.is_dir() and re.fullmatch(r"[A-Za-z0-9_-]+", source):
                restore_previous_playlist(root / source, uuid4().hex[:8])


def promote_playlist_staging(staging_path: Path, output_path: Path) -> Optional[Path]:
    """Swap directories on one volume, retaining the previous one for rollback."""
    if not staging_path.is_dir() or staging_path.resolve().parent != output_path.resolve().parent:
        raise ValueError("Staging must be a directory beside the playlist output")
    backup = output_path.with_name(f".{output_path.name}.previous")
    if backup.exists():
        raise RuntimeError(f"Unrecovered playlist backup: {backup}")
    if output_path.exists():
        if not output_path.is_dir():
            raise ValueError("Playlist output is not a directory")
        preserved = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".nfo", ".txt"}
        for item in output_path.iterdir():
            if item.is_file() and item.suffix.lower() in preserved and not (staging_path / item.name).exists():
                shutil.copy2(item, staging_path / item.name)
        os.replace(output_path, backup)
    try:
        os.replace(staging_path, output_path)
    except Exception:
        if backup.exists():
            os.replace(backup, output_path)
        raise
    return backup if backup.exists() else None


async def save_playlist_artwork_and_m3u(
    playlists_dir: str,
    music_dir: str,
    playlist_source: str,
    playlist_name: Optional[str] = None,
    artwork_url: Optional[str] = None,
    tracks: Optional[List[Dict[str, Any]]] = None,
    library_index: Optional[Dict[str, Any]] = None,
    staging_dir: Optional[str] = None,
) -> Path:
    """Save playlist M3U with #PLAYLIST: and #EXTALBUMARTURL: headers, plus sidecar cover art."""
    playlist_output_dir = os.path.join(playlists_dir, playlist_source)
    output_path = Path(playlist_output_dir)
    await asyncio.to_thread(output_path.mkdir, parents=True, exist_ok=True)

    # 1. Download and save sidecar artwork if artwork_url provided
    display_title = (
        playlist_name.strip()
        if playlist_name and playlist_name.strip()
        else playlist_source.replace("-", " ").title()
    )
    safe_display_title = sanitize_filename(display_title)

    if artwork_url and str(artwork_url).startswith("http"):
        try:
            from backend.app.clients.http_client import get_http_client
            client = await get_http_client()
            resp = await client.get(str(artwork_url), timeout=15)
            if resp.status_code == 200 and resp.content:
                sidecar_display = output_path / f"{safe_display_title}.jpg"
                sidecar_source = output_path / f"{playlist_source}.jpg"
                cover_path = output_path / "cover.jpg"
                folder_path = output_path / "folder.jpg"
                await asyncio.to_thread(sidecar_display.write_bytes, resp.content)
                await asyncio.to_thread(sidecar_source.write_bytes, resp.content)
                await asyncio.to_thread(cover_path.write_bytes, resp.content)
                await asyncio.to_thread(folder_path.write_bytes, resp.content)
                logger.info(f"Saved playlist artwork sidecars to {sidecar_display} and {cover_path}")
        except Exception as art_err:
            logger.warning(f"Failed to fetch/save playlist artwork for {playlist_source}: {art_err}")

    # 2. Build M3U lines with display title
    lines = ["#EXTM3U\n", f"#PLAYLIST:{display_title}\n"]
    if artwork_url:
        lines.append(f"#EXTALBUMARTURL:{artwork_url}\n")

    if tracks:
        if library_index is None:
            library_index = await asyncio.to_thread(_build_library_index, music_dir)

        explore_dir = Path(playlists_dir) / "explore"
        explore_files = []
        if explore_dir.exists():
            try:
                explore_files = [
                    f for f in explore_dir.iterdir()
                    if f.is_file() and f.suffix.lower() in [".mp3", ".flac", ".m4a"]
                ]
            except Exception:
                pass

        for tr in tracks:
            artist = tr.get("artist", "")
            title = tr.get("title", "")
            if not artist or not title:
                continue

            audio_path, _ = find_existing_track_file(
                music_dir=music_dir,
                playlist_dir=str(output_path),
                staging_dir=str(staging_dir) if staging_dir else "",
                artist=artist,
                title=title,
                library_index=library_index,
            )

            if audio_path:
                rel_track = os.path.relpath(str(audio_path), str(output_path)).replace("\\", "/")
                lines.append(f"#EXTINF:-1,{artist} - {title}\n")
                lines.append(f"{rel_track}\n")
            elif tr.get("filename"):
                exp_path = explore_dir / tr["filename"]
                if exp_path.exists():
                    rel_track = os.path.relpath(str(exp_path), str(output_path)).replace("\\", "/")
                    lines.append(f"#EXTINF:-1,{artist} - {title}\n")
                    lines.append(f"{rel_track}\n")
            elif explore_files:
                clean_art = re.sub(r'[^\w]', '', artist).lower()
                clean_t = re.sub(r'[^\w]', '', title).lower()
                matched_exp = None
                for ef in explore_files:
                    c_ef = re.sub(r'[^\w]', '', ef.name).lower()
                    if clean_t in c_ef and (clean_art in c_ef or not clean_art):
                        matched_exp = ef
                        break
                if matched_exp:
                    rel_track = os.path.relpath(str(matched_exp), str(output_path)).replace("\\", "/")
                    lines.append(f"#EXTINF:-1,{artist} - {title}\n")
                    lines.append(f"{rel_track}\n")

    m3u_path = output_path / f"{safe_display_title}.m3u"
    tmp_m3u = output_path / f".{safe_display_title}.m3u.tmp"
    try:
        with open(tmp_m3u, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.replace(tmp_m3u, m3u_path)
        try:
            os.chmod(str(m3u_path), 0o666)
        except Exception:
            pass
        # If display name differs from raw playlist_source, clean up stale source M3U
        if safe_display_title.lower() != playlist_source.lower():
            old_source_m3u = output_path / f"{playlist_source}.m3u"
            if old_source_m3u.exists():
                try:
                    old_source_m3u.unlink()
                except Exception:
                    pass
        logger.info(f"Generated playlist M3U at {m3u_path} with title '{display_title}'")
    except Exception as e:
        logger.warning(f"Could not write playlist M3U {m3u_path}: {e}")

    return m3u_path


async def run_sync(db: Database, config: AppConfig, playlist_source: Optional[str] = None, user_id: Optional[str] = None,
                   tracks_override: Optional[List[Dict[str, Any]]] = None,
                   album_override: Optional[str] = None,
                   playlist_name: Optional[str] = None,
                   artwork_url: Optional[str] = None):
    """Executes the complete synchronization run."""
    global is_syncing, current_run_id, sync_progress, current_sync_task
    if _sync_lock.locked():
        logger.warning("Synchronization is already running. Skipping trigger.")
        return False

    async with _sync_lock:
      if not playlist_source:
          raise ValueError("playlist_source must be provided to run_sync")
      if not re.fullmatch(r"[A-Za-z0-9_-]+", playlist_source):
          raise ValueError("Invalid playlist source")

      is_syncing = True
      current_sync_task = asyncio.current_task()
      sync_progress = {
          "status": "running",
          "tracks_found": 0,
          "tracks_downloaded": 0,
          "tracks_skipped": 0,
          "tracks_failed": 0,
          "started_at": datetime.utcnow().isoformat(),
          "user_id": user_id,
          "playlist_source": playlist_source
      }

      # Initialize logging variables
      run_id = await db.create_run(status="running", source=playlist_source, user_id=user_id)
      current_run_id = run_id
      app_logger.current_run_id = run_id

      # Fetch user config if user_id is provided
      lb_username = config.listenbrainz.username
      lb_token = config.listenbrainz.token
      music_dir = config.paths.music_dir
      playlists_dir = config.paths.navidrome_playlists_dir
      user_features = {}
      
      if user_id:
          user_cfg = await db.get_user_config(user_id)
          if user_cfg:
              user_features = user_cfg.get("enabled_features", {})
              if user_cfg.get("lb_username"):
                  lb_username = user_cfg["lb_username"]
              if user_cfg.get("lb_token"):
                  lb_token = user_cfg["lb_token"]
          user_row = await db.get_user_by_id(user_id)
          if user_row:
              if user_row.get("music_dir"):
                  music_dir = user_row["music_dir"]
              if user_row.get("playlist_dir"):
                  playlists_dir = user_row["playlist_dir"]

      # Create staging folder structure inside the Navidrome playlist destination
      staging_dir = None
      backup_path = None
      playlist_output_dir = os.path.join(playlists_dir, playlist_source)
      output_path = Path(playlist_output_dir)
      parent_dir = output_path.parent

      logger.info(f"Starting synchronization run #{run_id} for source '{playlist_source}'")

      try:
          # Pre-create parent directory to avoid FileNotFoundError
          try:
              await asyncio.to_thread(parent_dir.mkdir, parents=True, exist_ok=True)
              await asyncio.to_thread(restore_previous_playlist, output_path, str(run_id))
          except PermissionError as e:
              logger.error(
                  f"Sync run #{run_id} failed: cannot create playlist directory '{parent_dir}'. "
                  f"Check that the path is correctly mounted and writable by UID 1000. Error: {e}"
              )
              await db.update_run(run_id, "failed", 0, 0, 0, 0, str(e))
              return
          
          # Ensure explore dir exists and remove any .ndignore files that would hide files from Navidrome.
          try:
              explore_dir = parent_dir / "explore"
              await asyncio.to_thread(explore_dir.mkdir, parents=True, exist_ok=True)
              
              # Remove any lingering .ndignore from root playlists folder
              root_ndignore = parent_dir / ".ndignore"
              if await asyncio.to_thread(root_ndignore.exists):
                  await asyncio.to_thread(root_ndignore.unlink)
                  logger.info("Removed root .ndignore to allow Navidrome to scan playlist files.")
              
              # Remove .ndignore from explore folder so Navidrome indexes those files
              explore_ndignore = explore_dir / ".ndignore"
              if await asyncio.to_thread(explore_ndignore.exists):
                  await asyncio.to_thread(explore_ndignore.unlink)
                  logger.info("Removed explore/.ndignore so Navidrome can index explore-only tracks.")
          except Exception as e:
              logger.warning(f"Failed to configure .ndignore: {e}")

          staging_dir = parent_dir / f".staging_{playlist_source}"
          await asyncio.to_thread(staging_dir.mkdir, parents=True, exist_ok=True)
          staging_dir = str(staging_dir)
          logger.info(f"Using staging directory: {staging_dir}")

          # Initialize clients
          lb_client = ListenBrainzClient(
              username=lb_username,
              playlist_source=playlist_source,
              token=lb_token,
              timeout=config.timeouts.http_seconds
          )
          slskd_client = SlskdClient(
              base_url=config.slskd.base_url,
              api_key=config.slskd.api_key,
              timeout=config.timeouts.http_seconds
          )
          lrclib_client = LrcLibClient(
              base_url=config.lyrics.base_url,
              timeout=config.timeouts.http_seconds
          )
          deezer_client = DeezerClient(timeout=config.timeouts.http_seconds)

          # Clear old searches for this playlist before starting new ones
          await clear_old_playlist_searches(slskd_client, playlist_source)
          new_search_ids = []

          # 1. Resolve MBID and fetch playlist tracks
          if tracks_override is not None:
              tracks = tracks_override
          else:
              mbid = await lb_client.resolve_playlist_mbid()
              tracks = await lb_client.get_playlist_tracks(mbid)

          sync_progress["tracks_found"] = len(tracks)
          await db.update_run(run_id, "running", len(tracks), 0, 0, 0)

          if not tracks:
              logger.warning(f"ListenBrainz playlist '{playlist_source}' is empty. Nothing to synchronize.")
              # Still promote an empty M3U so stale tracks from the previous
              # generation do not remain visible forever.

          # 2. Build library index once to avoid O(n*m) filesystem walks
          logger.info(f"Building music library index from {music_dir}...")
          library_index = await asyncio.to_thread(_build_library_index, music_dir)
          indexed_count = len(library_index.get("filename_index", library_index)) if isinstance(library_index, dict) else len(library_index)
          logger.info(f"Library index built: {indexed_count} tracks indexed.")

          # Early playlist registration: create initial M3U + sidecar artwork and trigger early Navidrome scan
          try:
              await save_playlist_artwork_and_m3u(
                  playlists_dir=playlists_dir,
                  music_dir=music_dir,
                  playlist_source=playlist_source,
                  playlist_name=playlist_name,
                  artwork_url=artwork_url,
                  tracks=tracks,
                  library_index=library_index,
                  staging_dir=staging_dir,
              )
              if config.navidrome.url and config.navidrome.username and config.navidrome.password:
                  from backend.app.clients.navidrome import NavidromeClient
                  nd_client = NavidromeClient(
                      url=config.navidrome.url,
                      username=config.navidrome.username,
                      password=config.navidrome.password,
                  )
                  logger.info("Triggering early Navidrome scan so playlist appears immediately...")
                  await nd_client.trigger_scan()
          except Exception as early_err:
              logger.warning(f"Early playlist registration notice: {early_err}")

          # --- Sequential Download Loop ---
          results = []
          new_search_ids = []

          for idx, track in enumerate(tracks, 1):
              # Check cancellation
              if current_sync_task and current_sync_task.cancelled():
                  raise asyncio.CancelledError()

              artist = track["artist"]
              title = track["title"]
              album = track.get("album")
              track_duration_ms = track.get("duration")

              logger.info(f"--- [{idx}/{len(tracks)}] {artist} - {title} ---")

              # A. Check if track already exists
              existing_audio, existing_lyrics = find_existing_track_file(
                  music_dir, playlist_output_dir, staging_dir, artist, title, library_index
              )
              if existing_audio:
                  logger.info(f"Track '{artist} - {title}' already exists in library. Skipping download.")
                  # but do NOT copy to explore — the M3U generator will point directly
                  # to the library file via find_existing_track_file.
                  explore_name = get_safe_filename(artist, title, existing_audio.suffix)

                  lyrics_status = "none"
                  if existing_lyrics:
                      lyrics_status = "synced"

                  await process_track_skipped(db, run_id, artist, title, explore_name, lyrics_status)
                  results.append("skipped")
                  sync_progress["tracks_skipped"] = results.count("skipped")
                  await db.update_run(run_id, "running", len(tracks), results.count("downloaded"), results.count("skipped"), results.count("failed"))
                  continue


              # B. Initialize DB track record as pending
              track_db_id = await db.add_track(
                  run_id=run_id,
                  artist=artist,
                  title=title,
                  status="pending"
              )

              # Check discovery toggle
              if not user_features.get("discovery", True):
                  logger.info(f"Discovery is disabled for user. Skipping download of new track '{artist} - {title}'.")
                  await db.update_track(
                      track_db_id,
                      status="failed",
                      error_message="Discovery disabled by user features"
                  )
                  results.append("failed")
                  sync_progress["tracks_failed"] = results.count("failed")
                  await db.update_run(run_id, "running", len(tracks), results.count("downloaded"), results.count("skipped"), results.count("failed"))
                  continue

              # C. Build search queries without slskd's '-' exclusion syntax.
              clean_title = clean_search_title(title)
              search_queries = build_track_search_queries(artist, title, album)

              # D. Search: only try next query if previous returned ZERO matching candidates
              grabbed_candidate = None
              audio_quality_dict = config.slskd.audio_quality.model_dump() if hasattr(config.slskd.audio_quality, "model_dump") else dict(config.slskd.audio_quality)

              valid_candidates = []
              for strategy_idx, query in enumerate(search_queries):
                  logger.info(f"Search strategy {strategy_idx+1}/{len(search_queries)} for '{artist} - {title}': '{query}'")
                  try:
                      candidates, search_id = await slskd_client.search_candidates(
                          artist=artist,
                          title=clean_title,
                          query=query,
                          audio_quality=audio_quality_dict,
                          album=album,
                          search_timeout=config.timeouts.search_seconds
                      )
                      if search_id:
                          new_search_ids.append(search_id)

                      # Apply duration filter
                      valid_candidates = []
                      for c in candidates:
                          if track_duration_ms and c.get("duration"):
                              if abs((track_duration_ms / 1000) - c["duration"]) > 15:
                                  continue
                          valid_candidates.append(c)

                      if valid_candidates:
                          # Candidates found — stop trying other queries
                          break

                  except Exception as e:
                      logger.error(f"Search strategy {strategy_idx+1} failed: {e}")

              if not valid_candidates:
                  logger.error(f"No candidates found for '{artist} - {title}'")
                  await db.update_track(track_db_id, status="failed", error_reason="No candidates found")
                  results.append("failed")
                  sync_progress["tracks_failed"] = results.count("failed")
                  await db.update_run(run_id, "running", len(tracks), results.count("downloaded"), results.count("skipped"), results.count("failed"))
                  continue

              # Candidates found → attempt download, retrying up to max_candidate_attempts if they fail or get stuck
              failed_usernames = set()
              grabbed_candidate = None
              final_state = "unknown"
              
              max_attempts = min(len(valid_candidates), config.schedule.max_candidate_attempts)
              candidate_idx = 0
              attempt_count = 0

              while attempt_count < max_attempts and candidate_idx < len(valid_candidates):
                  candidate = valid_candidates[candidate_idx]
                  candidate_idx += 1
                  if candidate["username"] in failed_usernames:
                      continue

                  attempt_count += 1
                  logger.info(f"Grab attempt {attempt_count}/{max_attempts} ({candidate['username']}) for '{artist} - {title}'")
                  
                  if not await slskd_client.request_download(candidate["username"], candidate["filename"], candidate["size"]):
                      logger.warning(f"Peer '{candidate['username']}' refused download")
                      failed_usernames.add(candidate["username"])
                      continue

                  grabbed_candidate = candidate
                  logger.info(f"Download queued for '{artist} - {title}'. Waiting for transfer to complete...")

                  # E. Wait for this single download to complete
                  dl_start = datetime.utcnow()
                  dl_timeout = config.timeouts.download_seconds
                  not_found_count = 0
                  final_state = "unknown"
                  final_file_id = None
                  
                  last_bytes = 0
                  last_progress_time = datetime.utcnow()

                  while True:
                      if current_sync_task and current_sync_task.cancelled():
                          try:
                              _, fid = await slskd_client.get_download_status(
                                  grabbed_candidate["username"], grabbed_candidate["filename"], grabbed_candidate["size"]
                              )
                              if fid:
                                  await slskd_client.delete_download(grabbed_candidate["username"], fid)
                          except Exception:
                              pass
                          raise asyncio.CancelledError()

                      if (datetime.utcnow() - dl_start).total_seconds() > dl_timeout:
                          logger.warning(f"Download of '{artist} - {title}' timed out after {dl_timeout}s")
                          try:
                              _, fid = await slskd_client.get_download_status(
                                  grabbed_candidate["username"], grabbed_candidate["filename"], grabbed_candidate["size"]
                              )
                              if fid:
                                  await slskd_client.delete_download(grabbed_candidate["username"], fid)
                          except Exception:
                              pass
                          final_state = "timeout"
                          break

                      try:
                          downloads = await slskd_client.get_all_downloads()
                      except Exception as e:
                          logger.warning(f"Failed to fetch active transfers: {e}")
                          await asyncio.sleep(5)
                          continue

                      found_in_queue = False
                      for user_dl in downloads:
                          if user_dl.get("username") != grabbed_candidate["username"]:
                              continue
                          for directory in user_dl.get("directories", []):
                              for file_info in directory.get("files", []):
                                  actual_size = file_info.get("size", 0)
                                  target_size = grabbed_candidate["size"]
                                  size_ok = True
                                  if target_size > 0 and actual_size > 0:
                                      size_ok = abs(actual_size - target_size) / target_size < 0.05
                                      
                                  if file_info.get("filename") == grabbed_candidate["filename"] and size_ok:
                                      found_in_queue = True
                                      state = file_info.get("state", "").lower()
                                      final_file_id = file_info.get("id")
                                      if "succeeded" in state:
                                          final_state = "succeeded"
                                      elif any(x in state for x in ["error", "abort", "cancel", "fail", "time"]):
                                          final_state = "xfer_failed"
                                      else:
                                          final_state = "pending"
                                          # Stuck/No progress check:
                                          bytes_tx = file_info.get("bytesTransferred", 0)
                                          if bytes_tx > last_bytes:
                                              last_bytes = bytes_tx
                                              last_progress_time = datetime.utcnow()
                                          elif (datetime.utcnow() - last_progress_time).total_seconds() > 90:
                                              logger.warning(f"Download for '{artist} - {title}' is stuck in queue or has no progress for 90s. Skipping.")
                                              final_state = "stuck"

                      if final_state == "succeeded":
                          logger.info(f"Transfer complete for '{artist} - {title}'. Verifying AcoustID...")
                          downloaded_file = find_downloaded_file(config.slskd.downloads_dir, grabbed_candidate["filename"], grabbed_candidate["size"])
                          
                          if downloaded_file:
                              from backend.app.clients.acoustid import acoustid_client
                              expected_mbid = await lb_client.resolve_recording_mbid(artist, title)
                              if expected_mbid:
                                  is_valid = await acoustid_client.verify_track(
                                      downloaded_file, expected_mbid, expected_artist=artist, expected_title=title
                                  )
                                  if not is_valid:
                                      logger.warning(f"AcoustID verification failed for '{artist} - {title}'. Discarding.")
                                      final_state = "acoustid_mismatch"
                                      if final_file_id:
                                          try:
                                              await slskd_client.delete_download(grabbed_candidate["username"], final_file_id)
                                          except Exception:
                                              pass
                                      downloaded_file.unlink(missing_ok=True)
                              else:
                                  logger.info(f"No MusicBrainz recording MBID found for '{artist} - {title}'. Skipping AcoustID fingerprint check.")
                          if final_state == "succeeded":
                              break
                      elif final_state in ["xfer_failed", "stuck"]:
                          logger.warning(f"Transfer failed or stuck in slskd for '{artist} - {title}'")
                          if final_file_id:
                              try:
                                  await slskd_client.delete_download(grabbed_candidate["username"], final_file_id)
                              except Exception:
                                  pass
                          break

                      if not found_in_queue:
                          not_found_count += 1
                          if not_found_count >= 5:
                              logger.error(f"'{artist} - {title}' disappeared from slskd queue")
                              final_state = "missing"
                              break
                      else:
                          not_found_count = 0

                      await asyncio.sleep(5)

                  if final_state == "succeeded":
                      break
                  else:
                      logger.warning(f"Attempt with peer '{grabbed_candidate['username']}' failed with state: {final_state}. Retrying with another candidate...")
                      failed_usernames.add(grabbed_candidate["username"])
                      grabbed_candidate = None

              if not grabbed_candidate:
                  logger.error(f"All download attempts failed for '{artist} - {title}'")
                  await db.update_track(track_db_id, status="failed", error_reason="All download attempts failed or timed out")
                  results.append("failed")
                  sync_progress["tracks_failed"] = results.count("failed")
                  await db.update_run(run_id, "running", len(tracks), results.count("downloaded"), results.count("skipped"), results.count("failed"))
                  continue

              # F. Post-download processing
              track_result = "failed"
              if final_state == "succeeded":
                  explore_dir = Path(playlists_dir) / "explore"
                  explore_dir.mkdir(parents=True, exist_ok=True)
                  try:
                      dl_status, lib_filepath, l_status = await relocate_and_tag_download(
                          lrclib_client=lrclib_client,
                          deezer_client=deezer_client,
                          remote_filename=grabbed_candidate["filename"],
                          size=grabbed_candidate["size"],
                          artist=artist,
                          title=title,
                          downloads_dir=config.slskd.downloads_dir,
                          music_dir=music_dir,
                          dest_dir=str(explore_dir),
                          album_override=album_override
                      )
                  except Exception as err:
                      logger.error(f"Post-processing failed for '{artist} - {title}': {err}")
                      dl_status = "failed"
                      lib_filepath = None
                      l_status = None

                  if dl_status == "downloaded" and lib_filepath:
                      lib_file = Path(lib_filepath)
                      # The file was placed in explore_dir by relocate_and_tag_download.
                      # Its flat filename is used for M3U and cleanup_explore_master.
                      explore_name = lib_file.name

                      await db.update_track(
                          track_db_id,
                          status="downloaded",
                          filename=explore_name,
                          lyrics_status=l_status,
                          bitrate=grabbed_candidate.get("bitrate", 320),
                          size=grabbed_candidate["size"]
                      )
                      if l_status == "instrumental":
                          await db.mark_instrumental(str(lib_file), user_id)
                      track_result = "downloaded"

                  else:
                      await db.update_track(track_db_id, status="failed", error_reason="Post-download processing failed")
              else:
                  await db.update_track(track_db_id, status="failed", error_reason=f"Transfer ended: {final_state}")

              if final_file_id:
                  try:
                      await slskd_client.delete_download(grabbed_candidate["username"], final_file_id)
                  except Exception as e:
                      logger.warning(f"Failed to clean slskd queue: {e}")

              results.append(track_result)
              d_count = results.count("downloaded")
              s_count = results.count("skipped")
              f_count = results.count("failed")
              sync_progress["tracks_downloaded"] = d_count
              sync_progress["tracks_skipped"] = s_count
              sync_progress["tracks_failed"] = f_count
              await db.update_run(run_id, "running", len(tracks), d_count, s_count, f_count)

          # Save search query IDs so they can be deleted in the next run
          add_playlist_search_ids(playlist_source, new_search_ids)

          # 4. Sum up final statistics
          downloaded = results.count("downloaded")
          skipped = results.count("skipped")
          failed = results.count("failed")

          logger.info(f"Sync complete. Downloaded: {downloaded}, Skipped: {skipped}, Failed: {failed}")

          # 5. Promotion to final output path
          expected_parent = Path(playlists_dir).resolve()
          resolved_output = output_path.resolve()
          if not resolved_output.is_relative_to(expected_parent):
              raise ValueError(f"Output path {resolved_output} is outside allowed directory {expected_parent}. Aborting.")

          # Keep the old generation until the new one and its M3U are complete.
          staging_path = Path(staging_dir)
          backup_path = await asyncio.to_thread(promote_playlist_staging, staging_path, output_path)
          logger.info(f"Staging directory promoted to: {output_path}")

          # 6. Generate final M3U playlist with all downloaded tracks
          try:
              run_tracks = await db.get_tracks_for_run(run_id)
              m3u_tracks = []
              for db_track in sorted(run_tracks, key=lambda x: x["id"]):
                  if db_track["status"] in ["downloaded", "skipped"]:
                      m3u_tracks.append({
                          "artist": db_track["artist"],
                          "title": db_track["title"],
                          "filename": db_track.get("filename"),
                      })
              if not m3u_tracks and tracks:
                  m3u_tracks = tracks

              await save_playlist_artwork_and_m3u(
                  playlists_dir=playlists_dir,
                  music_dir=music_dir,
                  playlist_source=playlist_source,
                  playlist_name=playlist_name,
                  artwork_url=artwork_url,
                  tracks=m3u_tracks,
                  library_index=library_index,
                  staging_dir=staging_dir,
              )
          except Exception as e:
              logger.error(f"Failed to generate playlist: {e}")
              raise

          # 6. Trigger Navidrome Scan
          if config.navidrome.url and config.navidrome.username and config.navidrome.password:
              from backend.app.clients.navidrome import NavidromeClient
              nd_client = NavidromeClient(
                  url=config.navidrome.url,
                  username=config.navidrome.username,
                  password=config.navidrome.password
              )
              logger.info("Triggering Navidrome scan...")
              await nd_client.trigger_scan()

          # Promotion is committed only after playlist generation and scan.
          if backup_path and backup_path.exists():
              await asyncio.to_thread(shutil.rmtree, backup_path)
              backup_path = None

          # 7. Cleanup only after the previous generation is no longer needed.
          try:
              cleanup_explore_master(Path(playlists_dir))
          except Exception as e:
              logger.warning(f"Error cleaning up unreferenced master tracks: {e}")

          # Complete run in DB
          await db.update_run(
              run_id=run_id,
              status="completed",
              tracks_found=len(tracks),
              tracks_downloaded=downloaded,
              tracks_skipped=skipped,
              tracks_failed=failed
          )
          sync_progress["status"] = "completed"

      except Exception as e:
          import traceback
          logger.error(f"Sync run #{run_id} failed with error: {repr(e)}\n{traceback.format_exc()}")
          await db.update_run(
              run_id=run_id,
              status="failed",
              tracks_found=sync_progress["tracks_found"],
              tracks_downloaded=sync_progress["tracks_downloaded"],
              tracks_skipped=sync_progress["tracks_skipped"],
              tracks_failed=sync_progress["tracks_failed"],
              error_message=str(e) or repr(e)
          )
          sync_progress["status"] = "failed"

          # Preserve staging directory if downloads were partial
          if staging_dir and os.path.exists(staging_dir):
              downloaded_count = sync_progress.get("tracks_downloaded", 0)
              if downloaded_count == 0:
                  try:
                      shutil.rmtree(staging_dir)
                  except Exception:
                      pass
              else:
                  logger.warning(f"Staging dir preserved at {staging_dir} — {downloaded_count} tracks already downloaded.")
      finally:
          if backup_path and backup_path.exists():
              try:
                  await asyncio.to_thread(restore_previous_playlist, output_path, str(run_id))
                  logger.warning("Restored previous playlist after interrupted sync; new files kept in an interrupted directory.")
              except Exception as restore_error:
                  logger.error(f"Could not restore previous playlist {output_path}: {restore_error}")
          is_syncing = False
          app_logger.current_run_id = None
          current_run_id = None
          current_sync_task = None
          try:
              from backend.app.main import _cached_playlists
              _cached_playlists.clear()
              logger.info("Cleared ListenBrainz playlist cache.")
          except Exception as e:
              logger.debug(f"Failed to clear playlist cache: {e}")
      return sync_progress["status"] == "completed"
