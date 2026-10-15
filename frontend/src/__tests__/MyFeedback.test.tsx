import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor, act } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { MyFeedback } from '../components/MyFeedback';
import { resolveTrackArtwork } from '../trackArtwork';

vi.mock('../api', () => ({ apiService: {
  getFeedback: vi.fn().mockResolvedValue({ feedback: [
    { artist: 'Artist A', title: 'Same title', score: 1 },
    { artist: 'Artist B', title: 'Same title', score: 1 },
  ] }),
  checkAlbumsBatch: vi.fn().mockResolvedValue([]),
  likeTrack: vi.fn().mockResolvedValue({}),
} }));
vi.mock('../trackArtwork', () => ({ resolveTrackArtwork: vi.fn(), trackKey: (t: any) => `${t.artist}:${t.title}` }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

test('removing a rating preserves other artists and late artwork cannot resurrect it', async () => {
  let finishArtwork!: (value: Map<string, string>) => void;
  vi.mocked(resolveTrackArtwork).mockImplementationOnce(() => new Promise(resolve => { finishArtwork = resolve; }));
  render(<MyFeedback />);
  expect(await screen.findByText('Artist A')).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole('button', { name: 'Remove Love (Unlike)' })[0]);
  await waitFor(() => expect(screen.queryByText('Artist A')).not.toBeInTheDocument());
  expect(screen.getByText('Artist B')).toBeInTheDocument();
  await act(async () => finishArtwork(new Map([['Artist A:Same title', '/a.jpg']])));
  expect(screen.queryByText('Artist A')).not.toBeInTheDocument();
  expect(screen.getByText('Artist B')).toBeInTheDocument();
});
