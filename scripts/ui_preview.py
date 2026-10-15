"""Read-only, loopback-only visual QA with synthetic data, never production APIs.

Build frontend first, then run: python scripts/ui_preview.py
This server cannot submit mutations or access a real library/account.
"""
import json
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
NOW = datetime.now(timezone.utc).isoformat()


def fixtures():
    sources = ['daily-jams', 'weekly-jams', 'weekly-exploration']
    runs = {source: {'id': index + 1, 'timestamp': NOW, 'status': 'completed',
        'tracks_found': 50, 'tracks_downloaded': 15 + index * 5, 'tracks_skipped': 34 - index * 5,
        'tracks_failed': 1, 'error_message': None} for index, source in enumerate(sources)}
    return {
        '/api/status': {'is_configured': True, 'validation_errors': None, 'is_syncing': False,
            'next_run': None, 'progress': {'status': 'idle'}, 'latest_run': runs[sources[0]], 'latest_runs': runs, 'active_playlists': sources[:2]},
        '/api/auth/me': {'id': 'preview', 'username': 'preview', 'displayName': 'UI Preview', 'isAdmin': True, 'musicDir': '/preview'},
        '/api/navidrome/stats': {'songs': 12840, 'albums': 946, 'artists': 382, 'available': True},
        '/api/lyrics/missing': [{'artist': 'Preview Artist', 'album': 'Preview Album', 'title': 'A song without timestamps', 'duration': 240, 'filepath': '/preview/example.mp3', 'lyrics_status': 'plain'}],
        '/api/metadata-rebuild/status': {'status': 'idle'}, '/api/metadata-rebuild/albums': [],
        '/api/library/albums': [], '/api/playlists/imported': [],
    }


class PreviewHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith('/api/'):
            dataset = fixtures()
            if path not in dataset:
                self.send_error(404, 'No preview fixture for this endpoint')
                return
            data = json.dumps(dataset[path]).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if not Path(self.translate_path(path)).is_file():
            self.path = '/index.html'
        super().do_GET()

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    if not (ROOT / 'index.html').exists():
        raise SystemExit('Build frontend first')
    print('Synthetic read-only UI preview: http://127.0.0.1:3011', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 3011), partial(PreviewHandler, directory=str(ROOT))).serve_forever()
