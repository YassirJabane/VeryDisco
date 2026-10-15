import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import apiService from '../api';
import MetadataRebuild from '../components/MetadataRebuild';

vi.mock('../api', () => ({
  default: { getMetadataRebuildStatus: vi.fn(), getMetadataRebuildAlbums: vi.fn(), startMetadataRebuildScan: vi.fn() },
  getErrorMessage: (_error: unknown, fallback: string) => fallback,
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(apiService.getMetadataRebuildStatus).mockResolvedValue({ status: 'completed' });
  vi.mocked(apiService.startMetadataRebuildScan).mockResolvedValue({ status: 'started' });
  vi.mocked(apiService.getMetadataRebuildAlbums).mockResolvedValue([{
    id: 'album', album_artist: 'Artist', album: 'Album', folder: '/music/Artist/Album',
    confidence: 1, state: 'planned', review: 'automatic', matched_tracks: 3, total_tracks: 3,
    selected_release: { release_mbid: 'release', album: 'Album', album_artist: 'Artist' },
    candidates: [{ release_mbid: 'release', album: 'Album', album_artist: 'Artist', track_total: 3 }],
    bpm_summary: { missing: 2, proposed: 1, unresolved: 1 },
    tracks: [
      { path: '/music/one.mp3', current: { title: 'One', bpm: [] }, proposed: { title: 'One', bpm: ['120'] }, bpm_lookup: { status: 'found', source: 'AcousticBrainz audio analysis' } },
      { path: '/music/two.mp3', current: { title: 'Two', bpm: [] }, proposed: { title: 'Two' }, bpm_lookup: { status: 'not_found' } },
      { path: '/music/three.mp3', current: { title: 'Three', bpm: ['90'] }, proposed: { title: 'Three', bpm: ['90'] }, bpm_lookup: { status: 'preserved' } },
    ],
  }]);
});
afterEach(cleanup);

test('shows missing BPM, reviewed estimates and preserved values without false deletion changes', async () => {
  render(<MetadataRebuild />);
  expect(await screen.findByText('BPM at scan: 2 missing · 1 proposed · 1 unresolved')).toBeInTheDocument();
  fireEvent.click(screen.getByText('Artist — Album'));
  expect(screen.getByText('120 BPM proposed')).toBeInTheDocument();
  expect(screen.getByText('BPM missing · no archive data')).toBeInTheDocument();
  expect(screen.getByText('90 BPM · preserved')).toBeInTheDocument();
  expect(screen.getByText('bpm: ∅ → 120')).toBeInTheDocument();
  expect(screen.queryByText(/90 → ∅/)).not.toBeInTheDocument();
});

test('passes the optional BPM lookup setting when starting a new scan', async () => {
  render(<MetadataRebuild />);
  await screen.findByText('Artist — Album');
  fireEvent.click(screen.getByLabelText('Look up missing BPM'));
  fireEvent.click(screen.getByRole('button', { name: 'Scan entire library' }));
  await waitFor(() => expect(apiService.startMetadataRebuildScan).toHaveBeenCalledWith(false, false));
});
