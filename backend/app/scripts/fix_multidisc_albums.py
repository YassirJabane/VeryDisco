import os
import sys
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from backend.app.config import ConfigManager
from backend.app.logger import setup_logging
from backend.app.clients.deezer import DeezerClient
from backend.app.album_sync import fetch_track_metadata_with_fallback
from backend.app.sync import (
    resolve_album_dir,
    get_library_filename,
    embed_metadata,
    sanitize_filename
)
from backend.app.clients.navidrome import NavidromeClient

logger = setup_logging("INFO")
deezer_client = DeezerClient()

def read_file_extended_tags(file_path: Path) -> dict:
    """Return dictionary with artist, title, album, album_artist, track_num, disc_num, disc_total."""
    ext = file_path.suffix.lower()
    res = {
        "artist": None,
        "title": None,
        "album": None,
        "album_artist": None,
        "track_num": None,
        "disc_num": 1,
        "disc_total": 1,
    }
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3
            tags = ID3(str(file_path))
            if "TPE1" in tags:
                res["artist"] = str(tags["TPE1"].text[0])
            if "TIT2" in tags:
                res["title"] = str(tags["TIT2"].text[0])
            if "TALB" in tags:
                res["album"] = str(tags["TALB"].text[0])
            if "TPE2" in tags:
                res["album_artist"] = str(tags["TPE2"].text[0])
            if "TRCK" in tags:
                try:
                    raw_trck = str(tags["TRCK"].text[0])
                    res["track_num"] = int(raw_trck.split("/")[0])
                except (ValueError, IndexError):
                    pass
            if "TPOS" in tags:
                try:
                    raw_tpos = str(tags["TPOS"].text[0])
                    parts = raw_tpos.split("/")
                    res["disc_num"] = int(parts[0])
                    if len(parts) > 1:
                        res["disc_total"] = int(parts[1])
                except (ValueError, IndexError):
                    pass

        elif ext in [".flac", ".ogg"]:
            from mutagen.flac import FLAC
            audio = FLAC(str(file_path))
            res["artist"] = audio.get("artist", [None])[0]
            res["title"] = audio.get("title", [None])[0]
            res["album"] = audio.get("album", [None])[0]
            res["album_artist"] = audio.get("albumartist", audio.get("album artist", [None]))[0]
            try:
                if "tracknumber" in audio:
                    res["track_num"] = int(audio["tracknumber"][0].split("/")[0])
            except Exception:
                pass
            try:
                if "discnumber" in audio:
                    res["disc_num"] = int(audio["discnumber"][0].split("/")[0])
                if "disctotal" in audio:
                    res["disc_total"] = int(audio["disctotal"][0])
            except Exception:
                pass

        elif ext in [".m4a", ".mp4"]:
            from mutagen.mp4 import MP4
            audio = MP4(str(file_path))
            res["artist"] = audio.get("\xa9ART", [None])[0]
            res["title"] = audio.get("\xa9nam", [None])[0]
            res["album"] = audio.get("\xa9alb", [None])[0]
            res["album_artist"] = audio.get("aART", [None])[0]
            try:
                if "trkn" in audio:
                    res["track_num"] = audio["trkn"][0][0]
            except Exception:
                pass
            try:
                if "disk" in audio:
                    res["disc_num"] = audio["disk"][0][0]
                    res["disc_total"] = audio["disk"][0][1] or 1
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Failed to read extended tags for {file_path}: {e}")
        
    return res

async def fix_multidisc_library(music_dir: Path):
    """Scan music_dir for albums and organize multi-disc albums into Disc 01, Disc 02 subdirectories."""
    if not music_dir.exists():
        logger.error(f"Music directory does not exist: {music_dir}")
        return

    logger.info(f"Scanning '{music_dir}' for multi-disc albums to re-organize into 'Disc 01/Disc 02'...")
    
    albums_processed = 0
    files_reorganized = 0

    # Find all album directories (directories containing audio files)
    for root, dirs, files in os.walk(music_dir):
        root_path = Path(root)
        
        # Skip explore and internal directories
        if any(p.lower() in ["explore", "playlists", "navidrome_playlists", "staging"] for p in root_path.parts):
            continue

        audio_files = [root_path / f for f in files if Path(f).suffix.lower() in [".mp3", ".flac", ".m4a"]]
        if not audio_files:
            continue

        # Check if this album directory has multi-disc tracks or duplicate track numbers
        file_meta_list = []
        track_nums_seen = set()
        has_duplicate_track_nums = False
        has_multi_disc_tags = False

        for f_path in audio_files:
            meta = read_file_extended_tags(f_path)
            meta["file_path"] = f_path
            file_meta_list.append(meta)
            
            t_num = meta.get("track_num")
            d_num = meta.get("disc_num", 1)
            d_tot = meta.get("disc_total", 1)

            if d_num > 1 or d_tot > 1:
                has_multi_disc_tags = True

            if t_num is not None:
                key = (d_num, t_num)
                if key in track_nums_seen:
                    has_duplicate_track_nums = True
                track_nums_seen.add(key)

        # Track occurrence counter to accurately infer disc 1 vs disc 2 for duplicate track numbers
        seen_track_counts = {}
        for meta in file_meta_list:
            t_num = meta.get("track_num")
            if t_num is not None:
                seen_track_counts[t_num] = seen_track_counts.get(t_num, 0) + 1
                meta["inferred_disc_num"] = seen_track_counts[t_num]
            else:
                meta["inferred_disc_num"] = meta.get("disc_num", 1)

        max_inferred_discs = max(seen_track_counts.values()) if seen_track_counts else 1

        if not has_multi_disc_tags and max_inferred_discs < 2 and not has_duplicate_track_nums:
            continue

        logger.info(f"Found Multi-Disc album in '{root_path}' ({len(audio_files)} tracks). Re-organizing into 'Disc 01/Disc 02'...")
        albums_processed += 1

        for meta in file_meta_list:
            f_path = meta["file_path"]
            artist = meta.get("artist") or root_path.parent.name
            title = meta.get("title") or f_path.stem
            album = meta.get("album") or root_path.name

            # Fetch canonical metadata to guarantee disc_num and track_num accuracy
            try:
                meta_res = await fetch_track_metadata_with_fallback(deezer_client, artist, title, album)
                dz_title = meta_res.get("title") or title
                dz_artist = meta_res.get("artist") or artist
                dz_album_artist = meta_res.get("album_artist") or meta.get("album_artist") or artist
                dz_album = meta_res.get("album") or album
                track_num = meta_res.get("track_num") or meta.get("track_num")
                track_total = meta_res.get("track_total")
                
                mb_disc_num = meta_res.get("disc_num")
                mb_disc_total = meta_res.get("disc_total")

                # If MB gave multi-disc info (> 1), use MB. Otherwise use tag / inferred disc number
                if mb_disc_total and mb_disc_total > 1:
                    disc_num = mb_disc_num or 1
                    disc_total = mb_disc_total
                elif meta.get("disc_num") and meta.get("disc_num") > 1:
                    disc_num = meta["disc_num"]
                    disc_total = max(2, meta.get("disc_total", 2))
                elif max_inferred_discs > 1:
                    disc_num = meta.get("inferred_disc_num", 1)
                    disc_total = max_inferred_discs
                else:
                    disc_num = 1
                    disc_total = 1

                mbid_album = meta_res.get("mbid_album")
                mbid_recording = meta_res.get("mbid_recording")
                cover_bytes = meta_res.get("cover_bytes")
                dz_date = meta_res.get("date")

                # Resolve target album dir (will create Disc 01 / Disc 02 if disc_total > 1)
                target_folder, safe_artist, safe_album = resolve_album_dir(
                    music_dir, dz_artist, dz_album, dz_album_artist, disc_num=disc_num, disc_total=disc_total
                )
                safe_filename = get_library_filename(dz_artist, safe_album, track_num, dz_title, f_path.suffix)
                dest_path = target_folder / safe_filename

                # Read existing lyrics sidecar
                lyrics_text = None
                lrc_path = f_path.with_suffix(".lrc")
                if lrc_path.exists():
                    try:
                        with open(lrc_path, "r", encoding="utf-8") as lf:
                            lyrics_text = lf.read()
                    except Exception:
                        pass

                # Move file + lyrics
                if f_path != dest_path:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    f_path.rename(dest_path)
                    if lrc_path.exists():
                        lrc_path.rename(dest_path.with_suffix(".lrc"))

                # Embed complete tags with disc_num, disc_total, track_num, track_total, MBIDs
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
                files_reorganized += 1
                logger.info(f"Re-organized: {dest_path}")

            except Exception as err:
                logger.error(f"Failed to process track {f_path}: {err}")

    logger.info(f"Multi-disc reorganization complete! Processed {albums_processed} albums and reorganized {files_reorganized} tracks.")

async def main():
    config_path = os.getenv("CONFIG_PATH", "/data/config.yml" if os.path.exists("/data/config.yml") else "config.yml")
    cfg_mgr = ConfigManager(config_path)
    cfg = cfg_mgr.config
    music_dir = Path(getattr(cfg.paths, "music_dir", "/music"))
    
    await fix_multidisc_library(music_dir)

    # Trigger Navidrome full rescan
    if cfg.navidrome.url and cfg.navidrome.username:
        logger.info("Triggering Navidrome library full rescan...")
        nd_client = NavidromeClient(
            url=cfg.navidrome.url,
            username=cfg.navidrome.username,
            password=cfg.navidrome.password
        )
        await nd_client.trigger_rescan(full_scan=True)

if __name__ == "__main__":
    asyncio.run(main())
