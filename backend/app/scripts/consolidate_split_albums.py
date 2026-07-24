import os
import sys
import shutil
import logging
from pathlib import Path
from typing import Dict, List, Tuple

from backend.app.config import load_config
from backend.app.sync import embed_metadata, get_library_filename
from backend.app.album_sync import extract_track_num_from_filename, clean_track_title

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def process_and_fix_album_folder(primary_dir: Path, primary_artist: str, album_name: str) -> int:
    """
    Standardizes all audio files in primary_dir:
    - Extracts track numbers from filenames/ID3 tags or assigns positional index
    - Fixes mangled titles (e.g. Teriya King -> KING if original filename was 01 - KING)
    - Unifies ALBUMARTIST tag to primary_artist
    - Embeds metadata and renames files to Artist_Album_TrackNum_Title.ext
    """
    audio_files = [f for f in primary_dir.glob("*") if f.is_file() and f.suffix.lower() in [".mp3", ".flac", ".m4a"]]
    if not audio_files:
        return 0

    # Sort files by parsed track number or filename
    def _sort_key(f: Path):
        tn = extract_track_num_from_filename(f.name)
        return (tn if tn is not None else 999, f.name)

    sorted_files = sorted(audio_files, key=_sort_key)
    updated_count = 0

    for idx, f in enumerate(sorted_files):
        try:
            from backend.app.main import read_basic_tags
            meta = read_basic_tags(f)
            
            parsed_tn = extract_track_num_from_filename(f.name)
            meta_tn = meta.get("track_num")
            track_num = parsed_tn or (int(meta_tn) if meta_tn and int(meta_tn) > 0 else (idx + 1))
            
            # Determine clean title
            raw_title = meta.get("title") or f.stem
            cleaned_title = clean_track_title(f.stem, primary_artist, album_name)
            
            # If metadata title looks mangled (e.g. Teriya King vs KING in filename)
            if "teri" in raw_title.lower() and "king" in raw_title.lower() and "king" in f.stem.lower():
                final_title = "KING"
            elif cleaned_title and len(cleaned_title) < len(raw_title) and "09 -" not in cleaned_title:
                final_title = cleaned_title
            else:
                final_title = raw_title or cleaned_title or f.stem

            # Ensure track artist doesn't spill into album artist
            track_artist = meta.get("artist") or primary_artist

            embed_metadata(
                file_path=str(f),
                artist=track_artist,
                title=final_title,
                album=album_name,
                album_artist=primary_artist,
                track_num=track_num
            )

            # Rename file to standard library format
            new_filename = get_library_filename(primary_artist, album_name, track_num, final_title, f.suffix)
            new_path = primary_dir / new_filename
            if f.resolve() != new_path.resolve():
                if new_path.exists():
                    os.remove(new_path)
                shutil.move(str(f), str(new_path))
                # Also move sidecar lrc if exists
                old_lrc = f.with_suffix(".lrc")
                if old_lrc.exists():
                    shutil.move(str(old_lrc), str(new_path.with_suffix(".lrc")))

            updated_count += 1
            logger.info(f"Fixed track #{track_num:02d}: '{final_title}' -> {new_filename}")
        except Exception as e:
            logger.warning(f"Error processing file {f}: {e}")

    return updated_count

def consolidate_split_albums(music_dir: str, target_album_filter: str = "") -> int:
    """
    Finds albums split across multiple artist folders under music_dir,
    consolidates all tracks into a single main artist folder,
    updates their ALBUMARTIST ID3 tags, and cleans up empty folders.
    """
    music_path = Path(music_dir)
    if not music_path.exists():
        logger.error(f"Music directory {music_dir} does not exist")
        return 0

    album_map: Dict[str, List[Path]] = {}
    
    for artist_dir in music_path.iterdir():
        if artist_dir.is_dir():
            for album_dir in artist_dir.iterdir():
                if album_dir.is_dir():
                    alb_name = album_dir.name
                    if target_album_filter and target_album_filter.lower() not in alb_name.lower():
                        continue
                    key = alb_name.lower()
                    if key not in album_map:
                        album_map[key] = []
                    album_map[key].append(album_dir)

    total_updated = 0

    for alb_key, dirs in album_map.items():
        if len(dirs) <= 1:
            single_dir = dirs[0]
            primary_artist = single_dir.parent.name
            total_updated += process_and_fix_album_folder(single_dir, primary_artist, single_dir.name)
            continue

        logger.info(f"Found split album '{dirs[0].name}' across {len(dirs)} folders: {[d.parent.name for d in dirs]}")

        def folder_score(d: Path) -> Tuple[int, int]:
            count = len(list(d.glob("*.mp3"))) + len(list(d.glob("*.flac"))) + len(list(d.glob("*.m4a")))
            name_score = 10 if "kanye" in d.parent.name.lower() else (5 if "ye" in d.parent.name.lower() else 0)
            return (name_score, count)

        sorted_dirs = sorted(dirs, key=folder_score, reverse=True)
        primary_dir = sorted_dirs[0]
        primary_artist = primary_dir.parent.name
        album_name = primary_dir.name

        logger.info(f"Consolidating album '{album_name}' into primary folder: '{primary_dir}' (Artist: {primary_artist})")

        for sec_dir in sorted_dirs[1:]:
            for item in list(sec_dir.iterdir()):
                if item.is_file():
                    dest_file = primary_dir / item.name
                    if not dest_file.exists():
                        shutil.move(str(item), str(dest_file))
                        logger.info(f"Moved '{item.name}' from {sec_dir.parent.name} to {primary_dir.parent.name}")
                    else:
                        os.remove(item)

            try:
                sec_dir.rmdir()
                logger.info(f"Removed empty directory: {sec_dir}")
            except Exception as e:
                logger.warning(f"Could not remove {sec_dir}: {e}")

        total_updated += process_and_fix_album_folder(primary_dir, primary_artist, album_name)

    logger.info(f"Consolidation complete! Updated tags and names for {total_updated} files.")
    return total_updated

if __name__ == "__main__":
    cfg = load_config()
    target = sys.argv[1] if len(sys.argv) > 1 else ""
    music_dir = cfg.paths.music_dir
    consolidate_split_albums(music_dir, target)
