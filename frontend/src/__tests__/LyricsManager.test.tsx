import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import apiService from '../api';
import { LyricsManager } from '../components/LyricsManager';

vi.mock('../api', () => ({ default: { getMissingLyrics: vi.fn() } }));
vi.mock('../components/LyricsPreviewDialog', () => ({
  default: ({ onSaveSuccess }: { onSaveSuccess: () => void }) => <button onClick={onSaveSuccess}>Save lyrics test</button>,
}));
afterEach(cleanup);

test('plain lyrics remain eligible for a synced upgrade after saving plain text', async () => {
  vi.mocked(apiService.getMissingLyrics).mockResolvedValue([{
    artist: 'Artist', title: 'Song', album: 'Album', filepath: '/music/song.mp3', duration: 200, lyrics_status: 'plain',
  }]);
  render(<LyricsManager />);
  expect(await screen.findByText('Plain · upgrade to synced')).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole('button', { name: /Find Lyrics/i })[0]);
  fireEvent.click(screen.getByText('Save lyrics test'));
  await waitFor(() => expect(apiService.getMissingLyrics).toHaveBeenCalledTimes(2));
  expect(await screen.findByText('Plain · upgrade to synced')).toBeInTheDocument();
});
