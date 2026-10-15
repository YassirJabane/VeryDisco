import { describe, expect, test, vi } from 'vitest';
import { apiService } from '../api';
import { resolveTrackArtwork, trackKey } from '../trackArtwork';

vi.mock('../api', () => ({ apiService: { searchDeezer: vi.fn() } }));

describe('track artwork resolution', () => {
  test('resolves beyond the first ten tracks and reuses results on polling', async () => {
    const tracks = Array.from({ length: 12 }, (_, i) => ({ artist: 'Artist', title: `Song ${i}`, album: 'Album' }));
    vi.mocked(apiService.searchDeezer).mockImplementation(async query => ({ data: [{
      artist: { name: 'Artist' }, title: query.slice('Artist '.length), album: { title: 'Album', cover_medium: 'cover-url' },
    }] }));
    const covers = await resolveTrackArtwork(tracks);
    expect(covers.size).toBe(12);
    expect(covers.get(trackKey(tracks[11]))).toBe('cover-url');
    const callCount = vi.mocked(apiService.searchDeezer).mock.calls.length;
    await resolveTrackArtwork(tracks);
    expect(apiService.searchDeezer).toHaveBeenCalledTimes(callCount);
  });

  test('rejects the artwork of another album despite an identical song title', async () => {
    vi.mocked(apiService.searchDeezer).mockResolvedValue({ data: [{
      artist: { name: 'The Weeknd' }, title: 'Blinding Lights', album: { title: 'Remix Single', cover_medium: 'wrong-cover' },
    }] });
    const track = { artist: 'The Weeknd', title: 'Blinding Lights', album: 'After Hours' };
    const covers = await resolveTrackArtwork([track]);
    expect(covers.get(trackKey(track))).toBe('');
  });
});
