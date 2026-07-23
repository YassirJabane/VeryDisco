import logging
import os
import uuid
import yaml
from typing import Optional, Dict, Any, Tuple
from pydantic import BaseModel, Field, field_validator, ValidationError

logger = logging.getLogger(__name__)

class ListenBrainzConfig(BaseModel):
    username: str = Field(..., description="ListenBrainz username")
    active_playlists: list[str] = Field(default_factory=lambda: ["weekly-exploration"], description="List of activated auto-generated playlists to sync")
    token: str = Field("", description="Optional user token for private playlists")

# ---------------------------------------------------------------------------
# Quality Profiles
# ---------------------------------------------------------------------------
# Each profile entry describes one acceptable audio variant. The backend will
# try them in the order they appear (index 0 = highest preference).
#
# Fields per profile:
#   format       — file extension to match, e.g. "flac", "mp3", "wav"
#   min_bitrate  — minimum bitrate kbps (lossy files only), 0 = no limit
#   max_bitrate  — maximum bitrate kbps (lossy files only), 0 = no limit
#   bit_depth    — required bit depth (lossless), 0 = any
#   sample_rate  — required sample rate Hz (lossless), 0 = any
# ---------------------------------------------------------------------------

LOSSLESS_PRESETS_DEFAULT = [
    {"format": "flac", "min_bitrate": 0, "max_bitrate": 0, "bit_depth": 24, "sample_rate": 96000},
    {"format": "flac", "min_bitrate": 0, "max_bitrate": 0, "bit_depth": 24, "sample_rate": 0},
    {"format": "flac", "min_bitrate": 0, "max_bitrate": 0, "bit_depth": 0, "sample_rate": 0},
    {"format": "wav",  "min_bitrate": 0, "max_bitrate": 0, "bit_depth": 0, "sample_rate": 0},
    {"format": "alac", "min_bitrate": 0, "max_bitrate": 0, "bit_depth": 0, "sample_rate": 0},
]

STORAGE_SAVER_PRESETS_DEFAULT = [
    {"format": "mp3",  "min_bitrate": 300, "max_bitrate": 0, "bit_depth": 0, "sample_rate": 0},
    {"format": "mp3",  "min_bitrate": 192, "max_bitrate": 0, "bit_depth": 0, "sample_rate": 0},
    {"format": "m4a",  "min_bitrate": 192, "max_bitrate": 0, "bit_depth": 0, "sample_rate": 0},
    {"format": "ogg",  "min_bitrate": 192, "max_bitrate": 0, "bit_depth": 0, "sample_rate": 0},
]

class QualityProfile(BaseModel):
    """A single quality variant to match against Soulseek search results."""
    format: str = Field(..., description="File extension: flac, mp3, wav, m4a, ogg, opus, alac, ape")
    min_bitrate: int = Field(0, description="Minimum bitrate kbps, 0 = no lower limit")
    max_bitrate: int = Field(0, description="Maximum bitrate kbps, 0 = no upper limit")
    bit_depth: int = Field(0, description="Required bit depth (0 = accept any)")
    sample_rate: int = Field(0, description="Required sample rate Hz (0 = accept any)")

class AudioQualityConfig(BaseModel):
    preset: str = Field("lossless", description="Preset: lossless, storage_saver, custom")
    # custom_profiles is used when preset == 'custom'. Ordered highest-preference first.
    custom_profiles: list[QualityProfile] = Field(
        default_factory=list,
        description="Ordered list of quality profiles for the custom preset"
    )

class NavidromeConfig(BaseModel):
    url: str = Field("", description="Subsonic URL of the Navidrome instance")
    username: str = Field("", description="Username for Subsonic authentication")
    password: str = Field("", description="Password/Token for Subsonic authentication")

class SlskdConfig(BaseModel):
    base_url: str = Field(..., description="Base URL of the slskd instance")
    api_key: str = Field("", description="API key/token for slskd")
    downloads_dir: str = Field("/slskd_downloads", description="Path where slskd writes its downloads")
    audio_quality: AudioQualityConfig = Field(default_factory=AudioQualityConfig)

class LyricsConfig(BaseModel):
    provider: str = Field("lrclib", description="Lyrics provider name")
    base_url: str = Field("https://lrclib.net", description="Lyrics service API base URL")

class ScheduleConfig(BaseModel):
    daily_time: str = Field("04:00", description="Time for daily sync (HH:MM format)")
    weekly_time: str = Field("04:00", description="Time for weekly sync (HH:MM format)")
    weekly_day: str = Field("tue", description="Day of the week for weekly sync (mon, tue, wed, thu, fri, sat, sun)")
    file_checks_time: str = Field("04:00", description="Time for automated file checks (HH:MM format)")
    file_checks_day: str = Field("sun", description="Day of the week for automated file checks")
    run_on_startup: bool = Field(True, description="Execute sync immediately on application startup")
    batch_size: int = Field(5, description="Number of tracks to process concurrently")
    max_candidate_attempts: int = Field(3, description="Maximum number of search results to try downloading if previous ones fail")

class PathsConfig(BaseModel):
    weekly_output_dir: str = Field("/data/weekly/current", description="Destination directory for synchronized tracks")
    navidrome_playlists_dir: str = Field("/navidrome_playlists", description="Destination directory for Navidrome playlists")
    music_dir: str = Field("/music", description="Root directory of the music library to check for existing tracks")

class TimeoutsConfig(BaseModel):
    http_seconds: int = Field(20, description="General HTTP client timeout in seconds")
    search_seconds: int = Field(30, description="Maximum wait time for search completions")
    download_seconds: int = Field(240, description="Maximum wait time for a single track download")

class AuthConfig(BaseModel):
    secret_key: str = Field("", description="JWT signing secret. Auto-generated on first run if empty.")
    session_days: int = Field(7, description="Session cookie lifetime in days")
    cookie_secure: bool = Field(False, description="Set True only when serving over HTTPS. Defaults to False for plain http homelab access.")

class AcoustIDConfig(BaseModel):
    api_key: str = Field("", description="AcoustID API key for audio fingerprinting")

class FilenameConfig(BaseModel):
    """Naming schema for library files and folders."""
    enabled: bool = Field(True, description="Enable naming convention checks and mass-rename")
    # File pattern tokens: {artist}, {album}, {year}, {track}, {title}
    # {track} is zero-padded to 2 digits automatically
    file_pattern: str = Field(
        "{track:02d} - {title}",
        description="Filename pattern (without extension). Tokens: {track:02d}, {title}"
    )
    folder_pattern: str = Field(
        "{artist}/{year} - {album}",
        description="Subfolder path relative to music_dir. Tokens: {artist}, {year}, {album}"
    )

class ArtistAliasesConfig(BaseModel):
    """Map of artist name aliases for library grouping."""
    aliases: Dict[str, str] = Field(
        default_factory=lambda: {
            "Kanye West": "Ye",
            "Ye (formerly known as Kanye West)": "Ye",
        },
        description="Map of alias → canonical name (e.g. 'Kanye West' → 'Ye')"
    )

class AppConfig(BaseModel):
    listenbrainz: ListenBrainzConfig
    slskd: SlskdConfig
    navidrome: NavidromeConfig = Field(default_factory=NavidromeConfig)
    acoustid: AcoustIDConfig = Field(default_factory=AcoustIDConfig)
    lyrics: LyricsConfig = Field(default_factory=LyricsConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    timeouts: TimeoutsConfig = Field(default_factory=TimeoutsConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    filename: FilenameConfig = Field(default_factory=FilenameConfig)
    artist_aliases: ArtistAliasesConfig = Field(default_factory=ArtistAliasesConfig)
    log_level: str = Field("INFO", description="Console and file logging level (DEBUG, INFO, WARNING, ERROR)")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in levels:
            raise ValueError(f"log_level must be one of {levels}")
        return v.upper()


DEFAULT_TEMPLATE = """# VeryDisco Configuration File

# ListenBrainz configuration
listenbrainz:
  username: "your-username"                # Required: Your ListenBrainz username
  active_playlists:                        # Required: List of playlists to sync
    - "weekly-exploration"
  token: ""                                # Optional: Required only if your playlists are private

# slskd (Soulseek client) configuration
slskd:
  base_url: "http://slskd:5030"            # Required: Base URL of your slskd instance
  api_key: ""                              # Optional: API key/token if authentication is enabled on slskd
  downloads_dir: "/slskd_downloads"        # Required: Path where slskd downloads files (inside container volume)
  audio_quality:
    preset: "lossless"                     # Quality preset: lossless, storage_saver, custom
    custom_qualities:                      # Required if preset is custom
      - "FLAC"
      - "MP3 320"

# Navidrome/Subsonic server configuration
navidrome:
  url: ""                                  # Optional: Subsonic URL of your Navidrome server (e.g. http://navidrome:4533)
  username: ""                             # Optional: Username for Subsonic client
  password: ""                             # Optional: Password for Subsonic client

# Lyrics lookup configuration
lyrics:
  provider: "lrclib"                       # Optional: Lyrics provider name (default: "lrclib")
  base_url: "https://lrclib.net"           # Optional: Lyrics service API base URL

# Timing and behavior configuration
schedule:
  daily_time: "04:00"                      # HH:MM format (24-hour) for daily jobs
  weekly_time: "04:00"                     # HH:MM format (24-hour) for weekly jobs
  weekly_day: "tue"                        # mon, tue, wed, thu, fri, sat, sun
  file_checks_time: "04:00"                # HH:MM format for auto file checks (duplicates, album art, acoustid)
  file_checks_day: "sun"                   # Day of the week for file checks
  run_on_startup: true                     # Run sync when verydisco starts
  batch_size: 5                            # Number of tracks to process concurrently
  max_candidate_attempts: 3                # Max search results to try downloading

# File storage paths
paths:
  weekly_output_dir: "/data/weekly/current" # Optional: Path where downloaded files are organized
  navidrome_playlists_dir: "/navidrome_playlists" # Optional: Directory for Navidrome playlists
  music_dir: "/music"                      # Optional: Root music directory for hardlinking existing tracks


# Timeout configurations (in seconds)
timeouts:
  http_seconds: 20                         # Optional: General HTTP client request timeout
  search_seconds: 30                       # Optional: Maximum wait time for slskd search results
  download_seconds: 240                    # Optional: Maximum wait time for a single track download

# Logging configuration
log_level: "INFO"                         # Optional: Log level (DEBUG, INFO, WARNING, ERROR)
"""

class ConfigManager:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config: Optional[AppConfig] = None
        self.raw_yaml: str = ""
        self.validation_errors: Optional[str] = None
        self.is_configured: bool = False
        self.load()

    def _is_dir_mount(self) -> bool:
        """Returns True when Docker mounted config_path as a directory instead of a file."""
        return os.path.isdir(self.config_path)

    def generate_default(self):
        if self._is_dir_mount():
            # Cannot write — Docker created a directory at this path.
            # Operate in memory-only mode using the template.
            return
        parent_dir = os.path.dirname(self.config_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_TEMPLATE)

    def load(self) -> Tuple[bool, Optional[str]]:
        """Loads and validates config.yml. Returns (is_valid, error_message)"""
        if self._is_dir_mount():
            # Docker created a directory instead of a file mount.
            # This happens when config.yml doesn't exist on the host before compose up.
            # Operate in-memory using the default template so the UI is still reachable.
            self.is_configured = False
            self.raw_yaml = DEFAULT_TEMPLATE
            self.validation_errors = (
                "config.yml is mounted as a directory — Docker created it automatically because the file "
                "didn't exist on the host. Fix: run `docker compose down`, create the file on your host "
                "(`touch config.yml` or copy config.example.yml), then `docker compose up -d`."
            )
            return False, self.validation_errors

        if not os.path.exists(self.config_path):
            self.generate_default()
            self.is_configured = False
            self.validation_errors = "Configuration file was generated. Please configure your ListenBrainz username and slskd base URL."
            self.raw_yaml = DEFAULT_TEMPLATE
            return False, self.validation_errors

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.raw_yaml = f.read()

            data = yaml.safe_load(self.raw_yaml) or {}
            
            # Validate with Pydantic
            config = AppConfig.model_validate(data)
            
            # Additional check: default placeholders count as unconfigured
            if config.listenbrainz.username == "your-username":
                self.config = config
                self.is_configured = False
                self.validation_errors = "Please replace the placeholder username 'your-username' with your actual ListenBrainz username."
                return False, self.validation_errors

            # Navidrome URL is required for login to work
            if not config.navidrome.url:
                self.config = config
                self.is_configured = False
                self.validation_errors = "Navidrome URL is not configured. Please complete the setup."
                return False, self.validation_errors

            self.config = config
            self.validation_errors = None
            self.is_configured = True

            # Check for JWT_SECRET env var or generate session key
            env_secret = os.environ.get('JWT_SECRET')
            if env_secret:
                config.auth.secret_key = env_secret
            elif not config.auth.secret_key:
                new_key = uuid.uuid4().hex + uuid.uuid4().hex
                config.auth.secret_key = new_key
                logger.warning("JWT_SECRET should be set as an env var in production. Using generated key for this session.")

            return True, None

        except ValidationError as e:
            self.config = None
            self.is_configured = False
            self.validation_errors = str(e)
            return False, self.validation_errors
        except Exception as e:
            self.config = None
            self.is_configured = False
            self.validation_errors = f"Failed to parse YAML file: {str(e)}"
            return False, self.validation_errors

    def save(self, data: Any) -> Tuple[bool, Optional[str]]:
        """Saves dict data or raw yaml string back to YAML config and reloads."""
        if self._is_dir_mount():
            return False, (
                "Cannot save: config.yml is mounted as a directory. "
                "Run `docker compose down`, create config.yml on the host "
                "(`cp config.example.yml config.yml`), then `docker compose up -d`."
            )
        try:
            if isinstance(data, dict) and "raw_yaml" in data:
                with open(self.config_path, "w", encoding="utf-8") as f:
                    f.write(data["raw_yaml"])
            else:
                existing = {}
                if os.path.exists(self.config_path):
                    try:
                        with open(self.config_path, "r", encoding="utf-8") as f:
                            existing = yaml.safe_load(f) or {}
                    except Exception:
                        pass
                
                # Merge dictionary payload into existing config
                if isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(v, dict) and k in existing and isinstance(existing[k], dict):
                            existing[k].update(v)
                        else:
                            existing[k] = v
                else:
                    existing = data

                with open(self.config_path, "w", encoding="utf-8") as f:
                    yaml.safe_dump(existing, f, default_flow_style=False, sort_keys=False)

            # Reload
            return self.load()
        except Exception as e:
            return False, f"Failed to write config file: {str(e)}"
