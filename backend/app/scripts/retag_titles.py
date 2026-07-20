import os
import re
import asyncio
import httpx
import urllib.parse
from pathlib import Path
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from backend.app.config import ConfigManager
from backend.app.logger import setup_logging

logger = setup_logging("INFO")

def _normalize(s: str) -> str:
    return re.sub(r'[^\w]', '', s).lower()

def _result_matches(result: dict, artist: str, title: str) -> bool:
    r_artist = _normalize(result.get("artist", {}).get("name", ""))
    r_title = _normalize(result.get("title", ""))
    n_artist = _normalize(artist)
    n_title = _normalize(title)

    title_match = n_title in r_title or r_title in n_title
    if not title_match:
        return False

    artist_words = [w for w in n_artist.split() if len(w) > 2]
    artist_match = any(w in r_artist for w in artist_words) if artist_words else (n_artist in r_artist)
    return artist_match

class TitleEqualizer:
    def __init__(self, music_dir: str):
        self.music_dir = Path(music_dir)
        self.base_url = "https://api.deezer.com"

    async def get_official_title(self, client: httpx.AsyncClient, artist: str, title: str) -> str | None:
        clean_title = title.split('(')[0].split('[')[0].strip()
        clean_artist = artist.split('feat')[0].split('ft.')[0].strip()
        query = f"{clean_artist} {clean_title}"

        url = f"{self.base_url}/search?q={urllib.parse.quote(query)}&limit=5"
        try:
            resp = await client.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("data", [])
                for r in results:
                    if _result_matches(r, clean_artist, clean_title):
                        return r.get("title")
        except Exception as e:
            logger.error(f"Deezer search failed for '{artist} - {title}': {e}")
        return None

    def read_tags(self, file_path: Path) -> tuple[str, str] | None:
        ext = file_path.suffix.lower()
        try:
            if ext == ".mp3":
                audio = EasyID3(str(file_path))
                artist = audio.get("artist", [""])[0]
                title = audio.get("title", [""])[0]
                return artist, title
            elif ext == ".flac":
                audio = FLAC(str(file_path))
                artist = audio.get("artist", [""])[0]
                title = audio.get("title", [""])[0]
                return artist, title
            elif ext == ".m4a":
                audio = MP4(str(file_path))
                artist = audio.get("\xa9ART", [""])[0]
                title = audio.get("\xa9nam", [""])[0]
                return artist, title
        except Exception as e:
            logger.error(f"Failed to read tags from {file_path.name}: {e}")
        return None

    def write_title(self, file_path: Path, new_title: str):
        ext = file_path.suffix.lower()
        try:
            if ext == ".mp3":
                audio = EasyID3(str(file_path))
                audio["title"] = new_title
                audio.save()
            elif ext == ".flac":
                audio = FLAC(str(file_path))
                audio["title"] = new_title
                audio.save()
            elif ext == ".m4a":
                audio = MP4(str(file_path))
                audio["\xa9nam"] = new_title
                audio.save()
            logger.info(f"Retagged '{file_path.name}' -> Title: '{new_title}'")
        except Exception as e:
            logger.error(f"Failed to save tags to {file_path.name}: {e}")

    async def run(self):
        if not self.music_dir.exists() or not self.music_dir.is_dir():
            logger.error(f"Music directory does not exist: {self.music_dir}")
            return

        logger.info(f"Scanning music library: {self.music_dir}")
        audio_files = []
        for root, dirs, files in os.walk(self.music_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in ["playlists", "explore"]]
            for f in files:
                if f.lower().endswith((".mp3", ".flac", ".m4a")):
                    audio_files.append(Path(root) / f)

        logger.info(f"Found {len(audio_files)} audio files in library.")

        async with httpx.AsyncClient() as client:
            for filepath in audio_files:
                tags = self.read_tags(filepath)
                if not tags:
                    continue
                artist, title = tags
                if not artist or not title:
                    continue

                official = await self.get_official_title(client, artist, title)
                if official and official != title:
                    logger.info(f"Title mismatch for '{filepath.name}': Current='{title}', Deezer='{official}'")
                    self.write_title(filepath, official)
                    # Small delay to prevent hitting rate limits
                    await asyncio.sleep(0.5)

if __name__ == "__main__":
    config_path = os.getenv("CONFIG_PATH", "./config.yml")
    config_manager = ConfigManager(config_path)
    if not config_manager.is_configured or not config_manager.config:
        logger.error("Configuration file is missing or incomplete.")
        exit(1)
        
    music_dir = config_manager.config.paths.music_dir
    equalizer = TitleEqualizer(music_dir)
    asyncio.run(equalizer.run())
