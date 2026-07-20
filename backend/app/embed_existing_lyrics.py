import os
import sys
from pathlib import Path
from backend.app.config import ConfigManager
from backend.app.sync import embed_lyrics
from backend.app.logger import get_logger

logger = get_logger()

def main():
    # Load config
    config_path = os.environ.get("VERYDISCO_CONFIG", "/app/config.yml")
    if not os.path.exists(config_path):
        config_path = "config.yml"
    
    manager = ConfigManager(config_path)
    if not manager.is_configured or not manager.config:
        print(f"Error: app is not configured or config file not found at {config_path}")
        sys.exit(1)
        
    config = manager.config
    music_dir = config.paths.music_dir
    playlists_dir = config.paths.navidrome_playlists_dir
    
    dirs_to_scan = [music_dir, playlists_dir]
    print(f"Scanning directories for existing .lrc files to embed: {dirs_to_scan}")
    
    embedded_count = 0
    skipped_count = 0
    error_count = 0
    
    for base_dir in dirs_to_scan:
        if not os.path.exists(base_dir):
            print(f"Warning: Directory {base_dir} does not exist. Skipping.")
            continue
            
        for root, _, files in os.walk(base_dir):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in [".mp3", ".flac", ".m4a", ".mp4", ".ogg"]:
                    audio_path = os.path.join(root, file)
                    audio_path_obj = Path(audio_path)
                    lrc_path = audio_path_obj.with_suffix(".lrc")
                    
                    if lrc_path.is_file():
                        try:
                            with open(lrc_path, "r", encoding="utf-8") as f:
                                lyrics_text = f.read().strip()
                            
                            if lyrics_text:
                                embed_lyrics(audio_path, lyrics_text)
                                embedded_count += 1
                            else:
                                skipped_count += 1
                        except Exception as e:
                            print(f"Error embedding lyrics in {audio_path}: {e}")
                            error_count += 1
                    else:
                        skipped_count += 1

    print(f"\nEmbedding complete!")
    print(f"Total files updated: {embedded_count}")
    print(f"Total files skipped (no .lrc found or empty): {skipped_count}")
    print(f"Errors encountered: {error_count}")

if __name__ == "__main__":
    main()
