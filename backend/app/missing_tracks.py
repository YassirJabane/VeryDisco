"""Prepare album-bound missing downloads without changing existing media."""
import asyncio
import re
from collections import Counter
from pathlib import Path
from uuid import UUID

from backend.app.media_versions import same_recording_title
from backend.app.metadata_pipeline.providers import MusicBrainzReleaseProvider
from backend.app.metadata_pipeline.tags import read_track


async def prepare_missing_tracks(db, user_id, music_dir, folder_path, release_mbid, requested):
    root, folder = Path(music_dir).resolve(), Path(folder_path).resolve()
    if folder == root or not folder.is_relative_to(root) or not folder.is_dir():
        raise PermissionError('Album folder must be inside your music library.')
    rows = await db.query_library_index_complete(user_id)
    owned = [row for row in rows if Path(row['filepath']).resolve().is_relative_to(folder)]
    if not owned:
        raise PermissionError('Album is not in your indexed library. Refresh the library first.')
    local = []
    for row in owned:
        path = Path(row['filepath'])
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise PermissionError('Album contains a path outside your library.')
        if path.is_file():
            physical = path.resolve().parent
            if re.fullmatch(r'(?i)(?:disc|cd|disk)[ _.-]*\d+', physical.name):
                physical = physical.parent
            if physical != folder:
                raise ValueError('Select the actual album folder, not its artist or parent directory.')
            local.append(await asyncio.to_thread(read_track, path))
    if not local:
        raise ValueError('Album has no readable indexed files.')
    try:
        identities = {str(UUID(t['release_mbid'])) for t in local if t.get('release_mbid')}
        release_mbid = str(UUID(release_mbid)) if release_mbid else None
    except (ValueError, TypeError):
        raise ValueError('Album contains invalid release identifiers. Verify the album first.')
    if len(identities) > 1:
        raise ValueError('Album contains mixed releases. Review its metadata before filling missing tracks.')
    pinned = next(iter(identities), release_mbid or '')
    if release_mbid and pinned != release_mbid:
        raise ValueError('Selected release differs from the existing album. Refresh the album first.')
    try:
        pinned = str(UUID(pinned))
    except (ValueError, TypeError):
        raise ValueError('A verified MusicBrainz release is required. Verify the album first.')
    provider = MusicBrainzReleaseProvider(db.get_provider_cache, db.set_provider_cache)
    release = await provider.release_details(pinned)
    if not release or release.get('release_mbid') != pinned:
        raise ValueError('The selected release could not be verified; no downloads were queued.')
    recording_ids = {t['recording_mbid'] for t in release['tracks'] if t.get('recording_mbid')}
    if any(t.get('recording_mbid') and t['recording_mbid'] not in recording_ids for t in local):
        raise ValueError('Existing recordings do not all belong to the selected release.')
    album_names = {t['album'] for t in local if t.get('album')}
    artist_names = {t['album_artist'] for t in local if t.get('album_artist')}
    if len(album_names) != 1 or len(artist_names) > 1:
        raise ValueError('Existing album tags are inconsistent. Review them before downloading missing tracks.')
    album = next(iter(album_names))
    album_artist = next(iter(artist_names), release['album_artist'])
    album_artists = next((t['album_artists'] for t in local if t.get('album_artists')), [album_artist])
    album_artist_ids = next((t['album_artist_mbids'] for t in local if t.get('album_artist_mbids')),
                            [a['mbid'] for a in release.get('album_artists', []) if a.get('mbid')])
    dates = Counter(t['date'] for t in local if t.get('date'))
    date = dates.most_common(1)[0][0] if dates else release.get('date')
    prepared, seen = [], set()
    for item in requested:
        matches = [t for t in release['tracks'] if same_recording_title(item.title, t['title'])
                   and (item.track_number is None or item.track_number == t['track'])
                   and (item.disc_number is None or item.disc_number == t['disc'])
                   and (not item.recording_mbid or item.recording_mbid == t['recording_mbid'])]
        if len(matches) != 1 or not matches[0].get('recording_mbid'):
            raise ValueError(f'Cannot uniquely locate {item.title!r} in the selected release.')
        track = matches[0]
        identity = (track['disc'], track['track'])
        if identity in seen:
            continue
        seen.add(identity)
        if any(t.get('recording_mbid') == track['recording_mbid'] or (
            t.get('disc') == track['disc'] and t.get('track') == track['track']
            and same_recording_title(t.get('title', ''), track['title'])) for t in local):
            continue
        disc_dirs = {Path(t['path']).parent for t in local if t.get('disc') == track['disc']
                     and Path(t['path']).parent != folder
                     and re.fullmatch(r'(?i)(?:disc|cd|disk)[ _.-]*\d+', Path(t['path']).parent.name)}
        if len(disc_dirs) > 1:
            raise ValueError('Ambiguous disc folders; review the album layout first.')
        has_root_disc = any(t.get('disc') == track['disc'] and Path(t['path']).parent == folder for t in local)
        destination = next(iter(disc_dirs)) if disc_dirs else (
            folder if has_root_disc or release['disc_total'] <= 1 else folder / f"Disc {track['disc']:02d}")
        prepared.append(dict(
            artist=track.get('artist') or album_artist, title=track['title'], album=album,
            mbid_album_override=pinned,
            metadata_override=dict(
                artist=track.get('artist') or album_artist, title=track['title'], album=album,
                album_artist=album_artist, album_artists=album_artists,
                credited_artists=[a['name'] for a in track.get('artists', [])],
                track_num=track['track'], track_total=release['track_total'],
                disc_num=track['disc'], disc_total=release['disc_total'],
                mbid_album=pinned, mbid_recording=track['recording_mbid'], date=date,
                metadata_extras={**({'release_group_mbid': [release['release_group_mbid']]} if release.get('release_group_mbid') else {}),
                                 **({'album_artist_mbids': album_artist_ids} if album_artist_ids else {}),
                                 **({'artist_mbids': [a['mbid'] for a in track.get('artists', []) if a.get('mbid')]}
                                    if track.get('artists') else {})},
                album_root=str(folder), album_disc_dir=str(destination))))
    return prepared
