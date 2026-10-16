"""User-scoped, sequential lyrics backfill and plain-to-synced upgrades."""
import asyncio
import os
import re
import tempfile
from pathlib import Path

import httpx
from backend.app.clients.lrclib import LrcLibClient
from backend.app.logger import get_logger

TIMESTAMP = re.compile(r'\[\d{1,3}:\d{2}(?:\.\d+)?\]')


def new_progress():
    return dict(status='running', total=0, processed=0, synced=0, plain=0,
                not_found=0, skipped=0, failed=0, current='', error=None)


def snapshot(audio, root):
    paths = [audio, audio.with_suffix('.lrc'), audio.with_suffix('.txt')]
    if any(not path.resolve().is_relative_to(root) or path.is_symlink() for path in paths):
        raise ValueError('Path outside music directory')
    stats = tuple((p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None for p in paths)
    if stats[0] is None:
        raise ValueError('Audio file missing')
    texts = [p.read_text(encoding='utf-8-sig') for p in paths[1:] if p.exists()]
    return stats, any(TIMESTAMP.search(text) for text in texts), any(t.strip() for t in texts)


async def run_autosearch(db, user_id, music_dir, progress, notify, *, base_url='https://lrclib.net', timeout=20):
    root = Path(music_dir).resolve()
    client = LrcLibClient(base_url=base_url, timeout=timeout)
    updated = []
    try:
        rows = await db.query_library_lyrics_missing(user_id)
        overrides = await db.get_instrumental_overrides(user_id)
        progress['total'] = len(rows)
        for row in rows:
            progress['current'] = f"{row.get('artist') or ''} — {row.get('title') or ''}"
            try:
                audio = Path(row['filepath'])
                if str(audio) in overrides or re.search(r'(?i)\binstrumental\b', f"{row.get('title', '')} {row.get('album', '')}"):
                    progress['skipped'] += 1
                    continue
                before, synced, plain = await asyncio.to_thread(snapshot, audio, root)
                if synced:
                    await db.refresh_library_paths(user_id, [str(audio.resolve())])
                    progress['skipped'] += 1
                    continue
                if not row.get('artist') or not row.get('title'):
                    progress['skipped'] += 1
                    continue
                text, kind = await client.find_automatic(
                    row['artist'], row['title'], row.get('album') or '', row.get('duration') or 0,
                    synced_only=plain or bool(row.get('lyrics_plain')))
                if not text:
                    progress['not_found'] += 1
                    continue
                # A manual edit or instrumental marking during the request wins.
                after, _, _ = await asyncio.to_thread(snapshot, audio, root)
                if after != before or row['filepath'] in await db.get_instrumental_overrides(user_id):
                    progress['skipped'] += 1
                    continue
                target = audio.with_suffix('.lrc')
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                                     prefix='.lyrics-', suffix='.tmp', delete=False) as stream:
                        temporary = stream.name
                        stream.write(text)
                    os.replace(temporary, target)
                finally:
                    if temporary and os.path.exists(temporary):
                        os.unlink(temporary)
                updated.append(str(audio.resolve()))
                progress[kind] += 1
                await db.refresh_library_paths(user_id, [str(audio.resolve())])
            except httpx.HTTPStatusError as exc:
                get_logger().warning('Lyrics autosearch HTTP %s for %s', exc.response.status_code, row.get('filepath'))
                progress['failed'] += 1
                if exc.response.status_code == 429:
                    progress.update(status='rate_limited', error='LRCLIB rate limit reached. Retry later.')
                    break
            except httpx.RequestError as exc:
                get_logger().warning('Lyrics autosearch stopped: LRCLIB temporarily unavailable (%s)', type(exc).__name__)
                progress['failed'] += 1
                progress.update(status='service_unavailable', error='LRCLIB is temporarily unavailable. Retries exhausted; retry later. Remaining tracks were not checked.')
                break
            except Exception:
                get_logger().exception('Lyrics autosearch failed for %s', row.get('filepath'))
                progress['failed'] += 1
            finally:
                progress['processed'] += 1
        if progress['status'] == 'running':
            progress['status'] = 'completed'
    except asyncio.CancelledError:
        progress['status'] = 'cancelled'
        raise
    except Exception:
        get_logger().exception('Lyrics autosearch failed')
        progress.update(status='failed', error='Lyrics autosearch failed; see logs.')
    finally:
        progress['current'] = ''
        if updated:
            try:
                await db.delete_cache(f'missing_lyrics_{user_id}')
                await notify()
            except Exception:
                get_logger().exception('Lyrics saved, but library notification failed')
                progress['error'] = 'Lyrics saved, but library notification failed; refresh the library.'
