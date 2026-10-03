# VeryDisco 🎵

**VeryDisco** is a Dockerized homelab synchronization service that automatically retrieves your weekly ListenBrainz personalized playlists ("Weekly Exploration" or "Weekly Jams"), resolves them dynamically, searches and downloads tracks from Soulseek (via `slskd`), retrieves synchronized lyrics files (`.lrc`), and presents a premium Web Dashboard styled in modern Material Design (MUI v5).

---

## Technical Features
- **FastAPI backend**: Fully async network stack using `httpx`, with `APScheduler` for internal cron triggers.
- **React + Material UI (MUI v5) Frontend**: Single-page application bundle, styled with premium dark/light toggles and fluid micro-animations, compiled via Vite and served directly by the FastAPI container.
- **Dynamic Playlist Resolution**: Pulls weekly recommendations by traversing ListenBrainz JSPF endpoints, eliminating static playlist ID hacks.
- **Advanced Peer Ranking**: Intelligent peer selection based on upload slot availability (`hasFreeUploadSlot`) ➔ shortest queue length (`queueLength`) ➔ highest audio bitrate ➔ maximum transfer speed.
- **Synced Lyrics Fallback**: Pulls matching lyrics from `lrclib` (preferring `.lrc` synced lyrics format and falling back to `.txt` plain lyrics format).
- **SQLite Database**: Persists history of past sync runs, individual track sync states, and structured system logs inside a shared persistent volume.
- **Config Hot-Reloading**: Edit settings in the UI or raw YAML, save them, and they are hot-reloaded in-memory without container restarts.
- **Recoverable playlist updates**: Downloads are prepared in a staging folder, then promoted with directory renames while the previous generation is kept for rollback. A crash during the swap is recovered on startup; keep independent backups of your playlist volume.

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
   mkdir -p data slskd_downloads music navidrome_playlists
   cp config.example.yml config.yml
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
   Open `http://localhost:8086` in your browser. The published port is bound to loopback by default. Complete the setup screen using a Navidrome admin account. For remote access, use an HTTPS reverse proxy and set `auth.cookie_secure: true`; do not publish the service directly to an untrusted network.

   If the UI is hosted on a separate origin, set `CORS_ORIGINS` to an explicit comma-separated list of trusted origins (for example `https://music.example.org`). Keep the API and UI on the same origin when possible. Existing users with uninitialized or formerly unrestricted library paths must log in again after correcting those paths within the configured volume roots.

---

## Configuration (`config.yml` Schema)

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **`listenbrainz.username`** | String | *Required* | Your ListenBrainz profile name. |
| **`listenbrainz.active_playlists`** | String list | `['weekly-exploration']` | Playlists to synchronize. |
| **`listenbrainz.token`** | String | `""` | Account auth token (required only for private playlists). |
| **`slskd.base_url`** | String | *Required* | Base HTTP URL where your slskd daemon is running. |
| **`slskd.api_key`** | String | `""` | API key if authentication is enabled on your slskd instance. |
| **`slskd.downloads_dir`** | String | `/slskd_downloads` | Path inside the VeryDisco container matching your slskd download volume mount. |
| **`slskd.audio_quality`** | Object | Lossless profile | Ordered acceptable audio variants. |
| **`lyrics.provider`** | String | `lrclib` | Lyrics provider (currently defaults to `lrclib`). |
| **`lyrics.base_url`** | String | `https://lrclib.net` | Base URL of the lyrics lookup endpoint. |
| **`schedule.daily_time`, `schedule.weekly_time`, `schedule.weekly_day`** | Strings | `04:00`, `04:00`, `tue` | Daily and weekly trigger times and weekly day. |
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

### Development images and releases

The Compose file defaults to `ghcr.io/yassirjabane/verydisco:beta`. A push to the `beta` branch runs backend/frontend tests and, only if they pass, publishes `:beta`. A push to `main` runs tests without publishing an image. A `vX.Y.Z` tag pointing to a commit already on `main` publishes both `:vX.Y.Z` and `:latest`; do not tag a beta-only commit as stable. Pull requests to `beta` or `main` run the same tests. For a deployed installation, back up `data/`, `config.yml`, and the mounted music/playlists before changing tags.

To use the published beta image, run `docker compose pull` and `docker compose up -d --no-build`. For a stable version, set `VERYDISCO_TAG=vX.Y.Z` in `.env` and run the same commands. `docker compose up -d --build` builds the local source instead of pulling the published image.

To prepare a release from a real Git clone (this distributed source snapshot has no `.git`), commit and push the changes to `beta`, verify the beta image in Docker with real slskd/Navidrome services, merge the verified commit into `main`, and only then create/push an annotated `vX.Y.Z` tag on that main commit. Do not push `latest` while downloads and migrations remain unverified in a real deployment.

The new Music Requests page keeps per-user requests and lets administrators approve or decline them. Search Music offers a Request button as well as the existing direct Download action. Requests are a first step toward a full Seerr-like workflow; notifications, calendars, subscriptions, full provider search parity, and robust reboot recovery for in-flight requests are not yet implemented.

### Volume Mount Matching (The Most Common Issue ⚠️)
For VeryDisco to find downloaded audio files, **both VeryDisco and slskd containers must mount the exact same physical folder** on the host. 
- In your `slskd` configuration, the downloads folder might be mapped to a path on your host (e.g. `/home/user/music/downloads`).
- In `docker-compose.yml`, mount that exact host path to `/slskd_downloads` inside VeryDisco:
  ```yaml
  volumes:
    - /home/user/music/downloads:/slskd_downloads
  ```
- Ensure `slskd.downloads_dir` in `config.yml` is set to `/slskd_downloads`.
- When `slskd` marks a download as complete, VeryDisco scans `/slskd_downloads` recursively for the file matching the exact size. If permissions are restricted (read-only) or paths do not align, files cannot be moved.

### File Staging Permissions
Because the container runs under a non-root user (`appuser`, UID/GID `1000`), make sure the host folders mounted for `./data`, `./navidrome_playlists` and `./config.yml` are writable by `1000` or run:
```bash
chown -R 1000:1000 ./data ./navidrome_playlists
chown 1000:1000 ./config.yml
```
Otherwise, the backend will fail to write `verydisco.db`, its persistent session key, or update `config.yml`.
