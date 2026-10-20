import os
import asyncio
import shutil
import urllib.parse
import httpx
import re
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path
from backend.app.logger import get_logger
from backend.app.clients.slskd import SlskdClient
from backend.app.clients.lrclib import LrcLibClient
from backend.app.sync import sanitize_filename, find_downloaded_file, embed_metadata
from backend.app.clients.deezer import DeezerClient
from backend.app.config import AppConfig
from backend.app.artist_identity import artist_alias_scope, search_queries_with_aliases

logger = get_logger()


def safe_move_file(src: Any, dst: Any):
    from backend.app.sync import safe_move_file as atomic_move
    return atomic_move(src, dst)

def safe_copy_file(src: Any, dst: Any):
    from backend.app.sync import safe_copy_file as atomic_copy
    return atomic_copy(src, dst)


async def _publish_tagged_track(source: Path, destination: Path, metadata: dict, lyrics: Optional[str], keep_source: bool = False, replace_existing: bool = True) -> None:
    """Keep the previous library file and raw transfer until tagging succeeds."""
    import uuid
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_name(f".verydisco-download-{uuid.uuid4().hex}{destination.suffix}")
    staged_lyrics = staged.with_suffix(".lrc")

    async def finish_io(function, *args, **kwargs):
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    try:
        await finish_io(safe_copy_file, source, staged)
        await finish_io(embed_metadata, file_path=str(staged), **metadata)
        if lyrics:
            staged_lyrics.write_text(lyrics, encoding="utf-8")
        if replace_existing:
            await finish_io(safe_move_file, staged, destination)
        else:
            # Same-directory hardlink publication is atomic and fails if another
            # writer won the race. Never replace an existing album track.
            await finish_io(os.link, staged, destination)
        if staged_lyrics.exists():
            try:
                await finish_io(safe_move_file if replace_existing else os.link, staged_lyrics, destination.with_suffix(".lrc"))
            except OSError as error:
                logger.warning("Audio saved, but lyrics sidecar could not be published for %s: %s", destination, error)
        if not keep_source and source.resolve() != destination.resolve():
            try:
                source.unlink(missing_ok=True)
            except OSError as error:
                logger.warning("Audio saved; raw transfer retained at %s: %s", source, error)
    finally:
        staged.unlink(missing_ok=True)
        staged_lyrics.unlink(missing_ok=True)


async def fetch_track_metadata_with_fallback(
    deezer_client: "DeezerClient",
    artist: str,
    title: str,
    album: str = "",
    release_mbid: str = "",
) -> Dict[str, Any]:
    """
    Fetch track metadata from MusicBrainz, and cover art from Deezer (cover_xl) / Cover Art Archive.

    Returns a unified dict:
      - 'title'         : canonical title (MB)
      - 'artist'        : full artist string (with feat.) (MB)
      - 'album_artist'  : primary artist only (MB)
      - 'date'          : release date string (YYYY or YYYY-MM-DD) (MB)
      - 'track_num'     : track position (int or None) (MB)
      - 'cover_bytes'   : image bytes from Deezer cover_xl or CAA
      - 'source'        : 'musicbrainz' | 'none'
    """
    result = {
        "title": title,
        "artist": artist,
        "credited_artists": [],
        "album_artist": artist,
        "album_artists": [artist] if artist else [],
        "album": album or title,
        "date": None,
        "track_num": None,
        "track_total": None,
        "disc_num": 1,
        "disc_total": 1,
        "mbid_album": None,
        "mbid_recording": None,
        "cover_bytes": None,
        "source": "none",
        "metadata_extras": {},
    }

    # --- Try MusicBrainz for metadata ---
    try:
        from backend.app.clients.musicbrainz import musicbrainz_client
        mb = await musicbrainz_client.get_track_metadata(artist, title, album, **({"release_mbid": release_mbid} if release_mbid else {}))
        if mb:
            result.update({
                "title": mb.get("title", title),
                "artist": mb.get("artist", artist),
                "credited_artists": mb.get("credited_artists") or [],
                "album_artist": mb.get("album_artist", artist),
                "album_artists": mb.get("album_artists") or [mb.get("album_artist", artist)],
                "album": mb.get("album") or result["album"],
                "date": mb.get("date"),
                "track_num": mb.get("track_num"),
                "track_total": mb.get("track_total"),
                "disc_num": mb.get("disc_num", 1),
                "disc_total": mb.get("disc_total", 1),
                "mbid_album": mb.get("release_mbid"),
                "mbid_recording": mb.get("recording_id"),
                "source": "musicbrainz",
            })
    except Exception as e:
        logger.debug(f"MusicBrainz metadata lookup failed for '{artist} - {title}': {e}")

    try:
        result["metadata_extras"] = await musicbrainz_client.get_metadata_extras(
            result.get("mbid_recording") or "", result.get("mbid_album") or ""
        )
        if not result["metadata_extras"].get("genre") and album:
            candidate = await deezer_client.search_album(artist, album)
            if candidate and candidate.get("id"):
                details = await deezer_client.get_album_metadata(candidate["id"])
                genres = [g["name"] for g in ((details or {}).get("genres") or {}).get("data", []) if g.get("name")]
                if genres:
                    result["metadata_extras"]["genre"] = genres
    except Exception as e:
        logger.debug(f"Optional metadata lookup failed for '{artist} - {title}': {e}")

    # A release-specific MBID is stronger evidence than a text search on Deezer.
    target_album = result["album"] or album or title
    target_artist = result["artist"] or artist

    if result.get("mbid_album"):
        try:
            from backend.app.clients.musicbrainz import musicbrainz_client
            result["cover_bytes"] = await musicbrainz_client.get_cover_art(result["mbid_album"])
        except Exception as e:
            logger.debug(f"MusicBrainz release cover lookup failed: {e}")

    # Fall back only to a candidate matching the requested album.
    if not result["cover_bytes"]:
        try:
            cover = await deezer_client.get_album_cover(target_artist, target_album)
            if cover:
                result["cover_bytes"] = cover
        except Exception as e:
            logger.debug(f"Deezer album cover lookup failed for '{target_artist} - {target_album}': {e}")

    return result


def clean_album_name(album: str) -> str:
    s = re.sub(r'[\(\[].*?[\)\]]', '', album)
    s = re.sub(r'(?i)\b(deluxe|remastered|special|expanded|edition|single|ep|lp|bonus|tracks|version)\b', '', s)
    s = re.sub(r'\s+-\s+', ' ', s)
    s = re.sub(r'[^\w\s-]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def clean_artist_name(artist: str) -> str:
    s = re.sub(r'\$', 'S', artist)
    s = re.sub(r'[\(\[].*?[\)\]]', '', s)
    s = re.sub(r'[^\w\s-]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def check_artist_match(artist: str, parent_dir: str, filename: str) -> bool:
    from backend.app.sync import extract_main_artist, get_artist_aliases
    norm_aliases = get_artist_aliases(artist)
    norm_parent = re.sub(r'[^\w]', '', parent_dir).lower()
    norm_filename = re.sub(r'[^\w]', '', filename).lower()
    return any(alias in norm_parent or alias in norm_filename for alias in norm_aliases)

def check_remix_mismatch(album_title: str, folder_path: str) -> bool:
    """
    Returns True if the folder_path contains remix/screwed/boxset keywords that
    are NOT in the requested album_title, indicating a mismatch.
    """
    keywords = [
        "chopped", "screwed", "remix", "slowed", "reverb", "tribute", "cover",
        "instrumental", "karaoke", "acapella", "acappella", "slopped",
        "mnesia", "boxset", "box set", "discography", "anthology", "rarities",
        "outtakes", "demos", "sessions", "unreleased", "greatest hits"
    ]
    folder_name = folder_path.replace("\\", "/").split("/")[-1].lower()
    album_lower = album_title.lower()
    from backend.app.media_versions import same_master
    if not same_master(album_title, folder_name):
        return True
    
    for kw in keywords:
        if kw in folder_name and kw not in album_lower:
            return True
    return False

def check_album_match(album_title: str, folder_path: str) -> bool:
    """
    Verify that the folder name is a close match for the requested album_title.
    This prevents matching "Culture II" when the user requested "Culture".
    """
    folder_name = folder_path.replace("\\", "/").split("/")[-1].lower()
    
    # 1. Strip 4-digit years (e.g. 2026, 1999) and scene quality tags ([320kbps], [FLAC], etc.)
    clean_folder = re.sub(r'[\(\[]?\b(?:19|20)\d{2}\b[\)\]]?', '', folder_name)
    clean_folder = re.sub(r'[\(\[]?\s*(?:320kbps|320|flac|mp3|web|cd|v0|v2|lossless|pmedia|pmedia1)\b[\)\]]?', '', clean_folder, flags=re.IGNORECASE)
    clean_folder = clean_folder.strip(' -_./\\')
    clean_folder = re.sub(r'\s+', ' ', clean_folder)
    
    # 2. Tokenize and check for roman numerals or version mismatches
    roman_numerals = ["ii", "iii", "iv", "v", "vi"]
    numeric_digits = ["2", "3", "4", "5", "6"]
    
    for roman in roman_numerals:
        if re.search(r'\b' + roman + r'\b', clean_folder) and not re.search(r'\b' + roman + r'\b', album_title.lower()):
            return False
            
    for num in numeric_digits:
        if re.search(r'\b' + num + r'\b', clean_folder) and not re.search(r'\b' + num + r'\b', album_title.lower()):
            return False
            
    # Extract core words from requested album title (ignoring stop words)
    stop_words = {"a", "an", "the", "and", "or", "of", "in", "on", "for", "with", "to", "by"}
    album_words = [w for w in re.findall(r'\w+', album_title.lower()) if len(w) > 1 and w not in stop_words]

    if not album_words:
        return True
        
    for word in album_words:
        # Optional edition keywords are evaluated in directory scoring, not as hard match failures
        if word in ["deluxe", "edition", "version", "remastered", "expanded", "bonus", "special"]:
            continue
        if not re.search(r'\b' + re.escape(word) + r'\b', clean_folder):
            return False
            
    return True
            
def extract_track_num_from_filename(filename: str) -> Optional[int]:
    """
    Extracts track position integer from filenames like:
    '01 - KING.mp3' -> 1
    '02 - THIS A MUST.mp3' -> 2
    'Artist_Album_03_Title.mp3' -> 3
    '15. BEAUTY AND THE BEAST.flac' -> 15
    """
    if not filename:
        return None
    stem = Path(filename).stem
    
    # 1. Check Artist_Album_01_Title format
    parts = stem.split('_')
    if len(parts) >= 4:
        for p in parts[2:]:
            if p.isdigit() and 1 <= int(p) <= 999:
                return int(p)

    # 2. Check leading digits like '01 - ', '01.', '01_', '01 '
    match = re.match(r'^(\d{1,3})\s*[\-_.]?\s*', stem)
    if match:
        num = int(match.group(1))
        if 1 <= num <= 999:
            return num

    # 3. Check trailing/middle track pattern like 'Track 01' or 't01'
    match = re.search(r'\b(?:track|t)[\s.\-_]*(\d{1,3})\b', stem, flags=re.IGNORECASE)
    if match:
        num = int(match.group(1))
        if 1 <= num <= 999:
            return num

    return None

def clean_track_title(basename: str, artist: str, album: str) -> str:
    """
    Extract a clean track title from a filename by stripping out
    artist names, album names, track numbers, and extra delimiters.
    """
    from backend.app.sync import clean_search_title

    title = basename
    if title.lower().endswith((".mp3", ".flac", ".m4a", ".wav", ".ogg", ".opus", ".aac", ".alac")):
        title = os.path.splitext(title)[0]

    # Split by underscore to handle Artist_Album_TrackNum_Title convention
    parts = title.split('_')
    if len(parts) >= 4:
        for idx, p in enumerate(parts):
            if p.isdigit() and len(p) <= 3:
                candidate = "_".join(parts[idx+1:]).strip()
                if candidate:
                    return clean_search_title(candidate) or candidate

    # Handle "Artist - Album - 01 - Title" or "Artist - 01 - Title" where a track number sits between separators
    mid_num_match = re.match(r'^.+?\s+[-_]\s*(?:\d{1,2}[-_.])?\d{1,2}\s*(?:[-_.]\s*|\s+)(.+)$', title)
    if mid_num_match:
        candidate = mid_num_match.group(1).strip()
        if clean_search_title(candidate):
            title = candidate

    # Strip leading disc/track numbers (e.g. "1-09 ", "09 - ", "09. ")
    title = re.sub(r'^\s*(?:\d{1,2}\s*[-_.]\s*)?\d{1,3}\s*(?:[-_.]|\s)+\s*', '', title).strip()

    # Strip leading artist/album prefixes only when followed by a separator (e.g. "Artist - Album - 01 - Title")
    # and only if stripping does not erase the actual track title (for eponymous title tracks like "Bully").
    prefixes = [artist, album]
    for item in (artist, album):
        for part in re.split(r'[,&-]', item or ""):
            if len(part.strip()) > 2:
                prefixes.append(part.strip())

    changed = True
    while changed:
        changed = False
        stripped_num = re.sub(r'^\s*(?:\d{1,2}\s*[-_.]\s*)?\d{1,3}\s*(?:[-_.]|\s)+\s*', '', title).strip()
        if stripped_num and stripped_num != title and clean_search_title(stripped_num):
            title = stripped_num
            changed = True
        for pref in prefixes:
            if not pref:
                continue
            m = re.match(rf'(?i)^\s*{re.escape(pref)}\s*(?:[-_]+|\s+[-_]\s*)\s*(.+)$', title)
            if m:
                remainder = m.group(1).strip()
                if clean_search_title(remainder):
                    title = remainder
                    changed = True
                    break

    title = re.sub(r'[_\-\s.]{2,}', ' ', title)

    # Strip release group scene tags & emojis (PMEDIA, 320kbps, FLAC, ⭐️, etc.)
    title = re.sub(r'(?i)[\(\[]?\b(PMEDIA|320kbps|320|FLAC|MP3|WEB|Explicit|HQ|CD|V0|V2|PMedia|PMedia1)\b[\)\]]?', '', title)
    title = re.sub(r'[⭐️⭐★✨]', '', title)
    title = title.strip(' -_./\\')

    cleaned = clean_search_title(title)
    if cleaned:
        title = cleaned

    if not title:
        title = basename
        if title.lower().endswith((".mp3", ".flac", ".m4a")):
            title = os.path.splitext(title)[0]
    return title

def filename_cleanliness_score(files: list) -> int:
    """
    Returns a bonus score (0 to 10) for clean filenames.
    Penalizes underscore-heavy filenames.
    """
    score = 10
    if not files:
        return score
    for f in files:
        name = os.path.basename(f.get("filename", "")).lower()
        # Penalize underscore-heavy names (automation tools)
        if name.count('_') >= 3:
            score -= 2
    return max(0, score)

def match_file_to_official_track(filename: str, official_tracks: list) -> Optional[dict]:
    """
    Tries to match a downloaded filename against a list of official album tracks.
    Returns the matched track dictionary or None.
    """
    if not official_tracks:
        return None
        
    filename_lower = filename.lower()
    from backend.app.media_versions import same_master
    stem = os.path.splitext(filename)[0]
    official_tracks = [t for t in official_tracks if same_master(stem, t.get("title", ""))]

    def _norm(t_str):
        s = t_str.lower()
        s = re.sub(r'\bparts?\s+i[-–—]v\b', 'parts 1 5', s)
        s = re.sub(r'\bparts?\s+vi[-–—]ix\b', 'parts 6 9', s)
        s = re.sub(r'\bparts?\s+1[-–—]5\b', 'parts 1 5', s)
        s = re.sub(r'\bparts?\s+6[-–—]9\b', 'parts 6 9', s)
        return re.sub(r'[^\w]', '', s)

    # 1. Match an explicit disc/track pair before the generic position: track 01
    # exists on multiple discs and must not silently select disc one.
    m_disc_tr = re.search(r'^(?:cd|disc)?\s*(\d{1,2})[-_\.](\d{1,2})\b', filename_lower)
    if m_disc_tr:
        try:
            d_num = int(m_disc_tr.group(1))
            t_num = int(m_disc_tr.group(2))
            for track in official_tracks:
                if (track.get("disk_number") or track.get("disc_number") or track.get("disc_num") or 1) == d_num and track.get("track_position") == t_num:
                    return track
        except Exception:
            pass
        return None

    # 2. Match by normalized title (stripping leading artist/album self-titled prefixes to prevent false positive collisions)
    # Longest title first to avoid substring collisions
    clean_filename = _norm(filename_lower)
    sorted_official = sorted(official_tracks, key=lambda t: len(t.get("title", "")), reverse=True)
    for track in sorted_official:
        t_title = track.get("title", "")
        if not t_title or len(t_title.strip()) < 2:
            continue
        clean_t_title = _norm(t_title)
        if not clean_t_title or len(clean_t_title) < 3:
            continue

        # If track title matches artist/album name (e.g. self-titled "Kids See Ghosts"),
        # strip artist/album occurrences from clean_filename to prevent matching every file in the album!
        check_target = clean_filename
        artist_name = track.get("artist") or track.get("album_artist") or ""
        album_name = track.get("album") or ""
        if artist_name and _norm(artist_name) == clean_t_title:
            check_target = check_target.replace(_norm(artist_name), "", 1)
        if album_name and _norm(album_name) == clean_t_title:
            check_target = check_target.replace(_norm(album_name), "", 1)

        if clean_t_title in check_target:
            return track

    # 3. Fallback: bare track position (only if safe and not conflicting with an unrelated title)
    m_tr_num = re.match(r'^(\d{1,3})\b', filename_lower)
    if m_tr_num:
        num = int(m_tr_num.group(1))
        matches = [track for track in official_tracks if str(track.get("track_position") or track.get("position") or track.get("track_num") or "") == str(num)]
        if len(matches) == 1:
            target_track = matches[0]
            clean_target_title = _norm(target_track.get("title", ""))
            letters_in_name = re.sub(r'[\d_\-\s\.]', '', filename_lower)
            # Only match if filename doesn't contain a distinct title from another song,
            # or if the target title is actually present
            if len(letters_in_name) <= 6 or clean_target_title in clean_filename:
                return target_track

    return None


def official_position_for_file(file_entry: dict, path: Path, official_tracks: list) -> Optional[tuple[int, int]]:
    """Prefer the pre-download match; renamed files can lose enough punctuation to rematch poorly."""
    stored = file_entry.get("_official_position")
    if stored and len(stored) == 2 and stored[1] is not None:
        return int(stored[0] or 1), int(stored[1])
    matched = match_file_to_official_track(path.name, official_tracks)
    if not matched:
        return None
    track = matched.get("track_position") or matched.get("position") or matched.get("track_num")
    if track is None:
        return None
    disc = matched.get("disk_number") or matched.get("disc_number") or matched.get("disc_num") or 1
    return int(disc), int(track)

def get_quality_priority(filename: str, bitrate: int, bit_depth: int, sample_rate: int, config) -> int:
    """Return profile list index (0 = best) if the file matches a profile, or -1 to reject."""
    ext = os.path.splitext(filename)[1].lower().strip(".")

    q_cfg = config.slskd.audio_quality
    preset = q_cfg.preset

    if preset == "lossless":
        from backend.app.config import LOSSLESS_PRESETS_DEFAULT
        active_profiles = [dict(p) for p in LOSSLESS_PRESETS_DEFAULT]
    elif preset == "storage_saver":
        from backend.app.config import STORAGE_SAVER_PRESETS_DEFAULT
        active_profiles = [dict(p) for p in STORAGE_SAVER_PRESETS_DEFAULT]
    elif preset == "custom":
        active_profiles = [
            p if isinstance(p, dict) else p.model_dump()
            for p in q_cfg.custom_profiles
        ]
    else:
        active_profiles = []  # accept everything

    if not active_profiles:
        return 0  # no filter — accept all

    for i, prof in enumerate(active_profiles):
        fmt = prof.get("format", "").lower().strip(".")
        if ext != fmt:
            continue

        min_br = prof.get("min_bitrate", 0) or 0
        max_br = prof.get("max_bitrate", 0) or 0
        req_depth = prof.get("bit_depth", 0) or 0
        req_sr = prof.get("sample_rate", 0) or 0

        # ---- bitrate check (skip if reported bitrate is 0 / unknown) ----
        if bitrate > 0:
            if min_br > 0 and bitrate < min_br:
                continue
            if max_br > 0 and bitrate > max_br:
                continue

        # ---- bit depth check (skip if reported depth is 0 / unknown) ----
        if bit_depth > 0 and req_depth > 0:
            if bit_depth != req_depth:
                continue

        # ---- sample rate check (skip if reported rate is 0 / unknown) ----
        if sample_rate > 0 and req_sr > 0:
            if sample_rate != req_sr:
                continue

        return i  # matched — lower index = higher preference

    return -1  # no profile matched

# Limit concurrent album downloads to prevent Slskd daemon CPU/network overload
album_download_semaphore = asyncio.Semaphore(1)

@artist_alias_scope
async def download_album_task(
    download_id: int,
    artist: str,
    track_title: str,
    album: str,
    config: 'AppConfig',
    db,
    force: bool = False,
    user_id: Optional[str] = None,
    chosen_username: Optional[str] = None,
    chosen_folder: Optional[str] = None,
    chosen_files: Optional[List[Dict[str, Any]]] = None,
    release_mbid: Optional[str] = None,
    record_type: Optional[str] = None,
):
    """Background task wrapper that restricts concurrent runs using a semaphore."""
    async with album_download_semaphore:
        return await _download_album_task_internal(
            download_id, artist, track_title, album, config, db, force,
            user_id, chosen_username, chosen_folder, chosen_files,
            release_mbid, record_type
        )

async def _download_album_task_internal(
    download_id: int,
    artist: str,
    track_title: str,
    album: str,
    config: 'AppConfig',
    db,
    force: bool = False,
    user_id: Optional[str] = None,
    chosen_username: Optional[str] = None,
    chosen_folder: Optional[str] = None,
    chosen_files: Optional[List[Dict[str, Any]]] = None,
    release_mbid: Optional[str] = None,
    record_type: Optional[str] = None,
):
    """Background task to search and download a full album via slskd with fallback strategies."""
    logger.info(
        f"Starting background {record_type or 'album'} download task for {artist} - {album} "
        f"(ID: {download_id}, release MBID: {release_mbid or 'automatic'}, force={force})"
    )
    
    target_album_artist = None
    target_album_artists: list[str] = []
    
    if not album:
        logger.warning(f"No album provided for {artist} - {track_title}. Aborting album download.")
        await db.update_album_download_status(download_id, "failed")
        return

    if not user_id:
        try:
            async with db.get_db() as conn:
                async with conn.execute("SELECT user_id FROM album_downloads WHERE id = ?", (download_id,)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        user_id = row["user_id"]
        except Exception:
            pass

    music_dir = config.paths.music_dir
    playlists_dir = config.paths.navidrome_playlists_dir
    active_playlists = config.listenbrainz.active_playlists or ["weekly-exploration"]
    if user_id:
        try:
            user_row = await db.get_user_by_id(user_id)
            if user_row:
                if user_row.get("music_dir"):
                    music_dir = user_row["music_dir"]
                if user_row.get("playlist_dir"):
                    playlists_dir = user_row["playlist_dir"]
            user_cfg = await db.get_user_config(user_id)
            if user_cfg and user_cfg.get("active_playlists"):
                active_playlists = user_cfg["active_playlists"]
        except Exception:
            pass

    try:
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

        # Pre-fetch official tracklist, release date (MusicBrainz), and cover art (Deezer cover_xl / MB CAA)
        official_album_tracks = []
        official_album_cover_bytes = None
        official_album_date = None
        official_mb_release_mbid = None
        # These fallbacks must also exist when every track is already local.
        mbid_album = None
        cover_bytes = None
        dz_date = None

        # 1. Fetch official album tracklist and release date from MusicBrainz
        try:
            from backend.app.clients.musicbrainz import musicbrainz_client, _is_matching_release_artist
            logger.info(f"Pre-fetching official tracklist for album '{artist} - {album}' from MusicBrainz...")
            mb_album = await musicbrainz_client.get_album_tracklist(
                artist, album, release_mbid=release_mbid
            )
            if mb_album:
                mb_aa = mb_album.get("album_artist") or ""
                mb_aas = mb_album.get("album_artists") or ([mb_aa] if mb_aa else [])
                credit_check = {"artist-credit": [{"name": a} for a in mb_aas if a]}
                if release_mbid or _is_matching_release_artist(credit_check, artist) or mb_aa.lower() == "various artists":
                    official_album_tracks = mb_album.get("tracks", [])
                    official_album_date = mb_album.get('date') or mb_album.get("release_date")
                    official_mb_release_mbid = mb_album.get("release_mbid")
                    target_album_artist = mb_aa or artist
                    target_album_artists = mb_aas or [target_album_artist]
                    logger.info(f"Fetched {len(official_album_tracks)} official tracks from MusicBrainz for '{album}' (MBID: {official_mb_release_mbid}).")
                else:
                    logger.warning(
                        f"Ignoring MusicBrainz release '{mb_album.get('title')}' by '{mb_aa}' "
                        f"because artist credit does not match requested artist '{artist}'."
                    )
        except Exception as e:
            logger.warning(f"Could not pre-fetch official album tracklist from MusicBrainz for '{artist} - {album}': {e}")

        # 1b. If MusicBrainz returned <= 1 track for a non-single album, check Deezer for full album tracklist
        if not release_mbid and (record_type or "").lower() != "single" and len(official_album_tracks) <= 1:
            try:
                dz_alb = await deezer_client.search_album(artist, album)
                if dz_alb and dz_alb.get("id") and int(dz_alb.get("nb_tracks") or 0) > max(1, len(official_album_tracks)):
                    dz_tracks_resp = await deezer_client.get_album_tracks(int(dz_alb["id"]))
                    dz_tracks_raw = (dz_tracks_resp or {}).get("data", []) if dz_tracks_resp else []
                    if len(dz_tracks_raw) > len(official_album_tracks):
                        dz_disks = {int(t.get("disk_number") or 1) for t in dz_tracks_raw}
                        nb_discs = max(dz_disks) if dz_disks else 1
                        nb_tracks = len(dz_tracks_raw)
                        dz_aa = (dz_alb.get("artist") or {}).get("name") or artist
                        official_album_tracks = [
                            {
                                "title": t.get("title") or t.get("title_short") or "",
                                "artist": (t.get("artist") or {}).get("name") or dz_aa,
                                "credited_artists": [(t.get("artist") or {}).get("name") or dz_aa],
                                "track_position": int(t.get("track_position") or idx),
                                "disk_number": int(t.get("disk_number") or 1),
                                "disc_num": int(t.get("disk_number") or 1),
                                "id": str(t.get("id") or ""),
                                "release_mbid": None,
                                "nb_tracks": nb_tracks,
                                "nb_discs": nb_discs,
                            }
                            for idx, t in enumerate(dz_tracks_raw, start=1)
                        ]
                        official_mb_release_mbid = None
                        if not target_album_artist:
                            target_album_artist = dz_aa
                            target_album_artists = [dz_aa]
                        logger.info(
                            f"Fetched {len(official_album_tracks)} official tracks from Deezer fallback for '{artist} - {album}'."
                        )
            except Exception as e:
                logger.debug(f"Deezer tracklist fallback failed for '{artist} - {album}': {e}")

        # An edition selected by MBID must never fall back to a broad slskd
        # search: that can put a different release (for example an EP or
        # mixtape) into the requested album folder.
        if release_mbid and not official_album_tracks:
            logger.error(
                f"Cannot resolve selected MusicBrainz release {release_mbid} for "
                f"'{artist} - {album}'; refusing an unscoped album search."
            )
            await db.update_album_download_status(download_id, "failed")
            return

        # The selected MusicBrainz release determines the preferred artwork.
        if official_mb_release_mbid:
            try:
                from backend.app.clients.musicbrainz import musicbrainz_client
                official_album_cover_bytes = await musicbrainz_client.get_cover_art(official_mb_release_mbid)
            except Exception as e:
                logger.debug(f"MusicBrainz cover art pre-fetch failed for '{album}': {e}")

        if not official_album_cover_bytes:
            try:
                official_album_cover_bytes = await deezer_client.get_album_cover(artist, album)
            except Exception as e:
                logger.debug(f"Deezer cover art pre-fetch failed for '{artist} - {album}': {e}")

        if chosen_username and chosen_folder and chosen_files:
            logger.info(f"Using manually chosen candidate from peer '{chosen_username}' for folder '{chosen_folder}'")
            candidates = [((chosen_username, chosen_folder), chosen_files)]
        else:
            # Build album search strategies (never use '-' hyphen separators, which act as exclusion syntax in slskd)
            from backend.app.sync import extract_main_artist, build_album_wildcard_queries, wildcard_artist_variants
            clean_art = clean_artist_name(artist)
            clean_alb = clean_album_name(album)
            main_art = extract_main_artist(artist)

            raw_alb = re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', album)).strip()
            stripped_alb = re.sub(r'(?i)\b(single|ep|lp|deluxe|remastered|version|edition)\b', '', album)
            stripped_alb = re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', stripped_alb)).strip()
            clean_alb_q = re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', clean_alb)).strip()
            main_art_q = re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', main_art)).strip()
            clean_art_q = re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', clean_art)).strip()

            queries = []
            art_lower = clean_art_q.lower()
            alb_lower = clean_alb_q.lower()

            seen_q = set()
            def add_query(q_text, req_artist):
                normalized = re.sub(r'\s+', ' ', (q_text or '').replace(' - ', ' ').replace('-', ' ')).strip()
                if normalized and normalized not in seen_q:
                    seen_q.add(normalized)
                    queries.append((normalized, req_artist))

            main_art_wildcard = f"*{main_art_q[1:]}" if len(main_art_q) >= 3 else main_art_q
            clean_art_wildcard = f"*{clean_art_q[1:]}" if len(clean_art_q) >= 3 else clean_art_q

            album_year = ""
            if official_album_date:
                m_yr = re.search(r'\b(19\d{2}|20\d{2})\b', str(official_album_date))
                if m_yr:
                    album_year = m_yr.group(1)

            if not album_year:
                m_yr = re.search(r'\b(19\d{2}|20\d{2})\b', album)
                if m_yr:
                    album_year = m_yr.group(1)

            if art_lower == alb_lower:
                for wildcard_query in reversed(wildcard_artist_variants(main_art_q)[1:]):
                    add_query(wildcard_query, False)
                add_query(clean_art_q, False)
            elif art_lower in alb_lower:
                for wildcard_query in build_album_wildcard_queries(main_art_q, raw_alb or stripped_alb or clean_alb_q):
                    add_query(wildcard_query, False)
                add_query(clean_alb_q, False)
            elif alb_lower in art_lower:
                for wildcard_query in build_album_wildcard_queries(main_art_q, raw_alb or stripped_alb or clean_alb_q):
                    add_query(wildcard_query, False)
                add_query(clean_art_q, False)
            else:
                raw_wildcards = build_album_wildcard_queries(main_art_q, raw_alb) if raw_alb else []
                stripped_wildcards = (
                    build_album_wildcard_queries(main_art_q, stripped_alb)
                    if stripped_alb and stripped_alb.lower() != raw_alb.lower()
                    else []
                )

                # 0. Full-wildcard album + full-wildcard artist FIRST (both raw with Deluxe/Edition and stripped)
                if raw_wildcards:
                    add_query(raw_wildcards[0], False)
                if stripped_wildcards:
                    add_query(stripped_wildcards[0], False)

                # 1. Remaining title+artist wildcard queries (space-separated, no '-' exclusion syntax)
                for wildcard_query in raw_wildcards[1:]:
                    add_query(wildcard_query, False)
                for wildcard_query in stripped_wildcards[1:]:
                    add_query(wildcard_query, False)

                if stripped_alb != clean_alb_q and clean_alb_q:
                    for wildcard_query in build_album_wildcard_queries(main_art_q, clean_alb_q):
                        add_query(wildcard_query, False)

                # 2. Clean album + artist (space-separated)
                if raw_alb and raw_alb.lower() != stripped_alb.lower():
                    add_query(f"{raw_alb} {main_art_q}", False)
                if stripped_alb:
                    add_query(f"{stripped_alb} {main_art_q}", False)

                # 3. Year-based directory patterns (space-separated, no '-')
                if album_year:
                    target_a = raw_alb if raw_alb else stripped_alb
                    if main_art_wildcard != main_art_q:
                        add_query(f"{target_a} {main_art_wildcard} {album_year}", False)
                    add_query(f"{target_a} {main_art_q} {album_year}", False)

                if stripped_alb != clean_alb_q and clean_alb_q:
                    add_query(f"{clean_alb_q} {main_art_q}", False)

                if main_art_q != clean_art_q and clean_art_q:
                    for wildcard_query in build_album_wildcard_queries(clean_art_q, stripped_alb or clean_alb_q):
                        add_query(wildcard_query, False)
                    add_query(f"{stripped_alb or clean_alb_q} {clean_art_q}", False)

                # 4. Alternate MusicBrainz album artist (e.g. 'Ye' when requested artist was 'Kanye West')
                if target_album_artist:
                    mb_art_q = re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', extract_main_artist(target_album_artist))).strip()
                    if mb_art_q and mb_art_q.lower() not in {main_art_q.lower(), clean_art_q.lower(), "various artists"}:
                        for wildcard_query in build_album_wildcard_queries(mb_art_q, stripped_alb or raw_alb):
                            add_query(wildcard_query, False)
                        add_query(f"{stripped_alb or raw_alb} {mb_art_q}", False)

                from backend.app.artist_identity import current_artist_aliases
                identities = current_artist_aliases()
                if identities:
                    for alias in identities.names_for(artist, search=True)[:8]:
                        add_query(f"{raw_alb or stripped_alb or clean_alb_q} {alias}", True)

                # 5. Album-only fallback
                if raw_alb and raw_alb.lower() != (stripped_alb or clean_alb_q).lower():
                    add_query(raw_alb, True)
                add_query(stripped_alb or clean_alb_q, True)

            best_dir_key = None
            best_files = []
            candidates = []

            async for query, require_artist_match in search_queries_with_aliases(artist, queries, album=album, album_search=True):
                if not query:
                    continue
                logger.info(f"Attempting album search strategy: '{query}' (require_artist_match={require_artist_match})")
                search_id = await slskd_client.create_search(query)
                if not search_id:
                    continue

                elapsed = 0
                poll_interval = 3
                search_completed = False
                min_wait_seconds = 8
                
                while elapsed < config.timeouts.search_seconds:
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval

                    try:
                        status = await slskd_client.get_search_status(search_id)
                        if status:
                            is_complete, file_count, _ = status
                            # Only accept completion if responses arrived (file_count > 0) or minimum wait time elapsed
                            if is_complete and (file_count > 0 or elapsed >= min_wait_seconds):
                                search_completed = True
                                logger.info(f"Search for '{query}' completed in {elapsed}s with {file_count} files found.")
                                break
                    except Exception as status_err:
                        logger.debug(f"Error checking search status for '{query}': {status_err}")

                if not search_completed:
                    logger.warning(f"Search for '{query}' timed out after {elapsed}s. Continuing with available results.")

                results = await slskd_client.get_search_responses(search_id)
                await slskd_client.delete_search(search_id)

                if not results:
                    continue

                directories = {}
                for res in results:
                    username = res.get("username")
                    if not username:
                        continue
                    files = res.get("files", [])
                    for f in files:
                        filename = f.get("filename")
                        if not filename:
                            continue
                        if not filename.lower().endswith((".mp3", ".flac", ".m4a")):
                            continue

                        # Extract audio parameters and check quality profiles
                        bitrate = f.get("bitrate") or f.get("bitRate") or 0
                        bit_depth = f.get("bitDepth") or f.get("bit_depth") or 0
                        sample_rate = f.get("sampleRate") or f.get("sample_rate") or 0
                        
                        priority = get_quality_priority(filename, bitrate, bit_depth, sample_rate, config)
                        if priority < 0:
                            continue
                        
                        parent_dir = "\\".join(filename.split("\\")[:-1]) if "\\" in filename else "/".join(filename.split("/")[:-1])
                        if not parent_dir:
                            continue
                        
                        if check_remix_mismatch(album, parent_dir) or not check_album_match(album, parent_dir):
                            continue
                            
                        if check_remix_mismatch(album, filename):
                            continue
                            
                        if require_artist_match:
                            if not check_artist_match(artist, parent_dir, filename):
                                continue
                            
                        key = (username, parent_dir)
                        if key not in directories:
                            directories[key] = []
                        directories[key].append({
                            "filename": filename,
                            "size": f.get("size", 0),
                            "bitrate": bitrate,
                            "priority": priority
                        })

                if not directories:
                    continue

                official_len = len(official_album_tracks) if official_album_tracks else 0

                def count_matching_official_tracks(dir_files):
                    if not official_album_tracks:
                        return len(dir_files)
                    cnt = 0
                    seen_pos = set()
                    for f_item in dir_files:
                        fn = os.path.basename(f_item["filename"].replace("\\", "/"))
                        matched_tr = match_file_to_official_track(fn, official_album_tracks)
                        if matched_tr and matched_tr.get("track_position") not in seen_pos:
                            seen_pos.add(matched_tr["track_position"])
                            cnt += 1
                    return cnt

                def directory_score(item):
                    dir_key, dir_files = item
                    folder_name = dir_key[1].replace("\\", "/").split("/")[-1].lower()
                    album_lower = album.lower()
                    
                    matches = count_matching_official_tracks(dir_files)
                    
                    # Track count distance penalty (closer to official track count is better)
                    if official_len > 0:
                        count_diff_penalty = -abs(len(dir_files) - official_len)
                    else:
                        count_diff_penalty = len(dir_files)

                    # Edition match bonus / penalty
                    edition_score = 0
                    if "remaster" in album_lower and "remaster" in folder_name:
                        edition_score += 5
                    if "deluxe" in album_lower and "deluxe" in folder_name:
                        edition_score += 15
                    elif "deluxe" in album_lower and "deluxe" not in folder_name:
                        edition_score -= 15

                    if "25" not in album_lower and ("25" in folder_name or "anniversary" in folder_name):
                        edition_score -= 10
                    if "deluxe" not in album_lower and "deluxe" in folder_name:
                        edition_score -= 5
                    if "special" not in album_lower and "special" in folder_name:
                        edition_score -= 5

                    cleanliness = filename_cleanliness_score(dir_files)
                    prio = -dir_files[0].get("priority", 999) if dir_files else -999

                    return (matches, count_diff_penalty, edition_score, cleanliness, prio)

                sorted_dirs = sorted(directories.items(), key=directory_score, reverse=True)
                candidates = sorted_dirs
                break

            if not candidates:
                logger.warning(f"No valid album directory found for '{artist} - {album}' after all fallback searches.")
                if not official_album_tracks:
                    await db.update_album_download_status(download_id, "failed")
                    return
                logger.info("Official tracklist is available; trying single-track downloads for the whole album.")

        max_attempts = getattr(config.schedule, "max_candidate_attempts", 3) or 3
        overall_downloaded = []
        overall_copied = []
        instrumental_paths: set[str] = set()
        album_complete = False

        # Upgrade check: if this is an upgraded edition (e.g. FOMO / Deluxe) of an existing
        # base album in the library, migrate any existing tracks into the upgraded album
        # and clean up the superseded album folder so they are merged without duplicates.
        try:
            from backend.app.sync import upgrade_or_merge_album_folders
            migrated_upgrades = await asyncio.to_thread(
                upgrade_or_merge_album_folders,
                music_dir=music_dir,
                artist=artist,
                target_album=album,
                official_album_tracks=official_album_tracks,
                official_album_cover_bytes=official_album_cover_bytes,
                official_mb_release_mbid=official_mb_release_mbid,
                official_album_date=official_album_date,
                playlists_dir=playlists_dir,
                target_album_artist=target_album_artist,
            )
            if migrated_upgrades:
                overall_copied.extend(migrated_upgrades)
                for f_item, dest_path in migrated_upgrades:
                    if _is_instrumental_track(f_item.get("title", ""), album):
                        instrumental_paths.add(str(dest_path))
                logger.info(
                    f"Successfully merged/upgraded {len(migrated_upgrades)} tracks from "
                    f"superseded album folder(s) into '{album}'."
                )
        except Exception as upgrade_err:
            logger.warning(f"Error checking album upgrade migration: {upgrade_err}")

        for attempt, (dir_key, best_files) in enumerate(candidates[:max_attempts]):
            best_username, best_dir_path = dir_key
            logger.info(f"--- [Attempt {attempt + 1}/{max_attempts}] Trying peer '{best_username}' for album directory '{best_dir_path}' with {len(best_files)} tracks. ---")

            from backend.app.sync import get_folder_artist_name, get_clean_album_folder, resolve_album_dir
            final_dir, safe_artist, safe_album = resolve_album_dir(music_dir, artist, album)

            to_download = []
            copied_files = []
            seen_tracks = set()

            # active_playlists is resolved from outer scope
            from backend.app.sync import find_existing_track_file, get_file_audio_info, check_quality_status

            # The resolved tracklist scopes both automatic and manually chosen
            # folders. The search-only ranking helper does not exist for manual picks.
            is_official_reliable = bool(official_album_tracks and len(official_album_tracks) >= 1)

            for f in best_files:
                filename_part = os.path.basename(f["filename"].replace("\\", "/"))
                basename = os.path.splitext(filename_part)[0]
                
                matched = match_file_to_official_track(filename_part, official_album_tracks) if official_album_tracks else None
                if matched:
                    clean_title = matched["title"]
                    f["track_num"] = matched.get("track_position")
                    f["title_tag"] = clean_title
                    f["_official_position"] = (
                        matched.get("disk_number") or matched.get("disc_number") or matched.get("disc_num") or 1,
                        matched.get("track_position") or matched.get("position") or matched.get("track_num"),
                    )
                elif is_official_reliable:
                    logger.info(f"Skipping extra non-album file in search result folder: {filename_part}")
                    continue
                else:
                    clean_title = clean_track_title(basename, artist, album)
                
                if matched and (matched.get("track_position") or matched.get("position") or matched.get("track_num")):
                    d_num = matched.get("disk_number") or matched.get("disc_number") or matched.get("disc_num") or 1
                    t_pos = matched.get("track_position") or matched.get("position") or matched.get("track_num")
                    track_key = (d_num, t_pos)
                else:
                    track_key = re.sub(r'[^\w]', '', clean_title).lower()

                if track_key in seen_tracks:
                    logger.info(f"Skipping duplicate track in search result folder: {filename_part}")
                    continue
                seen_tracks.add(track_key)

                existing_path = None
                if not force:
                    # 1. Direct check in final_dir (destination album directory) for tracks already downloaded in earlier attempts
                    if os.path.isdir(final_dir):
                        final_dir_files = [str(p) for p in Path(final_dir).rglob('*') if p.suffix.lower() in {'.mp3', '.flac', '.m4a', '.mp4', '.ogg'}]
                        for f_dest in final_dir_files:
                            fname = os.path.basename(f_dest)
                            from backend.app.sync import match_track_titles
                            if match_track_titles(clean_title, fname, artist):
                                existing_path = f_dest
                                break

                    if not existing_path:
                        # Library lookup must run even when no playlists are configured.
                        audio_path, _ = find_existing_track_file(music_dir, str(Path(playlists_dir) / 'explore'), "", artist, clean_title)
                        if audio_path:
                            existing_path = audio_path
                    if not existing_path:
                        for playlist_source in active_playlists:
                            playlist_output_dir = os.path.join(playlists_dir, playlist_source)
                            audio_path, _ = find_existing_track_file(music_dir, playlist_output_dir, "", artist, clean_title)
                            if audio_path:
                                existing_path = audio_path
                                break

                if existing_path:
                    try:
                        from backend.app.metadata_pipeline.tags import read_track
                        from backend.app.media_versions import same_master
                        local_tags = read_track(Path(existing_path))
                        local_id = local_tags.get('recording_mbid')
                        if not same_master(album, local_tags.get('album') or Path(existing_path).parent.name):
                            existing_path = None
                        elif matched and matched.get('id') and local_id and local_id != matched['id']:
                            existing_path = None
                    except Exception:
                        existing_path = None
                if existing_path:
                    ext, bitrate, bit_depth, sample_rate = get_file_audio_info(existing_path)
                    if get_quality_priority(str(existing_path), bitrate, bit_depth, sample_rate, config) >= 0:
                        logger.info(f"Skipping download of track '{artist} - {clean_title}'; using existing same/better quality file: {existing_path}")
                        
                        # If the existing file is already inside the destination album folder,
                        # there is nothing to do — skip entirely to avoid creating duplicates.
                        if Path(existing_path).resolve().is_relative_to(Path(final_dir).resolve()):
                            logger.debug(f"Existing file already in destination folder, no copy needed for '{clean_title}'")
                            copied_files.append((f, existing_path))
                            if _is_instrumental_track(clean_title, album):
                                instrumental_paths.add(str(Path(existing_path)))
                            continue
                        
                        # 1. Fetch metadata (MusicBrainz primary, Deezer fallback)
                        track_num = None
                        track_total = len(official_album_tracks) if official_album_tracks else None
                        disc_num = 1
                        disc_total = 1
                        mbid_album = None
                        mbid_recording = None
                        cover_bytes = None
                        lyrics_text = None
                        title_tag = clean_title
                        dz_date = None
                        dz_album_artist = None
                        dz_artist = artist
                        credited_artists = matched.get("credited_artists") if matched else None

                        filename_part = os.path.basename(str(existing_path))
                        matched = matched or match_file_to_official_track(filename_part, official_album_tracks)
                        official_disc_num = None
                        if matched:
                            official_disc_num = matched.get("disk_number") or matched.get("disc_num")
                            disc_num = official_disc_num or 1
                            track_num = matched.get("track_position")

                        if official_album_tracks:
                            max_disc_in_official = max((t.get("disk_number") or t.get("disc_num") or 1 for t in official_album_tracks), default=1)
                            if max_disc_in_official > disc_total:
                                disc_total = max_disc_in_official

                        try:
                            meta_result = {}
                            meta_result = await fetch_track_metadata_with_fallback(
                                deezer_client, artist, clean_title, album, release_mbid=official_mb_release_mbid or ""
                            )
                            title_tag = meta_result["title"]
                            track_num = track_num or meta_result["track_num"]
                            if not official_disc_num and meta_result.get("disc_num"):
                                disc_num = meta_result["disc_num"]
                            if meta_result.get("disc_total"):
                                disc_total = max(disc_total, meta_result["disc_total"])
                            if meta_result.get("track_total"):
                                track_total = track_total or meta_result["track_total"]
                            cover_bytes = meta_result["cover_bytes"]
                            dz_artist = meta_result["artist"]
                            credited_artists = credited_artists or meta_result.get("credited_artists")
                            dz_album_artist = meta_result["album_artist"]
                            dz_date = meta_result["date"]
                            mbid_album = meta_result.get("mbid_album")
                            mbid_recording = meta_result.get("mbid_recording")
                            if matched and matched.get("artist"):
                                dz_artist = matched["artist"]
                                mbid_recording = matched.get("id") or mbid_recording
                        except Exception as e:
                            logger.error(f"Metadata lookup failed for existing '{clean_title}': {e}")

                        ext_ext = Path(existing_path).suffix
                        from backend.app.sync import get_library_filename, resolve_album_dir
                        target_album_artist = target_album_artist or dz_album_artist or artist
                        dest_dir, safe_artist, safe_album = resolve_album_dir(
                            music_dir, target_album_artist, album, target_album_artist,
                            disc_num=disc_num, disc_total=disc_total
                        )
                        clean_filename = get_library_filename(dz_artist or artist, safe_album, track_num, title_tag, ext_ext)
                        dest_path = dest_dir / clean_filename
                        
                        try:
                            lyrics_text = None
                            l_type = "missing"
                            try:
                                lyrics_text, l_type = await lrclib_client.get_lyrics(artist, title_tag, album)
                            except Exception:
                                pass
                            existing_lrc = Path(existing_path).with_suffix(".lrc")
                            if not lyrics_text and existing_lrc.exists():
                                lyrics_text = existing_lrc.read_text(encoding="utf-8", errors="ignore")
                            await _publish_tagged_track(Path(existing_path), dest_path, dict(
                                artist=dz_artist or artist,
                                title=title_tag,
                                album=album,
                                track_num=track_num,
                                track_total=track_total,
                                cover_bytes=official_album_cover_bytes or cover_bytes,
                                lyrics_text=lyrics_text,
                                album_artist=target_album_artist,
                                date=official_album_date or dz_date,
                                disc_num=disc_num,
                                disc_total=disc_total,
                                is_explore=False,
                                mbid_album=official_mb_release_mbid or mbid_album,
                                mbid_recording=mbid_recording,
                                credited_artists=credited_artists,
                                metadata_extras={**((meta_result or {}).get("metadata_extras") or {}), **({"instrumental": ["1"]} if _is_instrumental_track(title_tag, album, l_type) else {})},
                                album_artists=target_album_artists or [target_album_artist or artist],
                            ), lyrics_text, keep_source=True)

                            # Update M3U references and clean up the old file in explore/playlists
                            from backend.app.sync import update_m3u_references
                            try:
                                if Path(playlists_dir).exists():
                                    update_m3u_references(Path(playlists_dir), Path(existing_path).name, dest_path)
                            except Exception as e:
                                logger.error(f"Failed to update M3U references on sync: {e}")

                            copied_files.append((f, dest_path))
                            if _is_instrumental_track(title_tag, album, l_type):
                                instrumental_paths.add(str(dest_path))
                        except Exception as e:
                            logger.error(f"Failed to copy and link existing track {clean_title}: {e}")
                            # Local I/O/tag errors cannot be fixed by downloading
                            # the same audio again. Preserve the original and stop.
                            raise RuntimeError(f"Could not reuse existing track '{clean_title}'; original retained") from e
                    else:
                        to_download.append(f)
                else:
                    to_download.append(f)

            # Reused tracks count even when this peer must supply extra tracks.
            # Otherwise a partial/failed transfer makes fallback request them again.
            overall_copied.extend(copied_files)
            logger.info('Album transfer plan for %s - %s: force=%s, %d local tracks reused this attempt, %d files to queue',
                artist, album, force, len(copied_files), len(to_download))
            if not to_download:
                logger.info(f"All tracks of album '{artist} - {album}' were already present in equal/better quality and copied. Album download completed instantly.")
                album_complete = True
                break

            url = f"{slskd_client.base_url}/api/v0/transfers/downloads/{urllib.parse.quote(best_username)}"
            
            # Queue downloads in throttled batches of 5
            BATCH_SIZE = 5
            queue_failed = False
            from backend.app.clients.http_client import get_http_client
            client = await get_http_client()
            for batch_start in range(0, len(to_download), BATCH_SIZE):
                batch = to_download[batch_start:batch_start + BATCH_SIZE]
                download_payload = [{"filename": f["filename"], "size": f["size"]} for f in batch]
                resp = await client.post(url, json=download_payload, headers=slskd_client._get_headers())
                if resp.status_code not in [200, 201, 202]:
                    logger.error(f"Failed to queue album download batch from '{best_username}': {resp.status_code} {resp.text}")
                    queue_failed = True
                    break
                await asyncio.sleep(0.2)
            
            if queue_failed:
                continue

            logger.info(f"Queued {len(to_download)} files for album download (throttled batches of {BATCH_SIZE}). Monitoring progress...")

            download_timeout = config.timeouts.download_seconds * max(1, len(to_download) // 2)
            elapsed = 0
            poll_interval = 5
            attempt_downloaded_files = []
            
            last_album_bytes = 0
            from datetime import datetime
            last_progress_time = datetime.utcnow()
            
            while elapsed < download_timeout:
                all_done = True
                current_album_bytes = 0
                
                try:
                    transfers = await slskd_client.get_peer_downloads(best_username)
                except Exception as e:
                    logger.warning(f"Failed to fetch transfers for peer '{best_username}': {e}")
                    transfers = []

                # Build a status lookup map for active downloads of this peer
                file_status_map = {}
                for transfer in transfers:
                    for directory in transfer.get("directories", []):
                        for file_info in directory.get("files", []):
                            file_id = file_info.get("id")
                            if file_id:
                                file_status_map[file_id] = (
                                    file_info.get("state", "").lower(),
                                    file_id,
                                    file_info.get("bytesTransferred", 0),
                                    file_info.get("size", 0),
                                    file_info.get("filename")
                                )

                for f in to_download:
                    if f.get("download_status") in ["succeeded", "failed"]:
                        continue
                        
                    fname = f["filename"]
                    req_size = f["size"]
                    
                    status_info = None
                    if f.get("file_id") and f["file_id"] in file_status_map:
                        status_info = file_status_map[f["file_id"]]
                    else:
                        for info in file_status_map.values():
                            if info[4] == fname:
                                status_info = info
                                f["file_id"] = info[1]
                                break
                                
                    if not status_info:
                        # Not found in transfers yet, treat as downloading
                        status = "downloading"
                        file_id = None
                        bytes_tx = 0
                    else:
                        matched_state, file_id, bytes_tx, actual_size, _ = status_info
                        size_ok = True
                        if req_size > 0 and actual_size > 0:
                            size_ok = abs(actual_size - req_size) / req_size < 0.05
                            
                        if not size_ok:
                            status = "failed"
                        elif "succeeded" in matched_state:
                            status = "succeeded"
                        elif any(x in matched_state for x in ["error", "abort", "cancel", "fail", "time"]):
                            logger.warning(f"Download state for '{fname}' from '{best_username}' is '{matched_state}'")
                            status = "failed"
                        else:
                            status = "downloading"
                    
                    current_album_bytes += bytes_tx
                    
                    if status == "succeeded":
                        f["download_status"] = "succeeded"
                        f["file_id"] = file_id
                        local_path = find_downloaded_file(config.slskd.downloads_dir, f["filename"], f["size"])
                        if local_path:
                            attempt_downloaded_files.append((f, local_path))
                    elif status == "failed":
                        # Retry failed downloads exactly once
                        if not f.get("retry_attempted"):
                            f["retry_attempted"] = True
                            logger.info(f"Retrying download of '{fname}' from '{best_username}' once due to failure/cancellation.")
                            if file_id:
                                try:
                                    await slskd_client.delete_download(best_username, file_id)
                                except Exception:
                                    pass
                            try:
                                async with httpx.AsyncClient(timeout=config.timeouts.http_seconds) as client:
                                    retry_payload = [{"filename": fname, "size": req_size}]
                                    await client.post(url, json=retry_payload, headers=slskd_client._get_headers())
                            except Exception as re_err:
                                logger.error(f"Failed to re-enqueue retry for '{fname}': {re_err}")
                            all_done = False
                        else:
                            # Already retried, fail permanently
                            f["download_status"] = "failed"
                            if file_id:
                                await slskd_client.delete_download(best_username, file_id)
                    else:
                        all_done = False
                        
                if all_done:
                    break
                    
                if current_album_bytes > last_album_bytes:
                    last_album_bytes = current_album_bytes
                    last_progress_time = datetime.utcnow()
                elif (datetime.utcnow() - last_progress_time).total_seconds() > 120:
                    logger.warning(f"Album download for '{artist} - {album}' is stuck (no byte progress across all files for 120s). Skipping remaining files.")
                    for f in to_download:
                        if f.get("download_status") not in ["succeeded", "failed"]:
                            f["download_status"] = "failed"
                            status_info = None
                            if f.get("file_id") and f["file_id"] in file_status_map:
                                status_info = file_status_map[f["file_id"]]
                            else:
                                for info in file_status_map.values():
                                    if info[4] == f["filename"]:
                                        status_info = info
                                        break
                            file_id = status_info[1] if status_info else None
                            if file_id:
                                await slskd_client.delete_download(best_username, file_id)
                    break
                    
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

            logger.info(f"Album download attempt finished. Successfully downloaded {len(attempt_downloaded_files)}/{len(to_download)} tracks.")
            
            if attempt_downloaded_files:
                # Sort files by parsed track number or filename so they process in correct album order
                def _sort_key(item):
                    f_entry, p = item
                    tn = extract_track_num_from_filename(p.name) or extract_track_num_from_filename(f_entry.get("filename", ""))
                    return (tn if tn is not None else 999, p.name)

                attempt_downloaded_files.sort(key=_sort_key)

                for file_idx, (f, local_path) in enumerate(attempt_downloaded_files):
                    basename = local_path.stem
                    disc_num = 1
                    disc_total = 1
                    track_total = len(official_album_tracks) if official_album_tracks else None
                    mbid_album = None
                    mbid_recording = None
                    
                    parsed_track_num = extract_track_num_from_filename(local_path.name) or extract_track_num_from_filename(f.get("filename", ""))
                    fallback_track_num = parsed_track_num or f.get("track_num") or (file_idx + 1)

                    matched = match_file_to_official_track(local_path.name, official_album_tracks)
                    if not matched and f.get("_official_position"):
                        wanted_disc, wanted_track = f["_official_position"]
                        matched = next((
                            item for item in official_album_tracks
                            if (item.get("disk_number") or item.get("disc_number") or item.get("disc_num") or 1) == wanted_disc
                            and (item.get("track_position") or item.get("position") or item.get("track_num")) == wanted_track
                        ), None)
                    official_disc_num = None
                    if matched:
                        clean_title = matched["title"]
                        track_num = matched.get("track_position") or fallback_track_num
                        official_disc_num = matched.get("disk_number") or matched.get("disc_num")
                        disc_num = official_disc_num or 1
                        title_tag = clean_title
                        f["_official_position"] = (
                            matched.get("disk_number") or matched.get("disc_number") or matched.get("disc_num") or 1,
                            matched.get("track_position") or matched.get("position") or matched.get("track_num"),
                        )
                    else:
                        clean_title = clean_track_title(basename, artist, album)
                        track_num = fallback_track_num
                        title_tag = f.get("title_tag", clean_title)

                    if official_album_tracks:
                        max_disc_in_official = max((t.get("disk_number") or t.get("disc_num") or 1 for t in official_album_tracks), default=1)
                        if max_disc_in_official > disc_total:
                            disc_total = max_disc_in_official

                    # 1. Fetch metadata (MusicBrainz primary, Deezer fallback)
                    cover_bytes = None
                    lyrics_text = None
                    dz_date = None
                    dz_album_artist = None
                    dz_artist = artist
                    mbid_recording = matched.get("id") if matched else None
                    mbid_album = official_mb_release_mbid
                    credited_artists = matched.get("credited_artists") if matched else None
                    try:
                        meta_result = {}
                        meta_result = await fetch_track_metadata_with_fallback(
                            deezer_client, artist, clean_title, album, release_mbid=official_mb_release_mbid or ""
                        )
                        if meta_result:
                            meta_title = meta_result.get("title")
                            meta_art = meta_result.get("artist") or ""

                            title_diff = False
                            if meta_title and clean_title:
                                norm_clean = re.sub(r'[^\w]', '', clean_title).lower()
                                norm_meta = re.sub(r'[^\w]', '', meta_title).lower()
                                if norm_clean not in norm_meta and norm_meta not in norm_clean:
                                    title_diff = True

                            artist_diff = False
                            if meta_art and artist:
                                norm_main = re.sub(r'[^\w]', '', artist).lower()
                                norm_dz_art = re.sub(r'[^\w]', '', meta_art).lower()
                                if norm_main not in norm_dz_art and norm_dz_art not in norm_main:
                                    artist_diff = True

                            if title_diff and artist_diff:
                                logger.warning(f"Discarded mismatched metadata result '{meta_art} - {meta_title}' for expected '{artist} - {clean_title}'")
                            else:
                                if meta_title and not title_diff:
                                    title_tag = meta_title
                                if not matched and meta_result.get("track_num"):
                                    track_num = meta_result["track_num"]
                                if not official_disc_num and meta_result.get("disc_num"):
                                    disc_num = meta_result["disc_num"]
                                if meta_result.get("disc_total"):
                                    disc_total = max(disc_total, meta_result["disc_total"])
                                cover_bytes = meta_result.get("cover_bytes")
                                dz_artist = meta_result.get("artist") or artist
                                credited_artists = credited_artists or meta_result.get("credited_artists")
                                dz_album_artist = meta_result.get("album_artist") or artist
                                dz_date = meta_result.get("date")
                                mbid_album = meta_result.get("mbid_album")
                                mbid_recording = meta_result.get("mbid_recording")
                                if matched and matched.get("artist"):
                                    dz_artist = matched["artist"]
                                    mbid_recording = matched.get("id") or mbid_recording
                    except Exception as e:
                        logger.error(f"Metadata lookup failed for '{clean_title}': {e}")

                        
                    # 2. AcoustID verification check on raw downloaded file BEFORE moving/tagging
                    from backend.app.clients.acoustid import acoustid_client, verification_status
                    acoustid_mismatch = False
                    try:
                        is_valid, reason = await acoustid_client.verify_track_against_metadata(
                            local_path,
                            expected_artist=dz_artist or artist,
                            expected_title=title_tag,
                            expected_mbid=mbid_recording,
                        )
                        if not is_valid:
                            if verification_status(is_valid, reason) != "mismatch":
                                logger.info(f"AcoustID verification skipped for '{title_tag}': {reason}")
                            else:
                                logger.warning(f"AcoustID mismatch detected for '{title_tag}': {reason}. Discarding downloaded file and queuing single-track replacement...")
                                acoustid_mismatch = True
                                if local_path.exists():
                                    local_path.unlink()
                                try:
                                    from backend.app.main import _create_tracked_task
                                    _create_tracked_task(
                                        download_single_track_task(
                                            artist=artist,
                                            title=title_tag,
                                            album=album,
                                            config=config,
                                            db=db,
                                            force=True,
                                            user_id=user_id
                                        ),
                                        task_id=f"track:{artist}:{title_tag}",
                                        task_type="track",
                                        metadata={"artist": artist, "title": title_tag, "album": album}
                                    )
                                except Exception:
                                    asyncio.create_task(download_single_track_task(
                                        artist=artist,
                                        title=title_tag,
                                        album=album,
                                        config=config,
                                        db=db,
                                        force=True,
                                        user_id=user_id
                                    ))
                    except Exception as ac_err:
                        logger.error(f"Error during AcoustID check for {local_path}: {ac_err}")

                    if acoustid_mismatch:
                        f["download_status"] = "failed"
                        continue

                    # 3. Determine clean destination filename using library convention
                    ext_ext = local_path.suffix
                    from backend.app.sync import get_library_filename, resolve_album_dir
                    target_album_artist = target_album_artist or dz_album_artist or artist
                    dest_dir, safe_artist, safe_album = resolve_album_dir(
                        music_dir, target_album_artist, album, target_album_artist,
                        disc_num=disc_num, disc_total=disc_total
                    )
                    clean_filename = get_library_filename(dz_artist or artist, safe_album, track_num, title_tag, ext_ext)
                    dest_path = dest_dir / clean_filename

                    # Stage the full metadata rebuild before replacing library media.
                    try:
                        lyrics_text, l_type = await lrclib_client.get_lyrics(artist, title_tag, album)
                        await _publish_tagged_track(local_path, dest_path, dict(
                            artist=dz_artist or artist,
                            title=title_tag,
                            album=album,
                            track_num=track_num,
                            track_total=track_total,
                            cover_bytes=official_album_cover_bytes or cover_bytes,
                            lyrics_text=lyrics_text,
                            album_artist=target_album_artist,
                            date=official_album_date or dz_date,
                            disc_num=disc_num,
                            disc_total=disc_total,
                            is_explore=False,
                            mbid_album=official_mb_release_mbid or mbid_album,
                            mbid_recording=mbid_recording,
                            credited_artists=credited_artists,
                            metadata_extras={**((meta_result or {}).get("metadata_extras") or {}), **({"instrumental": ["1"]} if _is_instrumental_track(title_tag, album, l_type) else {})},
                            album_artists=target_album_artists or [target_album_artist or artist],
                        ), lyrics_text)
                    except Exception as e:
                        logger.error(f"Could not publish tagged download {local_path}; original retained: {e}")
                        continue

                    overall_downloaded.append((f, dest_path))
                    if _is_instrumental_track(title_tag, album, l_type):
                        instrumental_paths.add(str(dest_path))

                    # 5. Delete successful download from slskd tab
                    fid = f.get("file_id")
                    if fid:
                        try:
                            await slskd_client.delete_download(best_username, fid)
                        except Exception as e:
                            logger.warning(f"Failed to delete completed download from slskd: {e}")

            downloaded_official_positions = set()
            for f_item, dest_path in overall_downloaded + overall_copied:
                position = official_position_for_file(f_item, dest_path, official_album_tracks)
                if position:
                    downloaded_official_positions.add(position)

            target_total = len(official_album_tracks) if official_album_tracks else len(to_download)
            if official_album_tracks and len(downloaded_official_positions) >= target_total:
                logger.info(f"Album '{artist} - {album}' is fully complete ({len(downloaded_official_positions)}/{target_total} official tracks).")
                album_complete = True
                break
            elif not official_album_tracks and sum(1 for f in to_download if f.get("download_status") == "succeeded") == len(to_download):
                album_complete = True
                break
            else:
                if attempt_downloaded_files:
                    logger.warning(f"Attempt {attempt + 1} finished with {len(downloaded_official_positions)}/{target_total} official tracks. Some tracks downloaded successfully. Breaking to let single-track fallback handle the rest instead of restarting full album download from next peer.")
                    break
                else:
                    logger.warning(f"Attempt {attempt + 1} finished with {len(downloaded_official_positions)}/{target_total} official tracks. Trying next candidate peer for remaining tracks...")

        if not overall_downloaded and not overall_copied and not official_album_tracks:
            logger.error(f"Failed to download or copy any tracks for '{artist} - {album}' across all peer candidates.")
            await db.update_album_download_status(download_id, "failed")
            return

        # Auto-fallback: if official album tracks are still missing after all candidate attempts, queue single-track downloads
        if official_album_tracks:
            downloaded_official_positions = set()
            for f_item, dest_path in overall_downloaded + overall_copied:
                position = official_position_for_file(f_item, dest_path, official_album_tracks)
                if position:
                    downloaded_official_positions.add(position)

            missing_official = [
                t for t in official_album_tracks 
                if ((t.get("disk_number") or t.get("disk_number") or t.get("disc_num") or 1), (t.get("track_position") or t.get("position") or t.get("track_num"))) not in downloaded_official_positions
            ]
            fallback_complete = True
            if missing_official:
                logger.warning(f"Album download for '{artist} - {album}' is missing {len(missing_official)} track(s) after checking peer candidates. Running automatic single-track fallbacks...")
                for missing_t in missing_official:
                    t_title = missing_t["title"]
                    logger.info(f"Running automatic single-track fallback for missing track #{missing_t.get('track_position')} ('{artist} - {t_title}')...")
                    try:
                        fallback_ok = await download_single_track_task(
                            artist=artist,
                            title=t_title,
                            album=album,
                            config=config,
                            db=db,
                            force=False,
                            user_id=user_id,
                            mbid_album_override=official_mb_release_mbid,
                            metadata_override={
                                'title': t_title, 'artist': missing_t.get('artist') or artist,
                                'album': album, 'album_artist': target_album_artist or artist,
                                'album_artists': target_album_artists or [target_album_artist or artist],
                                'credited_artists': missing_t.get('credited_artists') or [],
                                'track_num': missing_t.get('track_position'),
                                'track_total': sum(1 for t in official_album_tracks if (t.get('disk_number') or t.get('disc_num') or 1) == (missing_t.get('disk_number') or missing_t.get('disc_num') or 1)),
                                'disc_num': missing_t.get('disk_number') or missing_t.get('disc_num') or 1,
                                'disc_total': max((t.get('disk_number') or t.get('disc_num') or 1 for t in official_album_tracks), default=1),
                                'date': official_album_date, 'mbid_album': official_mb_release_mbid,
                                'mbid_recording': missing_t.get('id') if official_mb_release_mbid else None,
                            },
                        )
                        fallback_complete = bool(fallback_ok) and fallback_complete
                    except Exception as fallback_err:
                        fallback_complete = False
                        logger.error(f"Single-track fallback for '{t_title}' failed: {fallback_err}")
            # A folder containing only shared/base-edition tracks is not full
            # if any of the extra edition tracks failed their fallback.
            album_complete = fallback_complete

        # Harmonize metadata and ensure cover.jpg exists for the downloaded album
        if os.path.isdir(final_dir):
            if (official_album_cover_bytes or cover_bytes):
                cover_file = Path(final_dir) / "cover.jpg"
                if not cover_file.exists():
                    try:
                        cover_file.write_bytes(official_album_cover_bytes or cover_bytes)
                    except Exception:
                        pass
            try:
                from backend.app.sync import harmonize_album_directory
                await asyncio.to_thread(
                    harmonize_album_directory,
                    album_dir=final_dir,
                    target_album=album,
                    target_album_artist=target_album_artist or artist,
                    target_mbid=official_mb_release_mbid or mbid_album,
                    target_cover_bytes=official_album_cover_bytes or cover_bytes,
                    target_date=official_album_date or dz_date,
                )
            except Exception as harm_err:
                logger.warning(f"Could not harmonize album directory '{final_dir}': {harm_err}")

        # Trigger Navidrome scan at the end of album download
        if db and user_id:
            await db.refresh_library_paths(
                user_id, [str(path) for _, path in overall_downloaded + overall_copied]
            )
            for instrumental_path in instrumental_paths:
                await db.mark_instrumental(instrumental_path, user_id)
        if config.navidrome.url and config.navidrome.username and config.navidrome.password:
            try:
                from backend.app.clients.navidrome import NavidromeClient
                nd_client = NavidromeClient(
                    url=config.navidrome.url,
                    username=config.navidrome.username,
                    password=config.navidrome.password
                )
                logger.info("Triggering Navidrome scan after album download...")
                await nd_client.trigger_scan()
            except Exception as e:
                logger.warning(f"Failed to trigger Navidrome scan: {e}")

        logger.info(f"Album sync complete for {artist} - {album}.")
        
        # Cleanup any remaining explore/playlist duplicates for this album
        try:
            from backend.app.sync import cleanup_album_explore_tracks
            await asyncio.to_thread(cleanup_album_explore_tracks, Path(playlists_dir), Path(music_dir), artist, album)
        except Exception as cleanup_err:
            logger.error(f"Failed to cleanup explore tracks for downloaded album {album}: {cleanup_err}")
            
        await db.update_album_download_status(download_id, "completed" if album_complete else "partial")
        
    except asyncio.CancelledError:
        logger.warning(f"Album download {download_id} cancelled.")
        try:
            await db.update_album_download_status(download_id, "failed")
        except Exception as e:
            logger.error(f"Failed to update db status for cancelled album {download_id}: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in album download {download_id}: {e}")
        await db.update_album_download_status(download_id, "failed")
    finally:
        try:
            from backend.app.main import _cached_playlists
            _cached_playlists.clear()
            logger.info("Cleared ListenBrainz playlist cache.")
        except Exception as e:
            logger.debug(f"Failed to clear playlist cache: {e}")

_single_track_semaphore = asyncio.Semaphore(2)

def _is_instrumental_track(title: str, album: str = "", lyrics_type: str = "") -> bool:
    """Mark only when provider metadata or the release title gives explicit evidence."""
    return lyrics_type == "instrumental" or bool(re.search(
        r"(?i)(?:^|[\s\[(\-])instrumental(?:$|[\s\])\-])",
        f"{title or ''} {album or ''}",
    ))

@artist_alias_scope
async def download_single_track_task(
    artist: str,
    title: str,
    album: str,
    config: 'AppConfig',
    db=None,
    force: bool = False,
    user_id: Optional[str] = None,
    is_explore: bool = False,
    dest_dir_override: Optional[str] = None,
    mbid_album_override: Optional[str] = None,
    replace_path_override: Optional[str] = None,
    metadata_override: Optional[dict] = None,
):
    """Background task to search, download and organize a single track.

    Persist the decision trail before entering the semaphore.  This makes a
    failure inspectable after a restart instead of leaving only an ephemeral
    log line behind.
    """
    job_id = None
    if db and hasattr(db, "create_download_job"):
        try:
            job_id = await db.create_download_job(artist, title, album, user_id, request={
                "force": force, "is_explore": is_explore, "dest_dir_override": dest_dir_override,
                "mbid_album_override": mbid_album_override, "replace_path_override": replace_path_override,
                **({'metadata_override': metadata_override} if metadata_override else {}),
            })
        except Exception as journal_err:
            logger.warning(f"Could not create download journal for '{artist} - {title}': {journal_err}")
    try:
        async with _single_track_semaphore:
            if job_id is not None:
                await db.update_download_job(job_id, "running")
            success = await _download_single_track_internal(
                artist, title, album, config, db, force, user_id, is_explore,
                dest_dir_override, mbid_album_override, replace_path_override, job_id, metadata_override
            )
    except asyncio.CancelledError:
        if job_id is not None:
            await asyncio.shield(db.update_download_job(job_id, "interrupted", error_reason="Stopped before completion; retry is available"))
        raise
    except Exception as exc:
        if job_id is not None:
            await db.update_download_job(job_id, "failed", error_reason=str(exc))
        raise
    if job_id is not None:
        await db.update_download_job(
            job_id, "completed" if success else "failed",
            # The inner pipeline records a precise reason (for example the
            # search strategy produced no candidates).  Do not overwrite it
            # with a generic wrapper outcome.
            error_reason="" if success else None,
        )
    return success


async def _download_single_track_internal(
    artist: str,
    title: str,
    album: str,
    config: 'AppConfig',
    db=None,
    force: bool = False,
    user_id: Optional[str] = None,
    is_explore: bool = False,
    dest_dir_override: Optional[str] = None,
    mbid_album_override: Optional[str] = None,
    replace_path_override: Optional[str] = None,
    download_job_id: Optional[int] = None,
    metadata_override: Optional[dict] = None,
) -> bool:
    logger.info(f"Starting background single track download for {artist} - {title} (Album: {album}) (force={force})")

    music_dir = config.paths.music_dir
    playlists_dir = config.paths.navidrome_playlists_dir
    active_playlists = config.listenbrainz.active_playlists or ["weekly-exploration"]
    if user_id and db:
        try:
            user_row = await db.get_user_by_id(user_id)
            if user_row:
                if user_row.get("music_dir"):
                    music_dir = user_row["music_dir"]
                if user_row.get("playlist_dir"):
                    playlists_dir = user_row["playlist_dir"]
            user_cfg = await db.get_user_config(user_id)
            if user_cfg and user_cfg.get("active_playlists"):
                active_playlists = user_cfg["active_playlists"]
        except Exception:
            pass

    try:
        if not force:
            from backend.app.sync import find_existing_track_file, get_file_audio_info, check_quality_status
            
            playlist_dirs = [os.path.join(playlists_dir, p) for p in active_playlists]
            found_path = None
            for playlist_output_dir in playlist_dirs:
                audio_path, _ = find_existing_track_file(music_dir, playlist_output_dir, "", artist, title)
                if audio_path:
                    found_path = audio_path
                    break
            
            if found_path:
                ext, bitrate, bit_depth, sample_rate = get_file_audio_info(found_path)
                q_status = check_quality_status(ext, bitrate, bit_depth, sample_rate, config)
                if q_status in ["same", "better"]:
                    if album and not is_explore and not dest_dir_override and not replace_path_override:
                        # ``safe_move_file`` is intentionally resolved through the
                        # module-level wrapper above.  Importing it here makes it a
                        # local for this whole function, including the force=True
                        # download path below, where it was never assigned.
                        # ``safe_move_file`` and ``embed_metadata`` are resolved
                        # through the module-level imports.  Importing either here
                        # makes it local to this entire function and breaks the
                        # force=True path, which skips this branch.
                        from backend.app.sync import resolve_album_dir, get_library_filename, update_m3u_references
                        dest_dir, safe_artist, safe_album = resolve_album_dir(music_dir, artist, album)
                        if not Path(found_path).resolve().is_relative_to(dest_dir.resolve()) or metadata_override:
                            from backend.app.metadata_pipeline.tags import read_track
                            import base64
                            local = read_track(Path(found_path), include_cover=True)
                            known = metadata_override or {}
                            if local.get('recording_mbid') and known.get('mbid_recording') and local['recording_mbid'] != known['mbid_recording']:
                                logger.warning('Existing recording differs from selected release for %s; original retained', title)
                                return False
                            dest_dir, safe_artist, safe_album = resolve_album_dir(music_dir, artist, album,
                                disc_num=known.get('disc_num') or local.get('disc') or 1,
                                disc_total=known.get('disc_total') or local.get('disc_total') or 1)
                            clean_filename = get_library_filename(known.get('artist') or local.get('artist') or artist, safe_album,
                                known.get('track_num') or local.get('track'), known.get('title') or local.get('title') or title, Path(found_path).suffix)
                            dest_path = dest_dir / clean_filename
                            if not dest_path.exists() or dest_path.resolve() == Path(found_path).resolve():
                                try:
                                    found_lrc = Path(found_path).with_suffix(".lrc")
                                    lyrics = found_lrc.read_text(encoding='utf-8', errors='ignore') if found_lrc.exists() else None
                                    await _publish_tagged_track(Path(found_path), dest_path, dict(
                                        artist=known.get('artist') or local.get('artist') or artist,
                                        title=known.get('title') or local.get('title') or title, album=album,
                                        album_artist=known.get('album_artist') or local.get('album_artist') or artist,
                                        track_num=known.get('track_num') or local.get('track'), track_total=known.get('track_total') or local.get('track_total'),
                                        disc_num=known.get('disc_num') or local.get('disc') or 1, disc_total=known.get('disc_total') or local.get('disc_total') or 1,
                                        date=known.get('date') or local.get('date'),
                                        mbid_album=known.get('mbid_album') or local.get('release_mbid'),
                                        mbid_recording=known.get('mbid_recording') or local.get('recording_mbid'),
                                        cover_bytes=base64.b64decode(local['cover']) if local.get('cover') else None,
                                        credited_artists=known.get('credited_artists') or local.get('artists'),
                                        album_artists=known.get('album_artists') or local.get('album_artists'),
                                        album_artist_mbids=local.get('album_artist_mbids'),
                                    ), lyrics)
                                    if playlists_dir and Path(playlists_dir).exists():
                                        update_m3u_references(Path(playlists_dir), Path(found_path).name, dest_path)
                                    logger.info(f"Relocated and retagged existing track '{title}' into album folder '{dest_dir.name}'.")
                                    if db and user_id:
                                        await db.refresh_library_paths(user_id, [str(found_path), str(dest_path)])
                                    found_path = dest_path
                                except Exception as rel_err:
                                    logger.warning(f"Could not relocate existing track '{title}' into album: {rel_err}")
                                    return False

                    logger.info(f"Single track '{artist} - {title}' already exists in library in same/better quality ({ext.upper()} {bitrate}kbps). Skipping download.")
                    return True

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
        
        # Clean artist/title and build queries without slskd's '-' exclusion syntax.
        from backend.app.sync import build_track_search_queries
        clean_title = re.sub(r'[\(\[].*?[\)\]]', '', title).strip()
        search_queries = build_track_search_queries(artist, title, album)
        
        candidates = []
        i = -1
        async for query in search_queries_with_aliases(artist, search_queries, title=title, album=album):
            i += 1
            logger.info(f"Single track search strategy {i+1}: '{query}'")
            if download_job_id is not None:
                await db.update_download_job(download_job_id, "searching", query_text=query, query_strategy=i + 1)
            audio_quality_dict = config.slskd.audio_quality.model_dump() if hasattr(config.slskd.audio_quality, "model_dump") else dict(config.slskd.audio_quality)
            candidates, search_id = await slskd_client.search_candidates(
                artist=artist,
                title=title,
                query=query,
                audio_quality=audio_quality_dict,
                album=album,
                search_timeout=config.timeouts.search_seconds
            )
            if candidates:
                if search_id:
                    await slskd_client.delete_search(search_id)
                # Sort candidates keeping artist/quality/album priority first, using cleanliness and free slots as tie-breakers
                candidates = sorted(
                    candidates,
                    key=lambda c: (
                        not c.get("full_artist_matched", False),
                        c.get("priority", 999),
                        not c.get("album_matched", False),
                        -filename_cleanliness_score([c]),
                        not c.get("hasFreeUploadSlot", True),
                        c.get("queueLength", 0),
                        -c.get("speed", 0),
                    )
                )
                break
            if search_id:
                await slskd_client.delete_search(search_id)

        if not candidates:
            logger.warning(f"No candidates found for single track '{artist} - {title}'")
            if download_job_id is not None:
                await db.update_download_job(download_job_id, "failed", error_reason="No candidates matched the search strategies")
            return False

        # Try downloading the best candidate, skipping peers that already refused/timed out
        max_attempts = min(len(candidates), config.schedule.max_candidate_attempts)
        acoustid_retry_count = 0
        failed_usernames = set()
        candidate_idx = 0
        attempt_count = 0

        while attempt_count < max_attempts and candidate_idx < len(candidates):
            candidate = candidates[candidate_idx]
            candidate_idx += 1
            username = candidate["username"]
            if username in failed_usernames:
                continue

            remote_filename = candidate["filename"]
            size = candidate["size"]
            attempt_count += 1
            attempt_id = None
            if download_job_id is not None:
                attempt_id = await db.add_download_attempt(
                    download_job_id, attempt_count, query, username, remote_filename, size, "requested",
                    candidate.get("selection_reason", "selected ranked candidate"),
                )
            
            logger.info(f"Trying candidate {attempt_count}/{max_attempts} ({username}) for '{artist} - {title}'")
            success = await slskd_client.request_download(username, remote_filename, size)
            if not success:
                failed_usernames.add(username)
                if attempt_id is not None:
                    await db.update_download_attempt(attempt_id, "rejected", "slskd rejected the transfer request")
                continue

            # Poll status
            elapsed = 0
            poll_interval = 5
            status = "downloading"
            file_id = None
            
            last_bytes = 0
            from datetime import datetime
            last_progress_time = datetime.utcnow()
            
            while elapsed < config.timeouts.download_seconds:
                status, file_id, bytes_tx = await slskd_client.get_download_progress(username, remote_filename, size)
                if status == "succeeded":
                    break
                elif status == "failed":
                    if file_id:
                        await slskd_client.delete_download(username, file_id)
                    break
                
                # Stuck / No progress check:
                if bytes_tx > last_bytes:
                    last_bytes = bytes_tx
                    last_progress_time = datetime.utcnow()
                elif (datetime.utcnow() - last_progress_time).total_seconds() > 90:
                    logger.warning(f"Download for single track '{title}' is stuck in queue or has no progress for 90s. Skipping.")
                    status = "failed"
                    if file_id:
                        await slskd_client.delete_download(username, file_id)
                    break
                    
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

            if file_id:
                await slskd_client.delete_download(username, file_id)

            if status != "succeeded":
                failed_usernames.add(username)
                if attempt_id is not None:
                    await db.update_download_attempt(attempt_id, "failed", "transfer failed, timed out, or stalled", str(file_id or ""))
                continue

            # Find downloaded file
            from backend.app.sync import find_downloaded_file, get_safe_filename
            downloaded_file = find_downloaded_file(config.slskd.downloads_dir, remote_filename, size)
            if not downloaded_file:
                if attempt_id is not None:
                    await db.update_download_attempt(attempt_id, "failed", "transfer succeeded but the downloaded file was not found", str(file_id or ""))
                continue

            if attempt_id is not None:
                await db.update_download_attempt(attempt_id, "downloaded", transfer_id=str(file_id or ""))

            ext = os.path.splitext(remote_filename)[1] or ".mp3"

            # 1. Fetch metadata (MusicBrainz primary, Deezer fallback)
            fetched_album = album
            fetched_artist = artist
            track_num = None
            title_tag = title
            cover_bytes = None
            dz_date = None
            dz_album_artist = None
            credited_artists = None
            album_artists = None
            disc_num, disc_total, mbid_album, mbid_recording = 1, 1, None, None
            try:
                meta_result = {}
                try:
                    if metadata_override and metadata_override.get('album_root'):
                        # This context was verified against the existing release
                        # before queueing. Never choose another edition per song.
                        meta_result = {'artist': artist, 'title': title, 'track_num': None,
                            'cover_bytes': None, 'album_artist': artist, 'date': None}
                    else:
                        meta_result = await fetch_track_metadata_with_fallback(
                            deezer_client, artist, title, album,
                            **({'release_mbid': mbid_album_override} if mbid_album_override else {})
                        )
                except Exception:
                    if not metadata_override:
                        raise
                    meta_result = {'artist': artist, 'title': title, 'track_num': None,
                        'cover_bytes': None, 'album_artist': artist, 'date': None}
                extras = {**(meta_result.get('metadata_extras') or {}), **((metadata_override or {}).get('metadata_extras') or {})}
                meta_result.update({key: value for key, value in (metadata_override or {}).items() if value not in (None, '', [])})
                meta_result['metadata_extras'] = extras
                fetched_artist = meta_result["artist"]
                title_tag = meta_result["title"] or title
                fetched_album = album if album else (meta_result.get("album") or title)
                track_num = meta_result["track_num"]
                disc_num = meta_result.get("disc_num", 1)
                disc_total = meta_result.get("disc_total", 1)
                mbid_album = meta_result.get("mbid_album")
                mbid_recording = meta_result.get("mbid_recording")
                cover_bytes = meta_result["cover_bytes"]
                dz_album_artist = meta_result["album_artist"]
                credited_artists = meta_result.get("credited_artists")
                album_artists = meta_result.get("album_artists")
                dz_date = meta_result["date"]
            except Exception as meta_err:
                logger.warning(f"Could not retrieve metadata: {meta_err}")

            if mbid_album_override and not track_num:
                logger.warning('Selected release metadata has no verified position for %s; raw download retained, not publishing track 00', title)
                continue

            # 1.5 AcoustID check on raw downloaded file BEFORE moving/tagging
            from backend.app.clients.acoustid import acoustid_client, verification_status
            acoustid_mismatch = False
            verification_rejection_reason = "AcoustID metadata mismatch"
            try:
                is_valid, reason = await acoustid_client.verify_track_against_metadata(
                    downloaded_file,
                    expected_artist=fetched_artist,
                    expected_title=title_tag,
                    expected_mbid=mbid_recording,
                    **({'require_recording_match': True} if replace_path_override else {}),
                )
                status = verification_status(is_valid, reason)
                if status != "verified":
                    if status != "mismatch" and not replace_path_override:
                        logger.info(f"AcoustID verification skipped for single track '{title_tag}': {reason}")
                    else:
                        acoustid_retry_count += 1
                        acoustid_mismatch = True
                        verification_rejection_reason = f"AcoustID {status}: {reason}"
                        if status == "mismatch" and downloaded_file.exists():
                            downloaded_file.unlink()
                        logger.warning("Candidate rejected for '%s': %s. Current library audio is preserved.", title_tag, verification_rejection_reason)
            except Exception as ac_err:
                logger.error(f"Error during AcoustID check for single track {downloaded_file}: {ac_err}")
                if replace_path_override:
                    acoustid_retry_count += 1
                    acoustid_mismatch = True
                    verification_rejection_reason = f"AcoustID verification unavailable: {type(ac_err).__name__}"

            if acoustid_mismatch:
                if attempt_id is not None:
                    await db.update_download_attempt(attempt_id, "rejected", verification_rejection_reason)
                if config and hasattr(config, 'acoustid') and acoustid_retry_count >= max(1, config.acoustid.max_retries):
                    logger.warning(f"AcoustID max retries ({config.acoustid.max_retries}) reached for single track '{title_tag}'. No rejected candidate will be saved.")
                    break
                continue

            from backend.app.sync import resolve_album_dir, get_library_filename, get_safe_filename
            if replace_path_override:
                replace_target = Path(replace_path_override).resolve()
                dest_dir = replace_target.parent
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_audio_path = replace_target.with_suffix(ext)
            elif metadata_override and metadata_override.get('album_root'):
                album_root = Path(metadata_override['album_root']).resolve()
                dest_dir = Path(metadata_override['album_disc_dir']).resolve()
                if not album_root.is_relative_to(Path(music_dir).resolve()) or not dest_dir.is_relative_to(album_root):
                    raise ValueError('Pinned album destination is outside the music library')
                dest_dir.mkdir(parents=True, exist_ok=True)
                clean_filename = get_library_filename(fetched_artist, album, track_num, title_tag, ext)
                dest_audio_path = dest_dir / clean_filename
            elif dest_dir_override:
                dest_dir = Path(dest_dir_override)
                dest_dir.mkdir(parents=True, exist_ok=True)
                clean_filename = get_safe_filename(fetched_artist, title_tag, ext)
                dest_audio_path = dest_dir / clean_filename
            elif is_explore:
                dest_dir = Path(playlists_dir) / "explore"
                dest_dir.mkdir(parents=True, exist_ok=True)
                clean_filename = get_safe_filename(fetched_artist, title_tag, ext)
                dest_audio_path = dest_dir / clean_filename
            else:
                target_album_artist = dz_album_artist or artist
                dest_dir, safe_artist, safe_album = resolve_album_dir(
                    music_dir, target_album_artist, album, target_album_artist,
                    disc_num=disc_num, disc_total=disc_total
                )
                clean_filename = get_library_filename(fetched_artist, safe_album, track_num, title_tag, ext)
                dest_audio_path = dest_dir / clean_filename

            # Tag a private copy before publishing anything over an existing file.
            lyrics_content = None
            l_type = "missing"
            try:
                lyrics_content, l_type = await lrclib_client.get_lyrics(artist, title_tag, album)
            except Exception as lyrics_err:
                logger.warning(f"Could not retrieve lyrics for single track: {lyrics_err}")

            try:
                if metadata_override and metadata_override.get('album_root') and dest_audio_path.exists():
                    logger.warning('Missing-track destination already exists; refusing overwrite: %s', dest_audio_path)
                    return False
                await _publish_tagged_track(downloaded_file, dest_audio_path, dict(
                    artist=fetched_artist,
                    title=title_tag,
                    album=album,
                    track_num=track_num,
                    track_total=(meta_result or {}).get("track_total"),
                    cover_bytes=cover_bytes,
                    lyrics_text=lyrics_content,
                    album_artist=dz_album_artist or artist,
                    date=dz_date,
                    disc_num=disc_num,
                    disc_total=disc_total,
                    is_explore=is_explore,
                    mbid_album=mbid_album_override or mbid_album,
                    mbid_recording=mbid_recording,
                    credited_artists=credited_artists,
                    metadata_extras={**((meta_result or {}).get("metadata_extras") or {}), **({"instrumental": ["1"]} if _is_instrumental_track(title_tag, album, l_type) else {})},
                    album_artists=album_artists or [dz_album_artist or artist],
                ), lyrics_content, replace_existing=not bool(metadata_override and metadata_override.get('album_root')))
                logger.info(f"Saved and embedded metadata for single track '{fetched_artist} - {title_tag}'")
            except Exception as e:
                logger.warning(f"Could not embed metadata/lyrics for single track: {e}")
                if attempt_id is not None:
                    await db.update_download_attempt(attempt_id, "failed", f"Metadata staging failed: {e}")
                continue

            published_path = dest_audio_path
            replaced_paths: list[str] = []
            if replace_path_override:
                old_path = Path(replace_path_override).resolve()
                replaced_paths = [str(old_path), str(published_path)]
                try:
                    if old_path != published_path and old_path.exists():
                        old_path.unlink()
                    old_lyrics = old_path.with_suffix(".lrc")
                    if old_lyrics != published_path.with_suffix(".lrc") and old_lyrics.exists():
                        old_lyrics.unlink()
                    logger.info("Atomically replaced library track '%s' with '%s'", old_path, published_path)
                except Exception as publish_error:
                    logger.warning("Replacement saved, but old sidecars/files could not be cleaned for %s: %s", old_path, publish_error)

            if db and user_id and not is_explore:
                await db.refresh_library_paths(user_id, replaced_paths or [str(published_path)])
                if _is_instrumental_track(title_tag, album, l_type):
                    await db.mark_instrumental(str(published_path), user_id)

            # Trigger scan
            if config.navidrome.url and config.navidrome.username and config.navidrome.password:
                from backend.app.clients.navidrome import NavidromeClient
                nd_client = NavidromeClient(
                    url=config.navidrome.url,
                    username=config.navidrome.username,
                    password=config.navidrome.password
                )
                try:
                    await nd_client.trigger_scan()
                except Exception as scan_error:
                    logger.warning("Track saved, but Navidrome rescan failed: %s", scan_error)
                
            logger.info(f"Single track download complete for {artist} - {title}")
            if attempt_id is not None:
                await db.update_download_attempt(attempt_id, "completed", transfer_id=str(file_id or ""))
            if download_job_id is not None:
                await db.update_download_job(download_job_id, "committing", destination_path=str(published_path))
            return True
            
        logger.error(f"All candidate attempts failed for single track '{artist} - {title}'")
        if download_job_id is not None:
            await db.update_download_job(download_job_id, "failed", error_reason="All candidate attempts failed; inspect the candidate decisions")
        return False
        
    except Exception as e:
        logger.error(f"Error in single track download task: {e}")
        if download_job_id is not None:
            await db.update_download_job(download_job_id, "failed", error_reason=str(e))
        return False


@artist_alias_scope
async def grab_single_track_task(
    artist: str,
    title: str,
    album: str,
    username: str,
    remote_filename: str,
    size: int,
    config: 'AppConfig',
    db=None,
    user_id: Optional[str] = None,
    is_explore: bool = False,
    dest_dir_override: Optional[str] = None
):
    """Background task to download a chosen single track candidate and process it."""
    logger.info(f"Starting grab single track task for {artist} - {title} from peer {username}")
    
    music_dir = config.paths.music_dir
    playlists_dir = config.paths.navidrome_playlists_dir
    if user_id and db:
        try:
            user_row = await db.get_user_by_id(user_id)
            if user_row:
                if user_row.get("music_dir"):
                    music_dir = user_row["music_dir"]
                if user_row.get("playlist_dir"):
                    playlists_dir = user_row["playlist_dir"]
        except Exception:
            pass

    try:
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

        success = await slskd_client.request_download(username, remote_filename, size)
        if not success:
            logger.error(f"Failed to request download from slskd for '{artist} - {title}' from '{username}'")
            return

        # Poll status
        elapsed = 0
        poll_interval = 5
        status = "downloading"
        file_id = None
        
        last_bytes = 0
        from datetime import datetime
        last_progress_time = datetime.utcnow()
        
        while elapsed < config.timeouts.download_seconds:
            status, file_id, bytes_tx = await slskd_client.get_download_progress(username, remote_filename, size)
            if status == "succeeded":
                break
            elif status == "failed":
                if file_id:
                    await slskd_client.delete_download(username, file_id)
                break
            
            if bytes_tx > last_bytes:
                last_bytes = bytes_tx
                last_progress_time = datetime.utcnow()
            elif (datetime.utcnow() - last_progress_time).total_seconds() > 90:
                logger.warning(f"Grab download for single track '{title}' is stuck in queue or has no progress for 90s. Skipping.")
                status = "failed"
                if file_id:
                    await slskd_client.delete_download(username, file_id)
                break
                
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        if file_id:
            try:
                await slskd_client.delete_download(username, file_id)
            except Exception:
                pass

        if status != "succeeded":
            logger.error(f"Download did not succeed. Status: {status}")
            return

        # Find downloaded file
        from backend.app.sync import find_downloaded_file, get_safe_filename, sanitize_filename, embed_metadata
        downloaded_file = find_downloaded_file(config.slskd.downloads_dir, remote_filename, size)
        if not downloaded_file:
            logger.error(f"Could not find downloaded file on disk for '{artist} - {title}'")
            return

        ext = os.path.splitext(remote_filename)[1] or ".mp3"

        # 1. Fetch metadata first to resolve correct naming & folder
        dz_meta = None
        fetched_album = album
        fetched_artist = artist
        fetched_album = album
        track_num = None
        title_tag = title
        cover_bytes = None
        dz_date = None
        dz_album_artist = None
        credited_artists = None
        album_artists = None
        disc_num, disc_total, mbid_album, mbid_recording = 1, 1, None, None
        try:
            meta_result = {}
            meta_result = await fetch_track_metadata_with_fallback(
                deezer_client, artist, title, album
            )
            fetched_artist = meta_result["artist"]
            title_tag = meta_result["title"] or title
            fetched_album = album if album else (meta_result.get("album") or title)
            track_num = meta_result["track_num"]
            disc_num = meta_result.get("disc_num", 1)
            disc_total = meta_result.get("disc_total", 1)
            mbid_album = meta_result.get("mbid_album")
            mbid_recording = meta_result.get("mbid_recording")
            cover_bytes = meta_result["cover_bytes"]
            dz_album_artist = meta_result["album_artist"]
            credited_artists = meta_result.get("credited_artists")
            album_artists = meta_result.get("album_artists")
            dz_date = meta_result["date"]
        except Exception as meta_err:
            logger.warning(f"Could not retrieve metadata: {meta_err}")

        from backend.app.clients.acoustid import acoustid_client, verification_status
        valid, reason = await acoustid_client.verify_track_against_metadata(
            downloaded_file, expected_artist=fetched_artist, expected_title=title_tag,
            expected_mbid=mbid_recording,
        )
        if verification_status(valid, reason) == "mismatch":
            logger.warning("Chosen transfer rejected by AcoustID: %s", reason)
            # Preserve the existing library file; this raw candidate isn't published.
            downloaded_file.unlink(missing_ok=True)
            return False

        from backend.app.sync import resolve_album_dir, get_library_filename, get_safe_filename
        if dest_dir_override:
            dest_dir = Path(dest_dir_override)
            dest_dir.mkdir(parents=True, exist_ok=True)
            clean_filename = get_safe_filename(fetched_artist, title_tag, ext)
            dest_audio_path = dest_dir / clean_filename
        elif is_explore:
            dest_dir = Path(playlists_dir) / "explore"
            dest_dir.mkdir(parents=True, exist_ok=True)
            clean_filename = get_safe_filename(fetched_artist, title_tag, ext)
            dest_audio_path = dest_dir / clean_filename
        else:
            target_album_artist = dz_album_artist or artist
            dest_dir, safe_artist, safe_album = resolve_album_dir(
                music_dir, target_album_artist, album, target_album_artist,
                disc_num=disc_num, disc_total=disc_total
            )
            clean_filename = get_library_filename(fetched_artist, safe_album, track_num, title_tag, ext)
            dest_audio_path = dest_dir / clean_filename

        # 3. Fetch lyrics and embed tags
        lyrics_content = None
        l_type = "missing"
        try:
            lyrics_content, l_type = await lrclib_client.get_lyrics(artist, title_tag, album)
        except Exception as lyrics_err:
            logger.warning(f"Could not retrieve lyrics for single track: {lyrics_err}")

        try:
            await _publish_tagged_track(downloaded_file, dest_audio_path, dict(
                artist=fetched_artist,
                title=title_tag,
                album=album,
                track_num=track_num,
                track_total=(meta_result or {}).get("track_total"),
                cover_bytes=cover_bytes,
                lyrics_text=lyrics_content,
                album_artist=dz_album_artist or artist,
                date=dz_date,
                disc_num=disc_num,
                disc_total=disc_total,
                is_explore=is_explore,
                mbid_album=mbid_album,
                mbid_recording=mbid_recording,
                credited_artists=credited_artists,
                metadata_extras={**((meta_result or {}).get("metadata_extras") or {}), **({"instrumental": ["1"]} if _is_instrumental_track(title_tag, album, l_type) else {})},
                album_artists=album_artists or [dz_album_artist or artist],
            ), lyrics_content)
            logger.info(f"Saved and embedded metadata for grabbed track '{fetched_artist} - {title_tag}'")
        except Exception as e:
            logger.warning(f"Could not embed metadata/lyrics for grabbed track: {e}")
            return

        # Trigger scan
        if db and user_id and not is_explore:
            await db.refresh_library_paths(user_id, [str(dest_audio_path)])
            if _is_instrumental_track(title_tag, album, l_type):
                await db.mark_instrumental(str(dest_audio_path), user_id)

        if config.navidrome.url and config.navidrome.username and config.navidrome.password:
            from backend.app.clients.navidrome import NavidromeClient
            nd_client = NavidromeClient(
                url=config.navidrome.url,
                username=config.navidrome.username,
                password=config.navidrome.password
            )
            await nd_client.trigger_scan()
            
        logger.info(f"Grabbed track download complete for {artist} - {title}")
        
    except Exception as e:
        logger.error(f"Error in grab single track task: {e}")
