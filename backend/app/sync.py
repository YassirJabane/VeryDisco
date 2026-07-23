import os
import re
import shutil
import tempfile
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
    """
    Safely moves or copies a file across filesystem boundaries (handling Docker mounts / sendfile Errno 5).
    """
    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(src_path), str(dst_path))
    except Exception as e:
        logger.warning(f"Standard shutil.move failed ({e}) for {src_path} -> {dst_path}, using chunked fallback copy...")
        with open(src_path, "rb") as fsrc:
            with open(dst_path, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
        try:
            os.remove(src_path)
        except Exception:
            pass

def safe_copy_file(src: Any, dst: Any):
    """
    Safely copies a file across filesystem boundaries (handling Docker mounts / sendfile Errno 5).
    """
    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(str(src_path), str(dst_path))
    except Exception as e:
        logger.warning(f"Standard shutil.copy2 failed ({e}) for {src_path} -> {dst_path}, using chunked fallback copy...")
        with open(src_path, "rb") as fsrc:
            with open(dst_path, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)

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

# Regex for matching trailing features like (feat. ...), [ft. ...], etc.
FEAT_TAIL_RE = re.compile(r'(?i)\s*[\(\[\{]\s*(feat\.?|featuring|ft\.?|with)\s[^\)\]\}]*[\)\]\}]\s*$')
REMASTER_TAIL_RE = re.compile(r'(?i)\s*[-–—]\s*\d{4}\s*remaster(ed)?\s*$')

def clean_search_title(title: str) -> str:
    title = FEAT_TAIL_RE.sub('', title)
    title = REMASTER_TAIL_RE.sub('', title)
    return title.strip()


def get_file_audio_info(file_path: Path) -> Tuple[str, int, int, int]:
    """Get file extension, bitrate (kbps), bit_depth, and sample_rate (Hz) using mutagen."""
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
    config: Optional[Any] = None
) -> int:
    """
    Scans all audio files in dir_path, updates their embedded ID3/FLAC metadata 
    (Artist, Album Artist, Album) to match folder structure / target params, 
    and triggers a Navidrome scan.
    """
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
        return 0

    updated_count = 0
    folder_album = target_album or path.name
    folder_artist = target_artist or path.parent.name

    for f_path in path.rglob("*"):
        if f_path.is_file() and f_path.suffix.lower() in [".mp3", ".flac", ".m4a"]:
            try:
                from backend.app.main import read_basic_tags
                meta = read_basic_tags(f_path)
                curr_title = meta.get("title") or f_path.stem

                embed_metadata(
                    file_path=str(f_path),
                    artist=folder_artist,
                    title=curr_title,
                    album=folder_album,
                    album_artist=folder_artist,
                    track_num=meta.get("track_num")
                )
                updated_count += 1
                logger.info(f"Re-tagged embedded metadata for '{f_path.name}' -> Artist: '{folder_artist}', Album: '{folder_album}'")
            except Exception as e:
                logger.warning(f"Failed to update tags for '{f_path}': {e}")

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

def find_existing_album_folder(artist_dir: Path, target_album: str) -> Optional[Path]:
    """
    Looks under artist_dir for an existing album directory that matches target_album.
    Matches case-insensitively, and handles variations like '(Deluxe)', '[Explicit]', etc.
    """
    if not artist_dir.exists() or not artist_dir.is_dir():
        return None
        
    def normalize_album(name: str) -> str:
        cleaned = re.sub(r'[\(\[]?\s*(?:deluxe|bonus|explicit|expanded|remastered|special|edition|version|digital).*?[\)\]]?', '', name, flags=re.IGNORECASE)
        cleaned = re.sub(r'[\(\[]?\s*(?:\b(?:feat|ft|featuring)\.?\s+.*[\)\]]?)', '', cleaned, flags=re.IGNORECASE)
        return re.sub(r'[^\w]', '', cleaned).lower()

    target_clean = get_clean_album_folder(target_album).lower()
    target_norm = normalize_album(target_album)

    try:
        children = list(artist_dir.iterdir())
    except Exception:
        return None

    # 1. Exact case-insensitive match
    for child in children:
        if child.is_dir():
            if child.name.lower() == target_clean:
                return child

    # 2. Normalized match (e.g. UTOPIA vs UTOPIA (Deluxe))
    if target_norm:
        for child in children:
            if child.is_dir():
                child_norm = normalize_album(child.name)
                if child_norm and child_norm == target_norm:
                    return child

    return None

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
    mbid_recording: Optional[str] = None
):
    """Embed metadata, cover art, and lyrics directly into the audio file metadata."""
    ext = os.path.splitext(file_path)[1].lower()
    
    if is_explore:
        final_album = "Explore Tracks"
        final_album_artist = "Various Artists"
        compilation_val = "1"
    else:
        final_album = album or f"{title} - Single"
        final_album_artist = album_artist or artist
        compilation_val = "0"

    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, USLT, APIC, TALB, TRCK, TPE1, TPE2, TIT2, TDRC, TDOR, TYER, TPOS, TCMP, TXXX, UFID
            try:
                tags = ID3(file_path)
            except Exception:
                tags = ID3()
            
            # Remove all MusicBrainz and UFID tags
            for key in list(tags.keys()):
                if "musicbrainz" in key.lower() or key.lower().startswith("ufid"):
                    tags.delall(key)
            
            tags.setall("TPE1", [TPE1(encoding=3, text=artist)])
            tags.setall("TPE2", [TPE2(encoding=3, text=final_album_artist)])
            tags.setall("TIT2", [TIT2(encoding=3, text=title)])
            tags.setall("TALB", [TALB(encoding=3, text=final_album)])
            tags.setall("TCMP", [TCMP(encoding=3, text=compilation_val)])
            tags.delall("COMM")
            
            if is_explore:
                # Keep strictly essential frames for explore tracks. Purge all TXXX, COMM, TDRC, TDOR, TYER, UFID etc.
                allowed = {"TPE1", "TPE2", "TIT2", "TALB", "TCMP", "TPOS", "TRCK", "APIC", "USLT"}
                for key in list(tags.keys()):
                    frame_id = key.split(":")[0]
                    if frame_id not in allowed:
                        tags.delall(key)
                tags.setall("TPE1", [TPE1(encoding=3, text=artist)])
                tags.setall("TPE2", [TPE2(encoding=3, text=final_album_artist)])
                tags.setall("TIT2", [TIT2(encoding=3, text=title)])
                tags.setall("TALB", [TALB(encoding=3, text=final_album)])
                tags.setall("TCMP", [TCMP(encoding=3, text=compilation_val)])
                tags.setall("TPOS", [TPOS(encoding=3, text="1/1")])
            else:
                disc_str = f"{disc_num}/{disc_total}" if disc_total else str(disc_num)
                tags.setall("TPOS", [TPOS(encoding=3, text=disc_str)])
                if date:
                    tags.setall("TDRC", [TDRC(encoding=3, text=date)])
                    tags.setall("TDOR", [TDOR(encoding=3, text=date)])
                    if len(date) >= 4:
                        tags.setall("TYER", [TYER(encoding=3, text=date[:4])])
                if mbid_album:
                    tags.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=[mbid_album]))
                if mbid_recording:
                    tags.add(UFID(owner="http://musicbrainz.org", data=mbid_recording.encode('utf-8')))

            if track_num:
                trck_str = f"{track_num}/{track_total}" if track_total and not is_explore else str(track_num)
                tags.setall("TRCK", [TRCK(encoding=3, text=trck_str)])

            if lyrics_text:
                tags.setall("USLT", [USLT(encoding=3, lang='eng', desc='Lyrics', text=lyrics_text)])
            if cover_bytes:
                tags.setall("APIC", [APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_bytes)])
                
            tags.save(file_path, v2_version=3)
            
        elif ext in [".flac", ".ogg"]:
            from mutagen.flac import FLAC, Picture
            audio = FLAC(file_path)
            
            if is_explore:
                allowed_flac = {"artist", "albumartist", "album artist", "album", "title", "compilation", "discnumber", "disctotal", "tracknumber", "lyrics"}
                for key in list(audio.keys()):
                    if key.lower() not in allowed_flac:
                        del audio[key]
                audio["artist"] = artist
                audio["albumartist"] = final_album_artist
                audio["album artist"] = final_album_artist
                audio["album"] = final_album
                audio["title"] = title
                audio["compilation"] = compilation_val
                audio["discnumber"] = "1"
                audio["disctotal"] = "1"
            else:
                for key in list(audio.keys()):
                    if "musicbrainz" in key.lower():
                        del audio[key]
                audio["artist"] = artist
                audio["albumartist"] = final_album_artist
                audio["album artist"] = final_album_artist
                audio["album"] = final_album
                audio["title"] = title
                audio["compilation"] = compilation_val
                audio["discnumber"] = str(disc_num)
                audio["disctotal"] = str(disc_total)
                if date:
                    audio["date"] = date
                    if len(date) >= 4:
                        audio["year"] = date[:4]
                if mbid_album:
                    audio["musicbrainz_albumid"] = mbid_album
                if mbid_recording:
                    audio["musicbrainz_trackid"] = mbid_recording

            if track_num:
                audio["tracknumber"] = str(track_num)
            if track_total and not is_explore:
                audio["tracktotal"] = str(track_total)

            if lyrics_text:
                audio["lyrics"] = lyrics_text
            if cover_bytes:
                pic = Picture()
                pic.type = 3
                pic.mime = "image/jpeg"
                pic.desc = "Cover"
                pic.data = cover_bytes
                audio.add_picture(pic)
            audio.save()
            
        elif ext in [".m4a", ".mp4"]:
            from mutagen.mp4 import MP4, MP4Cover
            audio = MP4(file_path)
            
            if is_explore:
                allowed_m4a = {"\xa9ART", "aART", "\xa9nam", "\xa9alb", "cpil", "disk", "trkn", "\xa9lyr", "covr"}
                for key in list(audio.keys()):
                    if key not in allowed_m4a:
                        del audio[key]
                audio["\xa9ART"] = artist
                audio["aART"] = final_album_artist
                audio["\xa9nam"] = title
                audio["\xa9alb"] = final_album
                audio["cpil"] = True
                audio["disk"] = [(1, 1)]
            else:
                for key in list(audio.keys()):
                    if "musicbrainz" in key.lower():
                        del audio[key]
                audio["\xa9ART"] = artist
                audio["aART"] = final_album_artist
                audio["\xa9nam"] = title
                audio["\xa9alb"] = final_album
                audio["cpil"] = False
                audio["disk"] = [(disc_num, disc_total)]
                if date:
                    audio["\xa9day"] = date
                if mbid_album:
                    audio["----:com.apple.iTunes:MusicBrainz Album Id"] = mbid_album.encode('utf-8')
                if mbid_recording:
                    audio["----:com.apple.iTunes:MusicBrainz Track Id"] = mbid_recording.encode('utf-8')

            if track_num:
                audio["trkn"] = [(int(track_num), int(track_total or 0) if not is_explore else 0)]
            if lyrics_text:
                audio["\xa9lyr"] = lyrics_text
            if cover_bytes:
                audio["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()

        # Touch file modification time so Navidrome's file watcher immediately notices changes
        try:
            os.utime(file_path, None)
        except Exception:
            pass

        logger.info(f"Embedded metadata and cover art into: {file_path}")
    except Exception as e:
        logger.error(f"Failed to embed metadata into {file_path}: {e}")
            
        logger.info(f"Embedded metadata and cover art into: {file_path}")
    except Exception as e:
        logger.error(f"Failed to embed metadata into {file_path}: {e}")

def extract_main_artist(artist_name: str) -> str:
    """
    Extracts the primary main artist from a joint or featured artist string.
    Handles 'ASAP Rocky and Tim Burton', 'A$AP Rocky x Tim Burton', 'A$AP Rocky & Tim Burton',
    'A$AP Rocky feat. Tim Burton', etc.
    """
    if not artist_name:
        return ""
    # Normalize $ -> S (e.g. A$AP -> ASAP, Ke$ha -> Kesha)
    art = re.sub(r'\$', 'S', artist_name)
    # Remove parenthetical extras
    art = re.sub(r'[\(\[].*?[\)\]]', '', art).strip()
    # Split on feature/joint separators
    parts = re.split(r'(?i)\b(?:feat\.?|ft\.?|featuring|and|with)\b|\s+&\s+|\s+[xX]\s+|,|/', art)
    main = parts[0].strip() if parts else art
    return main or art

def get_artist_aliases(artist_name: str) -> list[str]:
    """Get list of normalized aliases and individual artist segments to handle joint/featured artists."""
    if not artist_name:
        return []

    aliases = set()

    # 1. Convert $ to s / S so A$AP becomes ASAP
    normalized_art = re.sub(r'\$', 's', artist_name, flags=re.IGNORECASE)
    parts = re.split(r'(?i)\b(?:feat\.?|ft\.?|featuring|and|with)\b|\s+&\s+|\s+[xX]\s+|,|/', normalized_art)
    for p in parts:
        clean_p = re.sub(r'[^\w]', '', p).lower().strip()
        if clean_p:
            aliases.add(clean_p)
            words = [w for w in re.findall(r'\w+', p.lower()) if len(w) > 2]
            for w in words:
                aliases.add(w)

    # 2. Also keep raw parts without $ conversion (e.g. A$AP -> aap)
    raw_parts = re.split(r'(?i)\b(?:feat\.?|ft\.?|featuring|and|with)\b|\s+&\s+|\s+[xX]\s+|,|/', artist_name)
    for rp in raw_parts:
        clean_rp = re.sub(r'[^\w]', '', rp).lower().strip()
        if clean_rp:
            aliases.add(clean_rp)

    # Special artist aliases
    if any(k in aliases for k in ["kanyewest", "ye", "kanye"]):
        aliases.update(["kanyewest", "ye", "kanye"])
    if any(a in aliases for a in ["asaprocky", "aaprocky", "asap"]):
        aliases.update(["asaprocky", "aaprocky", "asap", "rocky"])

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
    """Strips audio extensions, artist prefixes, track/disc numbers, and audio format tags."""
    base = os.path.splitext(filename)[0] if any(filename.lower().endswith(ext) for ext in ['.mp3', '.flac', '.m4a', '.wav', '.ogg', '.lrc']) else filename
    if target_artist:
        for art_alias in get_artist_aliases(target_artist):
            if art_alias:
                pattern = rf'(?i)^{re.escape(art_alias)}\s*[-_]*\s*'
                base = re.sub(pattern, '', base).strip(' -_./\\')
    base = re.sub(r'^(?:\d+[-._\s]+|\d+\.\s*|[a-z]\d+[\s._-]+|\d+_\d+[\s._-]+)', '', base, flags=re.IGNORECASE).strip()
    base = re.sub(r'(?i)\s*[\(\[](?:remastered|remaster|live|explicit|clean|audio|bonus|deluxe|version|edit|hd|hq)[\)\]]', '', base).strip()
    return base

def match_track_titles(target_title: str, candidate_filename: str, target_artist: str = "") -> bool:
    """Precision matching between target track title and candidate audio filename."""
    clean_candidate = clean_track_filename(candidate_filename, target_artist)
    
    def norm(txt: str):
        t = txt.lower().replace('_', ' ').replace('&', 'and')
        return re.sub(r'[^\w]', '', t)
        
    norm_target = norm(target_title)
    norm_candidate = norm(clean_candidate)
    
    if not norm_target or not norm_candidate:
        return False

    if norm_target == norm_candidate:
        return True

    raw_target = re.sub(r'[^\w]', '', target_title.lower().replace('_', ''))
    raw_candidate = re.sub(r'[^\w]', '', clean_candidate.lower().replace('_', ''))
    if raw_target == raw_candidate:
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
    """Search recursively for downloaded file using 4 robust strategies."""
    import time
    import re

    basename = os.path.basename(target_filename.replace("\\", "/"))
    
    downloads_path = Path(downloads_dir)
    if not downloads_path.exists():
        logger.error(f"slskd downloads directory does not exist: {downloads_dir}")
        return None

    audio_exts = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".alac"}
    now = time.time()

    candidates = []
    try:
        for path in downloads_path.rglob("*"):
            if path.is_file() and path.suffix.lower() in audio_exts:
                if any("incomplete" in part.lower() for part in path.parts):
                    continue
                candidates.append(path)
    except Exception as e:
        logger.error(f"Error scanning downloads directory {downloads_dir}: {e}")
        return None

    # Strategy 1: Exact basename + size within 15%
    for path in candidates:
        if path.name.lower() == basename.lower():
            try:
                actual_size = path.stat().st_size
                if actual_size > 0 and (target_size == 0 or abs(actual_size - target_size) / target_size < 0.15):
                    return path
            except Exception:
                pass

    # Strategy 2: Exact basename match (ignoring size variance)
    for path in candidates:
        if path.name.lower() == basename.lower():
            return path

    # Strategy 3: Cleaned basename matching (strip punctuation/accents)
    def norm_name(s: str) -> str:
        name_no_ext = os.path.splitext(s)[0]
        return re.sub(r'[^\w]', '', name_no_ext).lower()

    target_norm = norm_name(basename)
    if target_norm:
        for path in candidates:
            if norm_name(path.name) == target_norm:
                return path

    # Strategy 4: Fallback to most recently modified audio file (within last 180s)
    recent_files = []
    for path in candidates:
        try:
            mtime = path.stat().st_mtime
            if (now - mtime) <= 180:
                recent_files.append((mtime, path))
        except Exception:
            pass

    if recent_files:
        recent_files.sort(key=lambda x: x[0], reverse=True)
        return recent_files[0][1]

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
    album: Optional[str] = None
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
    dz_album = meta_result.get("album") or album or f"{title_clean} - Single"
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
        mbid_recording=mbid_recording
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

    logger.info(f"MBID enrichment: {len(rows)} tracks to look up for user {user_id}")
    enriched = 0
    for row in rows:
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
        except Exception as e:
            logger.debug(f"MBID enrichment: skipping {row.get('filepath')}: {e}")

    logger.info(f"MBID enrichment complete: {enriched}/{len(rows)} tracks enriched for user {user_id}")

async def run_sync(db: Database, config: AppConfig, playlist_source: Optional[str] = None, user_id: Optional[str] = None):
    """Executes the complete synchronization run."""
    global is_syncing, current_run_id, sync_progress, current_sync_task
    if _sync_lock.locked():
        logger.warning("Synchronization is already running. Skipping trigger.")
        return

    async with _sync_lock:
      if not playlist_source:
          raise ValueError("playlist_source must be provided to run_sync")

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
      playlist_output_dir = os.path.join(playlists_dir, playlist_source)
      output_path = Path(playlist_output_dir)
      parent_dir = output_path.parent

      logger.info(f"Starting synchronization run #{run_id} for source '{playlist_source}'")

      try:
          # Pre-create parent directory to avoid FileNotFoundError
          try:
              parent_dir.mkdir(parents=True, exist_ok=True)
          except PermissionError as e:
              logger.error(
                  f"Sync run #{run_id} failed: cannot create playlist directory '{parent_dir}'. "
                  f"Check that the path is correctly mounted and writable by UID 1000. Error: {e}"
              )
              await db.update_run(run_id, "failed", 0, 0, 0, 0, str(e))
              return
          
          # Ensure explore dir exists and remove any .ndignore files that would hide files from Navidrome.
          # Previously we put a .ndignore in explore/ to prevent duplicates when files were also copied
          # into per-playlist folders. Now that we no longer copy files, the explore/ files are the
          # only copy for explore-only tracks and must be visible to Navidrome.
          try:
              explore_dir = parent_dir / "explore"
              explore_dir.mkdir(parents=True, exist_ok=True)
              
              # Remove any lingering .ndignore from root playlists folder
              root_ndignore = parent_dir / ".ndignore"
              if root_ndignore.exists():
                  root_ndignore.unlink()
                  logger.info("Removed root .ndignore to allow Navidrome to scan playlist files.")
              
              # Remove .ndignore from explore folder so Navidrome indexes those files
              explore_ndignore = explore_dir / ".ndignore"
              if explore_ndignore.exists():
                  explore_ndignore.unlink()
                  logger.info("Removed explore/.ndignore so Navidrome can index explore-only tracks.")
          except Exception as e:
              logger.warning(f"Failed to configure .ndignore: {e}")

          staging_dir = parent_dir / f".staging_{playlist_source}"
          staging_dir.mkdir(parents=True, exist_ok=True)
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
          mbid = await lb_client.resolve_playlist_mbid()
          tracks = await lb_client.get_playlist_tracks(mbid)

          sync_progress["tracks_found"] = len(tracks)
          await db.update_run(run_id, "running", len(tracks), 0, 0, 0)

          if not tracks:
              logger.warning(f"ListenBrainz playlist '{playlist_source}' is empty. Nothing to synchronize.")
              await db.update_run(run_id, "completed", 0, 0, 0, 0)
              sync_progress["status"] = "completed"
              return

          # 2. Build library index once to avoid O(n*m) filesystem walks
          logger.info(f"Building music library index from {music_dir}...")
          library_index = await asyncio.to_thread(_build_library_index, music_dir)
          logger.info(f"Library index built: {len(library_index)} tracks indexed.")

          # --- Sequential Download Loop ---
          # Each track is fully resolved (search → grab → wait for download → process)
          # before moving to the next one. Fallback search queries are ONLY attempted
          # when the previous query returned ZERO matching candidates (even on timeout).
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

              # A. Check if track already exists (O(1) index check, no slskd requests needed)
              existing_audio, existing_lyrics = find_existing_track_file(
                  music_dir, playlist_output_dir, staging_dir, artist, title, library_index
              )
              if existing_audio:
                  logger.info(f"Track '{artist} - {title}' already exists in library. Skipping download.")
                  # Store the safe filename in DB so M3U fallback works if needed,
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

              # C. Build search queries (de-duplicated)
              clean_title = clean_search_title(title)
              main_artist = extract_main_artist(artist)
              clean_artist = re.sub(r'[\(\[].*?[\)\]]', '', artist).strip()
              clean_artist = re.sub(r'\$', 'S', clean_artist)

              query_main = re.sub(r'\s+', ' ', re.sub(r'[^\w\s-]', ' ', f"{clean_title} - {main_artist}")).strip()
              query_full = re.sub(r'\s+', ' ', re.sub(r'[^\w\s-]', ' ', f"{clean_title} - {clean_artist}")).strip()
              w_artist = wildcard_artist(main_artist)
              query_wildcard = re.sub(r'\s+', ' ', re.sub(r'[^\w\s\*-]', ' ', f"{clean_title} - {w_artist}")).strip()

              search_queries = []
              for q in [query_main, query_full, query_wildcard]:
                  if q and q not in search_queries:
                      search_queries.append(q)

              # D. Search: only try next query if previous returned ZERO matching candidates
              grabbed_candidate = None
              audio_quality_dict = config.slskd.audio_quality.model_dump() if hasattr(config.slskd.audio_quality, "model_dump") else dict(config.slskd.audio_quality)

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
                                  is_valid = await acoustid_client.verify_track(downloaded_file, expected_mbid)
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
                          dest_dir=str(explore_dir)
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
          if not str(resolved_output).startswith(str(expected_parent)):
              raise ValueError(f"Output path {resolved_output} is outside allowed directory {expected_parent}. Aborting.")

          PRESERVE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".nfo", ".txt"}

          if output_path.exists() and output_path.is_dir():
              # Delete only non-preserved files and subdirectories.
              # Files matching PRESERVE_EXTENSIONS (artwork etc.) are left untouched in-place.
              for item in list(output_path.iterdir()):
                  if item.is_dir():
                      shutil.rmtree(item)
                  elif item.is_file() and item.suffix.lower() not in PRESERVE_EXTENSIONS:
                      item.unlink()
                  else:
                      logger.info(f"Preserving user file across sync: {item.name}")
          else:
              output_path.mkdir(parents=True, exist_ok=True)

          # Move all staging files into the (now cleaned) output directory
          staging_path = Path(staging_dir)
          for item in list(staging_path.iterdir()):
              dest = output_path / item.name
              if dest.exists():
                  if dest.is_dir():
                      shutil.rmtree(dest)
                  else:
                      dest.unlink()
              shutil.move(str(item), str(dest))
          staging_path.rmdir()
          logger.info(f"Staging directory promoted to: {output_path}")

          # 6. Generate M3U playlist
          m3u_path = output_path / f"{playlist_source}.m3u"
          try:
              library_index = await asyncio.to_thread(_build_library_index, music_dir)

              if m3u_path.exists():
                  try:
                      m3u_path.unlink()
                  except Exception as e:
                      logger.warning(f"Could not unlink existing M3U file {m3u_path} before writing: {e}")

              with open(m3u_path, 'w', encoding='utf-8') as f:
                  f.write("#EXTM3U\n")
                  run_tracks = await db.get_tracks_for_run(run_id)
                  for db_track in sorted(run_tracks, key=lambda x: x["id"]):
                      if db_track["status"] in ["downloaded", "skipped"]:
                          audio_path, _ = find_existing_track_file(
                              music_dir=music_dir,
                              playlist_dir=str(output_path),
                              staging_dir=str(staging_dir),
                              artist=db_track["artist"],
                              title=db_track["title"],
                              library_index=library_index
                          )

                          if audio_path:
                              # Use relative path from the playlist directory to the library audio file
                              # to ensure Navidrome can resolve it correctly.
                              track_path = os.path.relpath(str(audio_path), str(output_path)).replace("\\", "/")
                          else:
                              # Explore tracks: relative path within the same playlists volume.
                              explore_file = Path(playlists_dir) / "explore" / db_track["filename"]
                              track_path = os.path.relpath(str(explore_file), str(output_path)).replace("\\", "/")

                          f.write(f"#EXTINF:-1,{db_track['artist']} - {db_track['title']}\n")
                          f.write(f"{track_path}\n")

              try:
                  os.chmod(str(m3u_path), 0o666)
              except Exception as e:
                  logger.debug(f"Could not set permissions on {m3u_path}: {e}")

              logger.info(f"Generated playlist file at {m3u_path}")
          except Exception as e:
              logger.error(f"Failed to generate playlist: {e}")

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

          # 7. Cleanup unreferenced master tracks
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
