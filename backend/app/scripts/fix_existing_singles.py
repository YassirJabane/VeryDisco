import os
import sys
import asyncio
import logging
from pathlib import Path
from typing import Optional

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from backend.app.config import ConfigManager
from backend.app.logger import setup_logging
from backend.app.clients.deezer import deezer_client
from backend.app.album_sync import fetch_track_metadata_with_fallback
from backend.app.sync import (
    resolve_album_dir,
    get_library_filename,
    embed_metadata,
    sanitize_filename
)
from backend.app.clients.navidrome import NavidromeClient

logger = setup_logging("INFO")

def read_file_basic_tags(file_path: Path) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (artist, title, album) from file tags."""
    ext = file_path.suffix.lower()
    try:
        if ext == ".mp3":
            from mutagen.easyid3 import EasyID3
            audio = EasyID3(str(file_path))
            artist = audio.get("artist", [""])[0]
            title = audio.get("title", [""])[0]
            album = audio.get("album", [""])[0]
            return artist, title, album
        elif ext in [".flac", ".ogg"]:
            from mutagen.flac import FLAC
            audio = FLAC(str(file_path))
            artist = audio.get("artist", [""])[0]
            title = audio.get("title", [""])[0]
            album = audio.get("album", [""])[0]
            return artist, title, album
        elif ext in [".m4a", ".mp4"]:
            from mutagen.mp4 import MP4
            audio = MP4(str(file_path))
            artist = audio.get("\xa9ART", [""])[0]
            title = audio.get("\xa9nam", [""])[0]
            album = audio.get("\xa9alb", [""])[0]
            return artist, title, album
    except Exception as e:
        logger.warning(f"Error reading tags from {file_path}: {e}")
    return None, None, None

async def process_music_directory(music_dir: Path):
    """Scan music_dir for any files in 'explore' or tagged as 'Singles' and fix them."""
    if not music_dir.exists():
        logger.error(f"Music directory does not exist: {music_dir}")
        return

    logger.info(f"Scanning library in '{music_dir}' for legacy 'Singles' albums...")
    
    fixed_explore = 0
    fixed_library = 0
    removed_folders = 0

    for root, dirs, files in os.walk(music_dir):
        root_path = Path(root)
        is_explore_folder = "explore" in [p.lower() for p in root_path.parts]
        
        for f in files:
            file_path = root_path / f
            ext = file_path.suffix.lower()
            if ext not in [".mp3", ".flac", ".m4a"]:
                continue

            artist, title, album = read_file_basic_tags(file_path)
            if not artist or not title:
                # Fallback to parsing filename
                stem = file_path.stem
                if " - " in stem:
                    parts = stem.split(" - ", 1)
                    artist = artist or parts[0].strip()
                    title = title or parts[1].strip()

            if not artist or not title:
                continue

            # Case A: Explore folder tracks
            if is_explore_folder or (album and album.lower() == "singles" and is_explore_folder):
                logger.info(f"[EXPLORE] Tagging explore track '{artist} - {title}' under 'Explore Tracks'...")
                try:
                    embed_metadata(
                        file_path=str(file_path),
                        artist=artist,
                        title=title,
                        is_explore=True
                    )
                    fixed_explore += 1
                except Exception as e:
                    logger.error(f"Failed to re-tag explore track {file_path}: {e}")

            # Case B: Library tracks tagged as "Singles" or inside a /Singles/ directory
            elif (album and album.lower() == "singles") or root_path.name.lower() == "singles":
                logger.info(f"[LIBRARY FIX] Resolving canonical metadata for '{artist} - {title}' (currently in 'Singles')...")
                try:
                    meta_result = await fetch_track_metadata_with_fallback(deezer_client, artist, title)
                    dz_title = meta_result.get("title") or title
                    dz_artist = meta_result.get("artist") or artist
                    dz_album_artist = meta_result.get("album_artist") or artist
                    dz_album = meta_result.get("album") or f"{dz_title} - Single"
                    track_num = meta_result.get("track_num")
                    track_total = meta_result.get("track_total")
                    disc_num = meta_result.get("disc_num", 1)
                    disc_total = meta_result.get("disc_total", 1)
                    mbid_album = meta_result.get("mbid_album")
                    mbid_recording = meta_result.get("mbid_recording")
                    cover_bytes = meta_result.get("cover_bytes")
                    dz_date = meta_result.get("date")

                    target_folder, safe_artist, safe_album = resolve_album_dir(
                        music_dir, dz_artist, dz_album, dz_album_artist, disc_num=disc_num, disc_total=disc_total
                    )
                    safe_filename = get_library_filename(dz_artist, safe_album, track_num, dz_title, ext)
                    dest_path = target_folder / safe_filename

                    # Read existing lyrics if available
                    lyrics_text = None
                    lrc_path = file_path.with_suffix(".lrc")
                    if lrc_path.exists():
                        try:
                            with open(lrc_path, "r", encoding="utf-8") as lf:
                                lyrics_text = lf.read()
                        except Exception:
                            pass

                    # Move file and lyrics
                    if file_path != dest_path:
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        file_path.rename(dest_path)
                        if lrc_path.exists():
                            lrc_path.rename(dest_path.with_suffix(".lrc"))

                    # Embed complete tags
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
                        mbid_recording=mbid_recording
                    )
                    fixed_library += 1
                    logger.info(f"Fixed '{dz_artist} - {dz_title}': moved to '{dest_path}'")
                except Exception as e:
                    logger.error(f"Error processing library single '{artist} - {title}': {e}")

    # Clean up empty 'Singles' directories
    for root, dirs, files in os.walk(music_dir, topdown=False):
        root_path = Path(root)
        if root_path.name.lower() == "singles":
            try:
                if not any(root_path.iterdir()):
                    root_path.rmdir()
                    removed_folders += 1
                    logger.info(f"Removed empty directory: {root_path}")
            except Exception:
                pass

    logger.info(f"Migration complete! Fixed {fixed_explore} explore tracks, {fixed_library} library singles, and removed {removed_folders} empty 'Singles' directories.")

async def main():
    cfg_mgr = ConfigManager()
    cfg = cfg_mgr.config
    music_dir = Path(cfg.slskd.music_dir)
    
    await process_music_directory(music_dir)

    # Trigger Navidrome rescan
    if cfg.navidrome.url and cfg.navidrome.username:
        logger.info("Triggering Navidrome library rescan...")
        nd_client = NavidromeClient(
            url=cfg.navidrome.url,
            username=cfg.navidrome.username,
            password=cfg.navidrome.password,
            token=cfg.navidrome.token,
            salt=cfg.navidrome.salt
        )
        await nd_client.trigger_rescan()

if __name__ == "__main__":
    asyncio.run(main())
