# VeryDisco-MD 🎵

**VeryDisco-MD** is a Dockerized homelab synchronization service that automatically retrieves your weekly ListenBrainz personalized playlists ("Weekly Exploration" or "Weekly Jams"), resolves them dynamically, searches and downloads tracks from Soulseek (via `slskd`), retrieves synchronized lyrics files (`.lrc`), and presents a premium Web Dashboard styled in modern Material Design (MUI v5).

---

## Technical Features
- **FastAPI backend**: Fully async network stack using `httpx`, with `APScheduler` for internal cron triggers.
- **React + Material UI (MUI v5) Frontend**: Single-page application bundle, styled with premium dark/light toggles and fluid micro-animations, compiled via Vite and served directly by the FastAPI container.
- **Dynamic Playlist Resolution**: Pulls weekly recommendations by traversing ListenBrainz JSPF endpoints, eliminating static playlist ID hacks.
- **Advanced Peer Ranking**: Intelligent peer selection based on upload slot availability (`hasFreeUploadSlot`) ➔ shortest queue length (`queueLength`) ➔ highest audio bitrate ➔ maximum transfer speed.
- **Synced Lyrics Fallback**: Pulls matching lyrics from `lrclib` (preferring `.lrc` synced lyrics format and falling back to `.txt` plain lyrics format).
- **SQLite Database**: Persists history of past sync runs, individual track sync states, and structured system logs inside a shared persistent volume.
- **Config Hot-Reloading**: Edit settings in the UI or raw YAML, save them, and they are hot-reloaded in-memory without container restarts.
- **Atomic staging swaps**: Downloads tracks into a temporary staging folder first, copy-merging previously completed songs, and renames the staging folder atomically to prevent half-finished sync folders.

---

## Directory Structure

```
veryDisco/
├── Dockerfile               # Multi-stage secure build
├── docker-compose.yml       # Docker Compose setup
├── config.example.yml       # Config template
├── README.md                # Documentation
├── backend/
│   ├── app/                 # FastAPI backend & sync daemon
│   └── requirements.txt     # Python requirements
└── frontend/
    ├── package.json         # Node configurations
    └── src/                 # React UI code
```

---

## Quick Start (Docker)

1. **Clone or Copy** the project files to your server directory.
2. **Create the data directories**:
   ```bash
   mkdir -p data slskd_downloads
   ```
3. **Configure Soulseek Download Path**:
   Set the environment variable or edit `docker-compose.yml` to specify your `slskd` downloads folder.
   For example, create a `.env` file:
   ```env
   SLSKD_DOWNLOADS_HOST_PATH=/home/user/appdata/slskd/downloads
   ```
4. **Build and Run**:
   ```bash
   docker compose up -d --build
   ```
5. **Access the Web UI**:
   Open `http://localhost:8080` in your browser. Upon first boot, a default `config.yml` will be generated in your directory. Follow the screen prompt to complete your configuration!

---

## Configuration (`config.yml` Schema)

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **`listenbrainz.username`** | String | *Required* | Your ListenBrainz profile name. |
| **`listenbrainz.playlist_source`** | String | `weekly-exploration` | Target playlist source (`weekly-exploration` or `weekly-jams`). |
| **`listenbrainz.token`** | String | `""` | Account auth token (required only for private playlists). |
| **`slskd.base_url`** | String | *Required* | Base HTTP URL where your slskd daemon is running. |
| **`slskd.api_key`** | String | `""` | API key if authentication is enabled on your slskd instance. |
| **`slskd.downloads_dir`** | String | `/slskd_downloads` | Path inside the VeryDisco container matching your slskd download volume mount. |
| **`slskd.min_bitrate`** | Integer | `320` | Minimum audio bitrate filtering for search results. |
| **`lyrics.provider`** | String | `lrclib` | Lyrics provider (currently defaults to `lrclib`). |
| **`lyrics.base_url`** | String | `https://lrclib.net` | Base URL of the lyrics lookup endpoint. |
| **`schedule.cron`** | String | `0 3 * * 1` | Cron trigger configuration (defaults to every Monday at 03:00 UTC). |
| **`schedule.run_on_startup`** | Boolean | `true` | Runs the synchronization routine instantly on application start. |
| **`schedule.batch_size`** | Integer | `5` | Concurrency limit of parallel downloads/searches. |
| **`schedule.max_candidate_attempts`**| Integer| `3` | Attempts other search matches if the first choice fails. |
| **`paths.weekly_output_dir`** | String | `/data/weekly/current` | Staging folder where completed files are finalized. |
| **`timeouts.http_seconds`** | Integer | `20` | HTTP request timeouts. |
| **`timeouts.search_seconds`** | Integer | `30` | Timeout window for slskd search completions. |
| **`timeouts.download_seconds`** | Integer | `240` | Timeout window for slskd download transfers. |
| **`log_level`** | String | `INFO` | Output logger details (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |

---

## Troubleshooting

### Volume Mount Matching (The Most Common Issue ⚠️)
For VeryDisco-MD to find downloaded audio files, **both VeryDisco-MD and slskd containers must mount the exact same physical folder** on the host. 
- In your `slskd` configuration, the downloads folder might be mapped to a path on your host (e.g. `/home/user/music/downloads`).
- In `docker-compose.yml`, mount that exact host path to `/slskd_downloads` inside VeryDisco-MD:
  ```yaml
  volumes:
    - /home/user/music/downloads:/slskd_downloads
  ```
- Ensure `slskd.downloads_dir` in `config.yml` is set to `/slskd_downloads`.
- When `slskd` marks a download as complete, VeryDisco-MD scans `/slskd_downloads` recursively for the file matching the exact size. If permissions are restricted (read-only) or paths do not align, files cannot be moved.

### File Staging Permissions
Because the container runs under a non-root user (`appuser`, UID/GID `10001`) for enhanced security, make sure the host folders mounted for `./data` and `./config.yml` are writeable by `10001` or run:
```bash
chown -R 10001:10001 ./data
```
Otherwise, the backend will fail to write `verydisco.db` or update `config.yml`.
