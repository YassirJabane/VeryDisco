import { apiService } from './api';
import { thumbnailUrl } from './thumbnail';

type Track = { artist: string; title: string; album?: string; [key: string]: any };
const cache = new Map<string, { expiresAt: number; result: Promise<string> }>();
const normalize = (value: string) => value.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
export const trackKey = (track: Track) => `${track.recording_mbid || track.mbid || `${track.artist}\0${track.title}`}\0${track.album || ''}`;

function coverFor(track: Track): Promise<string> {
  const key = trackKey(track);
  const cached = cache.get(key);
  if (cached && cached.expiresAt > Date.now()) return cached.result;
  const result = apiService.searchDeezer(`${track.artist} ${track.title}`, 'track')
    .then(response => {
      const candidate = (response.data || []).find((item: any) =>
        normalize(item.artist?.name || '') === normalize(track.artist) &&
        normalize(item.title || '') === normalize(track.title) &&
        (!track.album || normalize(item.album?.title || '') === normalize(track.album))
      );
      return thumbnailUrl(candidate?.album?.cover_medium || candidate?.album?.cover_small || '');
    }).catch(() => '');
  if (cache.size >= 1000) cache.delete(cache.keys().next().value!);
  cache.set(key, { expiresAt: Date.now() + 3600000, result });
  return result;
}

export async function resolveTrackArtwork(tracks: Track[]): Promise<Map<string, string>> {
  const covers = new Map<string, string>();
  // Resolve every missing cover, with bounded concurrency. Cached promises also
  // coalesce polling and requests from another mounted page.
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(4, tracks.length) }, async () => {
    while (next < tracks.length) {
      const track = tracks[next++];
      if (track.artwork || track.cover_medium || track.cover_small || track.cover || track.image_url) continue;
      covers.set(trackKey(track), await coverFor(track));
    }
  }));
  return covers;
}
