import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import apiService from '../api';
import { LyricsManager } from '../components/LyricsManager';

vi.mock('../api', () => ({ default: {
  getMissingLyrics: vi.fn(), getLyricsAutosearch: vi.fn(), startLyricsAutosearch: vi.fn(),
} }));
vi.mock('../components/LyricsPreviewDialog', () => ({
  default: ({ onSaveSuccess }: { onSaveSuccess: () => void }) => <button onClick={onSaveSuccess}>Save lyrics test</button>,
}));
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(apiService.getLyricsAutosearch).mockResolvedValue({ status: 'idle' });
});
afterEach(cleanup);

test('plain lyrics remain eligible for a synced upgrade after saving plain text', async () => {
  vi.mocked(apiService.getMissingLyrics).mockResolvedValue([{
    artist: 'Artist', title: 'Song', album: 'Album', filepath: '/music/song.mp3', duration: 200, lyrics_status: 'plain',
  }]);
  render(<LyricsManager />);
  expect((await screen.findAllByText('Plain · upgrade to synced')).length).toBeGreaterThan(0);
  fireEvent.click(screen.getAllByRole('button', { name: /Find Lyrics/i })[0]);
  fireEvent.click(screen.getByText('Save lyrics test'));
  await waitFor(() => expect(apiService.getMissingLyrics).toHaveBeenCalledTimes(2));
  expect((await screen.findAllByText('Plain · upgrade to synced')).length).toBeGreaterThan(0);
});

test('autosearch starts for the entire list even when the visible list is filtered', async () => {
  vi.mocked(apiService.getMissingLyrics).mockResolvedValue([
    { artist: 'Artist', title: 'Missing', album: '', filepath: '/music/a.mp3', duration: 200, lyrics_status: 'missing' },
    { artist: 'Artist', title: 'Plain', album: '', filepath: '/music/b.mp3', duration: 200, lyrics_status: 'plain' },
  ]);
  vi.mocked(apiService.startLyricsAutosearch).mockResolvedValue({ status: 'running', total: 2, processed: 0 });
  render(<LyricsManager />);
  await screen.findAllByText('Plain');
  fireEvent.change(screen.getByPlaceholderText(/Search tracks/), { target: { value: 'Missing' } });
  fireEvent.click(screen.getByRole('button', { name: 'Autosearch all' }));
  await waitFor(() => expect(apiService.startLyricsAutosearch).toHaveBeenCalledTimes(1));
  expect(apiService.startLyricsAutosearch).toHaveBeenCalledWith();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Autosearch all' })).toBeDisabled());
  expect(screen.getByText(/Autosearch running: 0\/2/)).toBeInTheDocument();
});

test('resumes progress after reopening the page and refreshes on completion', async () => {
  vi.mocked(apiService.getMissingLyrics).mockResolvedValue([]);
  vi.mocked(apiService.getLyricsAutosearch)
    .mockResolvedValueOnce({ status: 'running', total: 2, processed: 1 })
    .mockResolvedValue({ status: 'completed', total: 2, processed: 2, synced: 2 });
  render(<LyricsManager />);
  await screen.findByText(/Autosearch running: 1\/2/);
  await waitFor(() => expect(screen.getByText(/Autosearch completed: 2\/2/)).toBeInTheDocument(), { timeout: 3500 });
  await waitFor(() => expect(apiService.getMissingLyrics).toHaveBeenCalledTimes(2));
});

test('provider outage is shown separately from missing lyrics and allows a later retry', async () => {
  vi.mocked(apiService.getMissingLyrics).mockResolvedValue([
    { artist: 'Artist', title: 'Song', album: '', filepath: '/music/a.mp3', duration: 200 },
  ]);
  vi.mocked(apiService.getLyricsAutosearch).mockResolvedValue({
    status: 'service_unavailable', total: 20, processed: 1, failed: 1, not_found: 0,
    error: 'LRCLIB is temporarily unavailable. Retries exhausted; retry later. Remaining tracks were not checked.',
  });
  render(<LyricsManager />);
  await screen.findByText(/Remaining tracks were not checked/);
  expect(screen.getByText(/0 not found/)).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Autosearch all' })).toBeEnabled());
});
