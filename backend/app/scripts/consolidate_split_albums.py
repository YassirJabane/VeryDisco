import os
import sys
import shutil
import logging
from pathlib import Path
from typing import Dict, List, Tuple

from backend.app.config import load_config
from backend.app.sync import embed_metadata

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

    # 1. Gather all album directories: album_lower -> List[Path]
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

    consolidated_files_count = 0

    # 2. Process split albums
    for alb_key, dirs in album_map.items():
        if len(dirs) <= 1:
            # Check if single folder has inconsistent ALBUMARTIST tags
            single_dir = dirs[0]
            primary_artist = single_dir.parent.name
            for f in single_dir.glob("*"):
                if f.is_file() and f.suffix.lower() in [".mp3", ".flac", ".m4a"]:
                    try:
                        from backend.app.main import read_basic_tags
                        meta = read_basic_tags(f)
                        embed_metadata(
                            file_path=str(f),
                            artist=meta.get("artist") or primary_artist,
                            title=meta.get("title") or f.stem,
                            album=single_dir.name,
                            album_artist=primary_artist,
                            track_num=meta.get("track_num")
                        )
                        consolidated_files_count += 1
                    except Exception as e:
                        logger.warning(f"Error re-tagging {f}: {e}")
            continue

        logger.info(f"Found split album '{dirs[0].name}' across {len(dirs)} folders: {[d.parent.name for d in dirs]}")

        # Determine primary target folder (prefer Kanye West / Ye / folder with most files)
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
                        logger.warning(f"Destination file already exists, removing duplicate '{item}'")
                        os.remove(item)

            # Remove empty secondary folder
            try:
                sec_dir.rmdir()
                logger.info(f"Removed empty directory: {sec_dir}")
            except Exception as e:
                logger.warning(f"Could not remove {sec_dir}: {e}")

        # 3. Update ALBUMARTIST tag for all consolidated files in primary_dir
        for f in primary_dir.glob("*"):
            if f.is_file() and f.suffix.lower() in [".mp3", ".flac", ".m4a"]:
                try:
                    from backend.app.main import read_basic_tags
                    meta = read_basic_tags(f)
                    embed_metadata(
                        file_path=str(f),
                        artist=meta.get("artist") or primary_artist,
                        title=meta.get("title") or f.stem,
                        album=album_name,
                        album_artist=primary_artist,
                        track_num=meta.get("track_num")
                    )
                    consolidated_files_count += 1
                except Exception as e:
                    logger.warning(f"Error updating metadata for {f}: {e}")

    logger.info(f"Consolidation complete! Updated tags for {consolidated_files_count} files.")
    return consolidated_files_count

if __name__ == "__main__":
    cfg = load_config()
    target = sys.argv[1] if len(sys.argv) > 1 else ""
    music_dir = cfg.paths.music_dir
    consolidate_split_albums(music_dir, target)
