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

logger = get_logger()


async def fetch_track_metadata_with_fallback(
    deezer_client: "DeezerClient",
    artist: str,
    title: str,
    album: str = "",
) -> Dict[str, Any]:
    """
    Fetch track metadata + cover art, preferring MusicBrainz over Deezer.

    Returns a unified dict:
      - 'title'         : canonical title
      - 'artist'        : full artist string (with feat.)
      - 'album_artist'  : primary artist only
      - 'date'          : release date string (YYYY or YYYY-MM-DD)
      - 'track_num'     : track position (int or None)
      - 'cover_bytes'   : image bytes or None
      - 'source'        : 'musicbrainz' | 'deezer' | 'none'
    """
    result = {
        "title": title,
        "artist": artist,
        "album_artist": artist,
        "date": None,
        "track_num": None,
        "cover_bytes": None,
        "source": "none",
    }

    # --- Try MusicBrainz first ---
    try:
        from backend.app.clients.musicbrainz import musicbrainz_client
        mb = await musicbrainz_client.get_track_metadata(artist, title, album)
        if mb:
            result.update({
                "title": mb.get("title", title),
                "artist": mb.get("artist", artist),
                "album_artist": mb.get("album_artist", artist),
                "date": mb.get("date"),
                "track_num": mb.get("track_num"),
                "source": "musicbrainz",
            })
            # Try Cover Art Archive
            if mb.get("release_mbid"):
                cover = await musicbrainz_client.get_cover_art(mb["release_mbid"])
                if cover:
                    result["cover_bytes"] = cover
                    return result
    except Exception as e:
        logger.debug(f"MusicBrainz metadata lookup failed for '{artist} - {title}': {e}")

    # --- Deezer fallback for cover art (and full metadata if MB gave nothing) ---
    try:
        dz_meta = await deezer_client.get_track_metadata(artist, title)
        if dz_meta:
            if result["source"] == "none":
                # MB gave nothing — use Deezer for everything
                result["title"] = dz_meta.get("title", title)
                track_id = dz_meta.get("id")
                track_details = await deezer_client.get_track_details(track_id) if track_id else None
                if track_details:
                    dz_artist, dz_album_artist = deezer_client.resolve_joint_artists(track_details)
                else:
                    dz_artist, dz_album_artist = deezer_client.resolve_joint_artists(dz_meta)
                result["artist"] = dz_artist
                result["album_artist"] = dz_album_artist
                result["track_num"] = dz_meta.get("track_position")
                album_id = dz_meta.get("album", {}).get("id")
                if album_id:
                    album_meta = await deezer_client.get_album_metadata(album_id)
                    if album_meta:
                        result["date"] = album_meta.get("release_date")
                        _, result["album_artist"] = deezer_client.resolve_joint_artists(album_meta)
                result["source"] = "deezer"

            # Always try to get cover from Deezer if MB cover was unavailable
            if not result["cover_bytes"]:
                cover_url = dz_meta.get("album", {}).get("cover_xl")
                if cover_url:
                    result["cover_bytes"] = await deezer_client.download_cover_art(cover_url)
    except Exception as e:
        logger.debug(f"Deezer metadata lookup failed for '{artist} - {title}': {e}")

    return result


def clean_album_name(album: str) -> str:
    s = re.sub(r'[\(\[].*?[\)\]]', '', album)
    s = re.sub(r'(?i)\b(deluxe|remastered|special|expanded|edition|single|ep|lp|bonus|tracks|version)\b', '', s)
    s = re.sub(r'\s+-\s+', ' ', s)
    s = re.sub(r'[^\w\s-]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def clean_artist_name(artist: str) -> str:
    s = re.sub(r'[\(\[].*?[\)\]]', '', artist)
    s = re.sub(r'[^\w\s-]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def check_artist_match(artist: str, parent_dir: str, filename: str) -> bool:
    main_artist = re.split(r'(?i)\b(?:feat\.?|ft\.?|and|&|with)\b', artist)[0].strip()
    from backend.app.sync import get_artist_aliases
    norm_aliases = get_artist_aliases(main_artist)
    norm_parent = re.sub(r'[^\w]', '', parent_dir).lower()
    norm_filename = re.sub(r'[^\w]', '', filename).lower()
    return any(alias in norm_parent or alias in norm_filename for alias in norm_aliases)

def check_remix_mismatch(album_title: str, folder_path: str) -> bool:
    """
    Returns True if the folder_path contains remix/screwed keywords that
    are NOT in the requested album_title, indicating a mismatch.
    """
    keywords = ["chopped", "screwed", "remix", "slowed", "reverb", "tribute", "cover", "instrumental", "karaoke", "acapella", "acappella", "slopped"]
    folder_lower = folder_path.lower()
    album_lower = album_title.lower()
    
    for kw in keywords:
        if kw in folder_lower and kw not in album_lower:
            return True
    return False

def check_album_match(album_title: str, folder_path: str) -> bool:
    """
    Verify that the folder name is a close match for the requested album_title.
    This prevents matching "Culture II" when the user requested "Culture".
    """
    folder_name = folder_path.replace("\\", "/").split("/")[-1].lower()
    album_lower = album_title.lower()
    
    # 1. Strip year tags or quality descriptors from folder name
    clean_folder = re.sub(r'\(?\b\d{4}\b\)?', '', folder_name)
    clean_folder = re.sub(r'\[[^\]]+\]|\([^\)]+\)', '', clean_folder)
    clean_folder = clean_folder.strip()
    clean_folder = re.sub(r'\s+', ' ', clean_folder)
    
    # 2. Tokenize and check for roman numerals or version mismatches
    roman_numerals = ["ii", "iii", "iv", "v", "vi"]
    numeric_digits = ["2", "3", "4", "5", "6"]
    
    for roman in roman_numerals:
        if re.search(r'\b' + roman + r'\b', clean_folder) and not re.search(r'\b' + roman + r'\b', album_lower):
            return False
            
    for num in numeric_digits:
        if re.search(r'\b' + num + r'\b', clean_folder) and not re.search(r'\b' + num + r'\b', album_lower):
            return False
            
    # Verify core words
    album_words = [w for w in re.findall(r'\w+', album_lower) if len(w) > 1]
    if not album_words:
        return bool(re.search(r'\b' + re.escape(album_lower) + r'\b', clean_folder))
        
    for word in album_words:
        if not re.search(r'\b' + re.escape(word) + r'\b', clean_folder):
            return False
            
    return True

def clean_track_title(basename: str, artist: str, album: str) -> str:
    """
    Extract a clean track title from a filename by stripping out
    artist names, album names, track numbers, and extra delimiters.
    """
    title = basename
    if title.lower().endswith((".mp3", ".flac", ".m4a")):
        title = os.path.splitext(title)[0]
        
    # Split by underscore to handle Artist_Album_TrackNum_Title convention
    parts = title.split('_')
    if len(parts) >= 4:
        for idx, p in enumerate(parts):
            if p.isdigit() and len(p) <= 3:
                candidate = "_".join(parts[idx+1:]).strip()
                if candidate:
                    return candidate

    # Remove artist & album (case insensitive)
    title = re.sub(re.escape(artist), '', title, flags=re.IGNORECASE)
    title = re.sub(re.escape(album), '', title, flags=re.IGNORECASE)
    
    # Clean parts of artist/album
    for part in re.split(r'[,&-]', album):
        part = part.strip()
        if len(part) > 2:
            title = re.sub(re.escape(part), '', title, flags=re.IGNORECASE)
            
    for part in re.split(r'[,&-]', artist):
        part = part.strip()
        if len(part) > 2:
            title = re.sub(re.escape(part), '', title, flags=re.IGNORECASE)

    # Remove numbers / counters
    title = re.sub(r'\b\d{1,3}\b', '', title)
    title = re.sub(r'^\d+\s*[-_.]?\s*', '', title)
    title = re.sub(r'[_\-\s.]{2,}', ' ', title)
    
    title = title.strip(' -_./\\')
    
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
    
    # 1. Match by exact title substring (ignoring non-alphanumeric chars)
    # Sort by title length descending to match longer titles first
    sorted_official = sorted(official_tracks, key=lambda t: len(t.get("title", "")), reverse=True)
    for track in sorted_official:
        t_title = track.get("title", "")
        if not t_title:
            continue
        clean_t_title = re.sub(r'[^\w]', '', t_title.lower())
        clean_filename = re.sub(r'[^\w]', '', filename_lower)
        if clean_t_title and clean_t_title in clean_filename:
            return track
            
    # 2. Match by track number if present in filename
    numbers = re.findall(r'\b\d+\b', filename)
    if numbers:
        track_num_val = int(numbers[0])
        for track in official_tracks:
            if track.get("track_position") == track_num_val:
                return track
                
    return None

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
    chosen_files: Optional[List[Dict[str, Any]]] = None
):
    """Background task wrapper that restricts concurrent runs using a semaphore."""
    async with album_download_semaphore:
        return await _download_album_task_internal(
            download_id, artist, track_title, album, config, db, force,
            user_id, chosen_username, chosen_folder, chosen_files
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
    chosen_files: Optional[List[Dict[str, Any]]] = None
):
    """Background task to search and download a full album via slskd with fallback strategies."""
    logger.info(f"Starting background album download task for {artist} - {album} (ID: {download_id})")
    
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

        if chosen_username and chosen_folder and chosen_files:
            logger.info(f"Using manually chosen candidate from peer '{chosen_username}' for folder '{chosen_folder}'")
            candidates = [((chosen_username, chosen_folder), chosen_files)]
        else:
            # Build album search strategies
            clean_art = clean_artist_name(artist)
            clean_alb = clean_album_name(album)
            main_art = re.split(r'(?i)\b(?:feat\.?|ft\.?|and|&|with)\b', clean_art)[0].strip()
            stripped_alb = re.sub(r'(?i)\b(single|ep|lp|deluxe|remastered|version)\b', '', album)
            stripped_alb = re.sub(r'\s+-\s+', ' ', stripped_alb)
            stripped_alb = re.sub(r'[^\w\s-]', ' ', stripped_alb)
            stripped_alb = re.sub(r'\s+', ' ', stripped_alb).strip()

            queries = []
            art_lower = clean_art.lower()
            alb_lower = clean_alb.lower()
            if art_lower == alb_lower:
                queries.append((clean_art, False))
            elif art_lower in alb_lower:
                queries.append((clean_alb, False))
            elif alb_lower in art_lower:
                queries.append((clean_art, False))
            else:
                queries.append((f"{clean_art} {clean_alb}", False))
                if main_art != clean_art:
                    queries.append((f"{main_art} {clean_alb}", False))
                if stripped_alb and stripped_alb != clean_alb:
                    queries.append((f"{clean_art} {stripped_alb}", False))
                if stripped_alb and stripped_alb != clean_alb and main_art != clean_art:
                    queries.append((f"{main_art} {stripped_alb}", False))
                queries.append((clean_alb, True))  # Broad query requires verifying the artist

            best_dir_key = None
            best_files = []
            candidates = []

            for query, require_artist_match in queries:
                if not query:
                    continue
                logger.info(f"Attempting album search strategy: '{query}' (require_artist_match={require_artist_match})")
                search_id = await slskd_client.create_search(query)
                if not search_id:
                    continue

                elapsed = 0
                poll_interval = 3
                search_completed = False
                
                while elapsed < config.timeouts.search_seconds:
                    status = await slskd_client.get_search_status(search_id)
                    if status and status[0]:
                        search_completed = True
                        break
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval

                if not search_completed:
                    logger.warning(f"Search for '{query}' timed out. Continuing with available results.")

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

                sorted_dirs = sorted(
                    directories.items(),
                    key=lambda item: (len(item[1]), filename_cleanliness_score(item[1]), -item[1][0].get("priority", 999)),
                    reverse=True
                )
                candidates = sorted_dirs
                break

            if not candidates:
                logger.warning(f"No valid album directory found for '{artist} - {album}' after all fallback searches.")
                await db.update_album_download_status(download_id, "failed")
                return

        # Pre-fetch official tracklist, cover art, and release date (MusicBrainz first, Deezer fallback)
        official_album_tracks = []
        official_album_cover_bytes = None
        official_album_date = None

        # 1. Try MusicBrainz Cover Art Archive first for official album artwork
        try:
            from backend.app.clients.musicbrainz import musicbrainz_client
            logger.info(f"Pre-fetching official cover art for '{artist} - {album}' from MusicBrainz Cover Art Archive...")
            official_album_cover_bytes = await musicbrainz_client.get_cover_art_for_artist_album(artist, album)
            if official_album_cover_bytes:
                logger.info(f"Retrieved official album cover art from MusicBrainz Cover Art Archive for '{album}'.")
        except Exception as e:
            logger.debug(f"MusicBrainz cover art pre-fetch failed for '{artist} - {album}': {e}")

        # 2. Query Deezer for tracklist, date, and fallback cover art
        try:
            logger.info(f"Pre-fetching official tracklist for album '{album}' from Deezer...")
            async with httpx.AsyncClient(timeout=config.timeouts.http_seconds) as client:
                search_url = f"https://api.deezer.com/search/album?q={urllib.parse.quote(f'{artist} {album}')}"
                resp = await client.get(search_url)
                if resp.status_code == 200:
                    results = resp.json().get("data", [])
                    best_album_match = None
                    clean_target_album = clean_album_name(album).lower()
                    for res in results:
                        res_title = clean_album_name(res.get("title", "")).lower()
                        if res_title == clean_target_album or clean_target_album in res_title:
                            best_album_match = res
                            # Prefer full album over single (nb_tracks > 1)
                            if res.get("nb_tracks", 0) > 3:
                                break
                    if not best_album_match and results:
                        best_album_match = results[0]

                    if best_album_match:
                        album_id = best_album_match["id"]
                        if not official_album_cover_bytes:
                            cover_url = best_album_match.get("cover_xl") or best_album_match.get("cover_big")
                            if cover_url:
                                official_album_cover_bytes = await deezer_client.download_cover_art(cover_url)
                        album_details = await deezer_client.get_album_metadata(album_id)
                        if album_details:
                            official_album_date = album_details.get("release_date")
                        tracks_data = await deezer_client.get_album_tracks(album_id)
                        if tracks_data and "data" in tracks_data:
                            official_album_tracks = tracks_data["data"]
                            logger.info(f"Fetched {len(official_album_tracks)} official tracks from Deezer.")
        except Exception as e:
            logger.warning(f"Could not pre-fetch official album metadata from Deezer: {e}")

        max_attempts = getattr(config.schedule, "max_candidate_attempts", 3) or 3
        overall_downloaded = []
        overall_copied = []
        album_complete = False

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

            for f in best_files:
                filename_part = os.path.basename(f["filename"].replace("\\", "/"))
                basename = os.path.splitext(filename_part)[0]
                
                matched = match_file_to_official_track(filename_part, official_album_tracks)
                if matched:
                    clean_title = matched["title"]
                    f["track_num"] = matched.get("track_position")
                    f["title_tag"] = matched["title"]
                elif official_album_tracks:
                    logger.info(f"Skipping extra non-album file in search result folder: {filename_part}")
                    continue
                else:
                    clean_title = clean_track_title(basename, artist, album)
                
                norm_title = re.sub(r'[^\w]', '', clean_title).lower()
                if norm_title in seen_tracks:
                    logger.info(f"Skipping duplicate track in search result folder: {filename_part}")
                    continue
                seen_tracks.add(norm_title)

                existing_path = None
                if not force:
                    for playlist_source in active_playlists:
                        playlist_output_dir = os.path.join(playlists_dir, playlist_source)
                        audio_path, _ = find_existing_track_file(music_dir, playlist_output_dir, "", artist, clean_title)
                        if audio_path:
                            existing_path = audio_path
                            break

                if existing_path:
                    ext, bitrate, bit_depth, sample_rate = get_file_audio_info(existing_path)
                    q_status = check_quality_status(ext, bitrate, bit_depth, sample_rate, config)
                    if q_status in ["same", "better"]:
                        logger.info(f"Skipping download of track '{artist} - {clean_title}'; using existing same/better quality file: {existing_path}")
                        
                        # If the existing file is already inside the destination album folder,
                        # there is nothing to do — skip entirely to avoid creating duplicates.
                        if Path(existing_path).resolve().parent == Path(final_dir).resolve():
                            logger.debug(f"Existing file already in destination folder, no copy needed for '{clean_title}'")
                            copied_files.append((f, existing_path))
                            continue
                        
                        # 1. Fetch metadata (MusicBrainz primary, Deezer fallback)
                        track_num = None
                        cover_bytes = None
                        lyrics_text = None
                        title_tag = clean_title
                        dz_date = None
                        dz_album_artist = None
                        dz_artist = artist
                        try:
                            meta_result = await fetch_track_metadata_with_fallback(
                                deezer_client, artist, clean_title, album
                            )
                            title_tag = meta_result["title"]
                            track_num = meta_result["track_num"]
                            cover_bytes = meta_result["cover_bytes"]
                            dz_artist = meta_result["artist"]
                            dz_album_artist = meta_result["album_artist"]
                            dz_date = meta_result["date"]
                        except Exception as e:
                            logger.error(f"Metadata lookup failed for existing '{clean_title}': {e}")


                        ext_ext = existing_path.suffix
                        from backend.app.sync import get_library_filename
                        clean_filename = get_library_filename(artist, album, track_num, title_tag, ext_ext)
                        dest_path = final_dir / clean_filename
                        
                        try:
                            # Copy from explore/playlists dir to final library dir.
                            # Skip if they are already the same file.
                            if Path(existing_path).resolve() != Path(dest_path).resolve():
                                shutil.copy2(str(existing_path), str(dest_path))
                            
                            # Fetch lyrics and embed metadata
                            lyrics_text = None
                            try:
                                lyrics_text, l_type = await lrclib_client.get_lyrics(artist, title_tag)
                            except Exception:
                                pass
                            if lyrics_text:
                                with open(dest_path.with_suffix(".lrc"), "w", encoding="utf-8") as lf:
                                    lf.write(lyrics_text)
                            
                            embed_metadata(
                                file_path=str(dest_path),
                                artist=dz_artist,
                                title=title_tag,
                                album=album,
                                track_num=track_num,
                                cover_bytes=official_album_cover_bytes or cover_bytes,
                                lyrics_text=lyrics_text,
                                album_artist=artist,
                                date=official_album_date or dz_date
                            )

                            # Update M3U references and clean up the old file in explore/playlists
                            from backend.app.sync import update_m3u_references
                            try:
                                if Path(playlists_dir).exists():
                                    update_m3u_references(Path(playlists_dir), existing_path.name, dest_path)
                            except Exception as e:
                                logger.error(f"Failed to update M3U references on sync: {e}")

                            copied_files.append((f, dest_path))
                        except Exception as e:
                            logger.error(f"Failed to copy and link existing track {clean_title}: {e}")
                            to_download.append(f)
                    else:
                        to_download.append(f)
                else:
                    to_download.append(f)

            if not to_download:
                logger.info(f"All tracks of album '{artist} - {album}' were already present in equal/better quality and copied. Album download completed instantly.")
                overall_copied.extend(copied_files)
                album_complete = True
                break

            url = f"{slskd_client.base_url}/api/v0/transfers/downloads/{urllib.parse.quote(best_username)}"
            
            # Queue downloads in throttled batches of 3
            BATCH_SIZE = 3
            queue_failed = False
            async with httpx.AsyncClient(timeout=config.timeouts.http_seconds) as client:
                for batch_start in range(0, len(to_download), BATCH_SIZE):
                    batch = to_download[batch_start:batch_start + BATCH_SIZE]
                    download_payload = [{"filename": f["filename"], "size": f["size"]} for f in batch]
                    resp = await client.post(url, json=download_payload, headers=slskd_client._get_headers())
                    if resp.status_code not in [200, 201, 202]:
                        logger.error(f"Failed to queue album download batch from '{best_username}': {resp.status_code} {resp.text}")
                        queue_failed = True
                        break
                    await asyncio.sleep(2.0)
            
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
                            fname = file_info.get("filename")
                            actual_size = file_info.get("size", 0)
                            state = file_info.get("state", "").lower()
                            file_id = file_info.get("id")
                            bytes_tx = file_info.get("bytesTransferred", 0)
                            file_status_map[fname] = (state, file_id, bytes_tx, actual_size)

                for f in to_download:
                    if f.get("download_status") in ["succeeded", "failed"]:
                        continue
                        
                    fname = f["filename"]
                    req_size = f["size"]
                    
                    status_info = file_status_map.get(fname)
                    if not status_info:
                        # Not found in transfers yet, treat as downloading
                        status = "downloading"
                        file_id = None
                        bytes_tx = 0
                    else:
                        matched_state, file_id, bytes_tx, actual_size = status_info
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
                            status_info = file_status_map.get(f["filename"])
                            file_id = status_info[1] if status_info else None
                            if file_id:
                                await slskd_client.delete_download(best_username, file_id)
                    break
                    
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

            logger.info(f"Album download attempt finished. Successfully downloaded {len(attempt_downloaded_files)}/{len(to_download)} tracks.")
            
            if attempt_downloaded_files:
                for f, local_path in attempt_downloaded_files:
                    basename = local_path.stem
                    
                    matched = match_file_to_official_track(local_path.name, official_album_tracks)
                    if matched:
                        clean_title = matched["title"]
                        track_num = matched.get("track_position")
                        title_tag = matched["title"]
                    else:
                        clean_title = clean_track_title(basename, artist, album)
                        track_num = f.get("track_num")
                        title_tag = f.get("title_tag", clean_title)
                                     
                    # 1. Fetch metadata (MusicBrainz primary, Deezer fallback)
                    cover_bytes = None
                    lyrics_text = None
                    dz_date = None
                    dz_album_artist = None
                    dz_artist = artist
                    try:
                        meta_result = await fetch_track_metadata_with_fallback(
                            deezer_client, artist, clean_title, album
                        )
                        title_tag = meta_result["title"] or title_tag or clean_title
                        track_num = meta_result["track_num"] or track_num
                        cover_bytes = meta_result["cover_bytes"]
                        dz_artist = meta_result["artist"]
                        dz_album_artist = meta_result["album_artist"]
                        dz_date = meta_result["date"]
                    except Exception as e:
                        logger.error(f"Metadata lookup failed for '{clean_title}': {e}")

                        
                    # 2. Determine clean destination filename using library convention
                    ext_ext = local_path.suffix
                    from backend.app.sync import get_library_filename
                    clean_filename = get_library_filename(artist, album, track_num, title_tag, ext_ext)
                    dest_path = final_dir / clean_filename
                                     # 3. Move the file
                    try:
                        shutil.move(str(local_path), str(dest_path))
                    except Exception as e:
                        logger.error(f"Failed to move {local_path} to {dest_path}: {e}")
                        continue
 
                    # 4. Embed metadata & lyrics
                    try:
                        lyrics_text, l_type = await lrclib_client.get_lyrics(artist, title_tag)
                        if lyrics_text:
                            lrc_path = dest_path.with_suffix(".lrc")
                            with open(lrc_path, "w", encoding="utf-8") as lf:
                                lf.write(lyrics_text)
                        
                        embed_metadata(
                            file_path=str(dest_path),
                            artist=dz_artist if 'dz_artist' in locals() else artist,
                            title=title_tag,
                            album=album,
                            track_num=track_num,
                            cover_bytes=official_album_cover_bytes or cover_bytes,
                            lyrics_text=lyrics_text,
                            album_artist=artist,
                            date=official_album_date or dz_date
                        )
                    except Exception as e:
                        logger.error(f"Failed to embed metadata/lyrics for {dest_path}: {e}")

                    # 4.5 AcoustID verification check
                    from backend.app.clients.acoustid import acoustid_client
                    acoustid_mismatch = False
                    try:
                        is_valid, reason = await acoustid_client.verify_track_against_metadata(dest_path)
                        if not is_valid:
                            if "not configured" in reason or "No match found" in reason:
                                logger.info(f"AcoustID verification skipped for '{title_tag}': {reason}")
                            else:
                                logger.warning(f"AcoustID mismatch detected for '{title_tag}': {reason}. Discarding and queuing single-track replacement...")
                                acoustid_mismatch = True
                                # Delete mismatched file
                                if dest_path.exists():
                                    dest_path.unlink()
                                lrc_path = dest_path.with_suffix(".lrc")
                                if lrc_path.exists():
                                    lrc_path.unlink()
                                
                                # Queue replacement download
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
                        logger.error(f"Error during AcoustID check: {ac_err}")

                    if acoustid_mismatch:
                        f["download_status"] = "failed"
                        continue

                    overall_downloaded.append((f, dest_path))

                    # 5. Delete successful download from slskd tab
                    fid = f.get("file_id")
                    if fid:
                        try:
                            await slskd_client.delete_download(best_username, fid)
                        except Exception as e:
                            logger.warning(f"Failed to delete completed download from slskd: {e}")

            succeeded_count = sum(1 for f in to_download if f.get("download_status") == "succeeded")
            if succeeded_count == len(to_download):
                album_complete = True
                break
            else:
                logger.warning(f"Only downloaded {succeeded_count}/{len(to_download)} tracks from peer '{best_username}'. Falling back to the next peer candidate...")

        if not overall_downloaded and not overall_copied:
            logger.error(f"Failed to download or copy any tracks for '{artist} - {album}' across all peer candidates.")
            await db.update_album_download_status(download_id, "failed")
            return

        # Trigger Navidrome scan at the end of album download
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
            
        await db.update_album_download_status(download_id, "completed")
        
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

async def download_single_track_task(artist: str, title: str, album: str, config: 'AppConfig', db=None, force: bool = False, user_id: Optional[str] = None):
    """Background task to search, download and organize a single track."""
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
                    logger.info(f"Single track '{artist} - {title}' already exists in library in same/better quality ({ext.upper()} {bitrate}kbps). Skipping download.")
                    return

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
        
        # Clean artist and title
        clean_title = re.sub(r'[\(\[].*?[\)\]]', '', title)
        clean_artist = re.sub(r'[\(\[].*?[\)\]]', '', artist)
        
        # Query strategies
        query_primary = f"{clean_title} - {clean_artist}"
        query_primary = re.sub(r'[^\w\s-]', ' ', query_primary)
        query_primary = re.sub(r'\s+', ' ', query_primary).strip()
        
        from backend.app.sync import wildcard_artist
        w_artist = wildcard_artist(clean_artist)
        query_fallback_1 = f"{clean_title} - {w_artist}"
        query_fallback_1 = re.sub(r'[^\w\s\*-]', ' ', query_fallback_1)
        query_fallback_1 = re.sub(r'\s+', ' ', query_fallback_1).strip()
        
        main_artist = re.split(r'(?i)\b(?:feat\.?|ft\.?)\b', clean_artist)[0].strip()
        query_fallback_2 = f"{clean_title} - {main_artist}"
        query_fallback_2 = re.sub(r'[^\w\s-]', ' ', query_fallback_2)
        query_fallback_2 = re.sub(r'\s+', ' ', query_fallback_2).strip()

        search_queries = [query_primary, query_fallback_1, query_fallback_2]
        
        candidates = []
        for i, query in enumerate(search_queries):
            logger.info(f"Single track search strategy {i+1}: '{query}'")
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
                # Sort candidates by cleanliness first, keeping existing order (quality) as tie-breaker
                candidates = sorted(
                    candidates,
                    key=lambda c: filename_cleanliness_score([c]),
                    reverse=True
                )
                break
            if search_id:
                await slskd_client.delete_search(search_id)

        if not candidates:
            logger.warning(f"No candidates found for single track '{artist} - {title}'")
            return

        # Try downloading the best candidate
        attempts = min(len(candidates), config.schedule.max_candidate_attempts)
        for idx in range(attempts):
            candidate = candidates[idx]
            username = candidate["username"]
            remote_filename = candidate["filename"]
            size = candidate["size"]
            
            logger.info(f"Trying candidate {idx+1}/{attempts} ({username}) for '{artist} - {title}'")
            success = await slskd_client.request_download(username, remote_filename, size)
            if not success:
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
                continue

            # Find downloaded file
            from backend.app.sync import find_downloaded_file, get_safe_filename
            downloaded_file = find_downloaded_file(config.slskd.downloads_dir, remote_filename, size)
            if not downloaded_file:
                continue

            ext = os.path.splitext(remote_filename)[1] or ".mp3"

            # 1. Fetch metadata (MusicBrainz primary, Deezer fallback)
            fetched_album = album
            fetched_artist = artist
            track_num = None
            title_tag = title
            cover_bytes = None
            dz_date = None
            dz_album_artist = None
            try:
                meta_result = await fetch_track_metadata_with_fallback(
                    deezer_client, artist, title, album
                )
                fetched_artist = meta_result["artist"]
                title_tag = meta_result["title"] or title
                if meta_result.get("album"):
                    fetched_album = meta_result["album"]
                elif not fetched_album:
                    fetched_album = album
                track_num = meta_result["track_num"]
                cover_bytes = meta_result["cover_bytes"]
                dz_album_artist = meta_result["album_artist"]
                dz_date = meta_result["date"]
            except Exception as meta_err:
                logger.warning(f"Could not retrieve metadata: {meta_err}")

            from backend.app.sync import resolve_album_dir, get_library_filename
            dest_dir, safe_artist, safe_album = resolve_album_dir(music_dir, fetched_artist, fetched_album, dz_album_artist)

            clean_filename = get_library_filename(fetched_artist, safe_album, track_num, title_tag, ext)
            dest_audio_path = dest_dir / clean_filename

            # 2. Move file
            try:
                shutil.move(str(downloaded_file), str(dest_audio_path))
                logger.info(f"Moved single track to library: '{dest_audio_path}'")
            except Exception as e:
                logger.error(f"Failed to move single track to library: {e}")
                continue

            # 3. Fetch lyrics and embed tags
            lyrics_content = None
            try:
                lyrics_content, l_type = await lrclib_client.get_lyrics(artist, title_tag)
                if lyrics_content:
                    dest_lyrics_path = dest_audio_path.with_suffix(".lrc")
                    with open(dest_lyrics_path, "w", encoding="utf-8") as lf:
                        lf.write(lyrics_content)
            except Exception as lyrics_err:
                logger.warning(f"Could not retrieve lyrics for single track: {lyrics_err}")

            try:
                embed_metadata(
                    file_path=str(dest_audio_path),
                    artist=fetched_artist,
                    title=title_tag,
                    album=fetched_album,
                    track_num=track_num,
                    cover_bytes=cover_bytes,
                    lyrics_text=lyrics_content,
                    album_artist=dz_album_artist,
                    date=dz_date
                )
                logger.info(f"Saved and embedded metadata for single track '{fetched_artist} - {title_tag}'")
            except Exception as e:
                logger.warning(f"Could not embed metadata/lyrics for single track: {e}")

            # AcoustID check on single track replacement
            from backend.app.clients.acoustid import acoustid_client
            acoustid_mismatch = False
            try:
                is_valid, reason = await acoustid_client.verify_track_against_metadata(dest_audio_path)
                if not is_valid:
                    if "not configured" in reason or "No match found" in reason:
                        logger.info(f"AcoustID verification skipped for replacement track '{title_tag}': {reason}")
                    else:
                        logger.warning(f"AcoustID mismatch detected for replacement track '{title_tag}': {reason}. Discarding and retrying next candidate...")
                        acoustid_mismatch = True
                        if dest_audio_path.exists():
                            dest_audio_path.unlink()
                        lrc_path = dest_audio_path.with_suffix(".lrc")
                        if lrc_path.exists():
                            lrc_path.unlink()
            except Exception as ac_err:
                logger.error(f"Error during AcoustID check: {ac_err}")

            if acoustid_mismatch:
                continue

            # Trigger scan
            if config.navidrome.url and config.navidrome.username and config.navidrome.password:
                from backend.app.clients.navidrome import NavidromeClient
                nd_client = NavidromeClient(
                    url=config.navidrome.url,
                    username=config.navidrome.username,
                    password=config.navidrome.password
                )
                await nd_client.trigger_scan()
                
            logger.info(f"Single track download complete for {artist} - {title}")
            return
            
        logger.error(f"All candidate attempts failed for single track '{artist} - {title}'")
        
    except Exception as e:
        logger.error(f"Error in single track download task: {e}")


async def grab_single_track_task(
    artist: str,
    title: str,
    album: str,
    username: str,
    remote_filename: str,
    size: int,
    config: 'AppConfig',
    db=None,
    user_id: Optional[str] = None
):
    """Background task to download a chosen single track candidate and process it."""
    logger.info(f"Starting grab single track task for {artist} - {title} from peer {username}")
    
    music_dir = config.paths.music_dir
    if user_id and db:
        try:
            user_row = await db.get_user_by_id(user_id)
            if user_row and user_row.get("music_dir"):
                music_dir = user_row["music_dir"]
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
        track_num = None
        title_tag = title
        cover_bytes = None
        
        dz_date = None
        dz_album_artist = None
        try:
            dz_meta = await deezer_client.get_track_metadata(artist, title)
            if dz_meta:
                fetched_artist = dz_meta.get("artist", {}).get("name", artist)
                title_tag = dz_meta.get("title", title)
                if not fetched_album:
                    fetched_album = dz_meta.get("album", {}).get("title")
                track_num = dz_meta.get("track_position")
                cover_url = dz_meta.get("album", {}).get("cover_xl")
                if cover_url:
                    cover_bytes = await deezer_client.download_cover_art(cover_url)
                
                track_id = dz_meta.get("id")
                track_details = await deezer_client.get_track_details(track_id) if track_id else None
                if track_details:
                    fetched_artist, dz_album_artist = deezer_client.resolve_joint_artists(track_details)
                else:
                    fetched_artist, dz_album_artist = deezer_client.resolve_joint_artists(dz_meta)
                
                album_id = dz_meta.get("album", {}).get("id")
                if album_id:
                    album_meta = await deezer_client.get_album_metadata(album_id)
                    if album_meta:
                        dz_date = album_meta.get("release_date")
                        _, dz_album_artist = deezer_client.resolve_joint_artists(album_meta)
        except Exception as meta_err:
            logger.warning(f"Could not retrieve Deezer metadata: {meta_err}")

        from backend.app.sync import resolve_album_dir, get_library_filename
        dest_dir, safe_artist, safe_album = resolve_album_dir(music_dir, fetched_artist, fetched_album, dz_album_artist)
        clean_filename = get_library_filename(fetched_artist, safe_album, track_num, title_tag, ext)
        dest_audio_path = dest_dir / clean_filename

        # 2. Move file
        try:
            shutil.move(str(downloaded_file), str(dest_audio_path))
            logger.info(f"Moved grabbed track to library: '{dest_audio_path}'")
        except Exception as e:
            logger.error(f"Failed to move grabbed track to library: {e}")
            return

        # 3. Fetch lyrics and embed tags
        lyrics_content = None
        try:
            lyrics_content, l_type = await lrclib_client.get_lyrics(artist, title_tag)
            if lyrics_content:
                dest_lyrics_path = dest_audio_path.with_suffix(".lrc")
                with open(dest_lyrics_path, "w", encoding="utf-8") as lf:
                    lf.write(lyrics_content)
        except Exception as lyrics_err:
            logger.warning(f"Could not retrieve lyrics for single track: {lyrics_err}")

        try:
            embed_metadata(
                file_path=str(dest_audio_path),
                artist=fetched_artist,
                title=title_tag,
                album=fetched_album,
                track_num=track_num,
                cover_bytes=cover_bytes,
                lyrics_text=lyrics_content,
                album_artist=dz_album_artist,
                date=dz_date
            )
            logger.info(f"Saved and embedded metadata for grabbed track '{fetched_artist} - {title_tag}'")
        except Exception as e:
            logger.warning(f"Could not embed metadata/lyrics for grabbed track: {e}")

        # Trigger scan
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
