#!/usr/bin/env python3
"""
tag_existing_library_mbids.py

Recursively scans the local music library for audio files (.mp3, .flac, .m4a),
checks if MusicBrainz IDs (MBID) are embedded, fetches missing MBIDs from the
MusicBrainz API, and embeds them into audio tags with real-time console progress logging.

Usage:
    python backend/app/scripts/tag_existing_library_mbids.py [--music-dir PATH] [--dry-run]
"""

import sys
import os
import argparse
import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple

# Ensure backend root is on Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from backend.app.config import ConfigManager
from backend.app.clients.musicbrainz import musicbrainz_client

# Configure logging to stdout with timestamp formatting
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout
)
logger = logging.getLogger("mbid_tagger")

def extract_audio_tags(file_path: Path) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Extract (artist, title, album, mbid_track) from audio file metadata using Mutagen."""
    ext = file_path.suffix.lower()
    artist = None
    title = None
    album = None
    mbid_track = None

    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, UFID, TXXX
            tags = ID3(file_path)
            artist = str(tags.get("TPE1", [""])[0]) if "TPE1" in tags else None
            title = str(tags.get("TIT2", [""])[0]) if "TIT2" in tags else None
            album = str(tags.get("TALB", [""])[0]) if "TALB" in tags else None
            
            for ufid in tags.getall("UFID"):
                if ufid.owner in ("http://musicbrainz.org", "musicbrainz.org"):
                    mbid_track = ufid.data.decode("utf-8", errors="ignore")
                    break
            if not mbid_track:
                for txxx in tags.getall("TXXX"):
                    if txxx.desc.lower() in ("musicbrainz track id", "musicbrainz_trackid"):
                        mbid_track = txxx.text[0] if txxx.text else None
                        break

        elif ext in (".flac", ".ogg"):
            from mutagen.flac import FLAC
            audio = FLAC(file_path)
            artist = audio.get("artist", [None])[0]
            title = audio.get("title", [None])[0]
            album = audio.get("album", [None])[0]
            mbid_track = audio.get("musicbrainz_trackid", [None])[0]

        elif ext in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4
            audio = MP4(file_path)
            artist = audio.get("\xa9ART", [None])[0]
            title = audio.get("\xa9nam", [None])[0]
            album = audio.get("\xa9alb", [None])[0]
            t_data = audio.get("----:com.apple.iTunes:MusicBrainz Track Id")
            if t_data and t_data[0]:
                mbid_track = t_data[0].decode("utf-8", errors="ignore") if isinstance(t_data[0], bytes) else str(t_data[0])

    except Exception as e:
        logger.debug(f"Failed to read tags from {file_path}: {e}")

    return artist, title, album, mbid_track


def embed_mbid_into_file(file_path: Path, mbid_track: str, mbid_album: Optional[str] = None) -> bool:
    """Embed MusicBrainz track & album MBIDs into audio file tags."""
    ext = file_path.suffix.lower()
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, UFID, TXXX
            try:
                tags = ID3(file_path)
            except Exception:
                tags = ID3()
            if mbid_track:
                tags.add(UFID(owner="http://musicbrainz.org", data=mbid_track.encode('utf-8')))
                tags.add(TXXX(encoding=3, desc="MusicBrainz Track Id", text=[mbid_track]))
            if mbid_album:
                tags.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=[mbid_album]))
            tags.save(file_path, v2_version=3)

        elif ext in (".flac", ".ogg"):
            from mutagen.flac import FLAC
            audio = FLAC(file_path)
            if mbid_track:
                audio["musicbrainz_trackid"] = mbid_track
            if mbid_album:
                audio["musicbrainz_albumid"] = mbid_album
            audio.save()

        elif ext in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4
            audio = MP4(file_path)
            if mbid_track:
                audio["----:com.apple.iTunes:MusicBrainz Track Id"] = mbid_track.encode('utf-8')
            if mbid_album:
                audio["----:com.apple.iTunes:MusicBrainz Album Id"] = mbid_album.encode('utf-8')
            audio.save()

        return True
    except Exception as e:
        logger.error(f"Failed to embed MBID into {file_path}: {e}")
        return False


async def process_library(music_dir: str, dry_run: bool = False):
    """Main scanning and MBID tagging loop."""
    logger.info("=" * 70)
    logger.info("VeryDisco MusicBrainz Library Tagging Tool")
    logger.info(f"Target Music Directory: {music_dir}")
    logger.info(f"Mode: {'DRY-RUN (No file modifications)' if dry_run else 'LIVE (Tags will be embedded)'}")
    logger.info("=" * 70)

    music_path = Path(music_dir)
    if not music_path.exists():
        logger.error(f"Music directory '{music_dir}' does not exist!")
        return

    # Gather audio files
    audio_files = []
    for root, _, files in os.walk(music_dir):
        for f in files:
            if f.lower().endswith((".mp3", ".flac", ".m4a")):
                audio_files.append(Path(root) / f)

    total_files = len(audio_files)
    logger.info(f"Found {total_files} audio files in library.\n")

    tagged_count = 0
    skipped_count = 0
    failed_count = 0

    for idx, file_path in enumerate(audio_files, 1):
        rel_path = file_path.relative_to(music_path)
        artist, title, album, existing_mbid = extract_audio_tags(file_path)

        # Fallback to path info if tags missing
        if not artist or not title:
            parts = rel_path.parts
            if len(parts) >= 2:
                artist = artist or parts[0]
            title = title or file_path.stem

        if existing_mbid:
            logger.info(f"[{idx}/{total_files}] [SKIP] MBID present ({existing_mbid[:8]}...): {artist} - {title}")
            skipped_count += 1
            sys.stdout.flush()
            continue

        logger.info(f"[{idx}/{total_files}] [LOOKUP] Fetching MBID for: '{artist}' - '{title}' (Album: {album or 'N/A'})...")
        sys.stdout.flush()

        try:
            mb_rec = await musicbrainz_client.search_recording(artist=artist, title=title, album=album or "")
            if mb_rec and mb_rec.get("id"):
                track_mbid = mb_rec["id"]
                album_mbid = mb_rec.get("release_mbid")
                
                logger.info(f"[{idx}/{total_files}] [FOUND] MBID: {track_mbid} | Match: {mb_rec.get('artist')} - {mb_rec.get('title')}")
                
                if not dry_run:
                    success = embed_mbid_into_file(file_path, track_mbid, album_mbid)
                    if success:
                        logger.info(f"[{idx}/{total_files}] [SUCCESS] Embedded MBID into: {rel_path}")
                        tagged_count += 1
                    else:
                        failed_count += 1
                else:
                    logger.info(f"[{idx}/{total_files}] [DRY-RUN] Would embed MBID: {track_mbid}")
                    tagged_count += 1
            else:
                logger.warning(f"[{idx}/{total_files}] [NOT FOUND] No MusicBrainz match for: {artist} - {title}")
                failed_count += 1

        except Exception as e:
            logger.error(f"[{idx}/{total_files}] [ERROR] Exception looking up '{artist} - {title}': {e}")
            failed_count += 1

        sys.stdout.flush()
        await asyncio.sleep(1.0)

    logger.info("\n" + "=" * 70)
    logger.info("MBID Tagging Complete!")
    logger.info(f"Total Processed: {total_files}")
    logger.info(f"Already Tagged:  {skipped_count}")
    logger.info(f"Newly Tagged:    {tagged_count}")
    logger.info(f"Failed/NotFound: {failed_count}")
    logger.info("=" * 70)

def main():
    parser = argparse.ArgumentParser(description="Tag existing library audio files with MusicBrainz MBIDs.")
    parser.add_argument("--music-dir", type=str, help="Path to music library directory")
    parser.add_argument("--dry-run", action="store_true", help="Simulate lookup without writing tags")
    args = parser.parse_args()

    music_dir = args.music_dir
    if not music_dir:
        try:
            cm = ConfigManager("config.yaml")
            if cm.config and cm.config.paths and cm.config.paths.music_dir:
                music_dir = cm.config.paths.music_dir
        except Exception:
            pass

    if not music_dir:
        music_dir = "/music"

    asyncio.run(process_library(music_dir, dry_run=args.dry_run))

if __name__ == "__main__":
    main()
