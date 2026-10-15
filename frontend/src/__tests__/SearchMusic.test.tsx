import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { SearchMusic } from '../components/SearchMusic';
import { apiService } from '../api';

const notifications = vi.hoisted(() => ({ notify: vi.fn(), confirm: vi.fn() }));
vi.mock('../context/NotificationContext', () => ({ useNotification: () => notifications }));
vi.mock('../components/LyricsPreviewDialog', () => ({ default: () => null }));
vi.mock('../api', () => ({ apiService: {
  getMusicRequests: vi.fn(), searchDeezer: vi.fn(), checkAlbumsBatch: vi.fn(),
  getArtistAlbums: vi.fn(), getAlbumTracks: vi.fn(), downloadAlbum: vi.fn(),
} }));
const base = { id: 100, title: 'For All the Dogs', artist: { id: 1, name: 'Drake' }, nb_tracks: 23 };
const edition = { id: 200, title: 'For All The Dogs Scary Hours Edition', artist: base.artist, nb_tracks: 29 };
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(apiService.getMusicRequests).mockResolvedValue({ requests: [] } as any);
  vi.mocked(apiService.searchDeezer).mockResolvedValue({ data: [base] });
  vi.mocked(apiService.getArtistAlbums).mockResolvedValue({ data: [base, edition] });
  vi.mocked(apiService.checkAlbumsBatch).mockResolvedValueOnce([{ status: 'full', exists: true, upgrade_available: false }]);
  vi.mocked(apiService.downloadAlbum).mockResolvedValue({ status: 'queued', message: 'Queued' });
});
afterEach(cleanup);

async function searchAlbums(buttonName = 'Check missing tracks') {
  render(<SearchMusic />);
  fireEvent.mouseDown(screen.getByRole('combobox'));
  fireEvent.click(screen.getByRole('option', { name: 'Album' }));
  fireEvent.change(screen.getByLabelText('Search artist, track or album...'), { target: { value: 'Drake' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await screen.findByRole('button', { name: buttonName });
}
async function selectEdition() {
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Release Version' }));
  fireEvent.click(await screen.findByRole('option', { name: edition.title }));
}

test('an edition never inherits completeness from the base and waits for its own check', async () => {
  let finish!: (value: any[]) => void;
  vi.mocked(apiService.checkAlbumsBatch).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await searchAlbums();
  await selectEdition();
  expect(screen.getByRole('button', { name: 'Checking…' })).toBeDisabled();
  expect(screen.queryByText('Fully in Library')).not.toBeInTheDocument();
  await act(async () => finish([{ status: 'partial', upgrade_available: false }]));
  fireEvent.click(await screen.findByRole('button', { name: 'Complete album' }));
  await waitFor(() => expect(apiService.downloadAlbum).toHaveBeenCalledWith('Drake', edition.title, false));
  expect(apiService.checkAlbumsBatch).toHaveBeenLastCalledWith([{ artist: 'Drake', title: edition.title, album_id: 200, expected_tracks: 29 }]);
  expect(notifications.confirm).not.toHaveBeenCalled();
});

test('even a falsely full album keeps the primary action non-forced', async () => {
  await searchAlbums();
  fireEvent.click(screen.getByRole('button', { name: 'Check missing tracks' }));
  await waitFor(() => expect(apiService.downloadAlbum).toHaveBeenCalledWith('Drake', base.title, false));
  expect(notifications.confirm).not.toHaveBeenCalled();
});

test('direct Scary Hours search with only 23 of 29 tracks offers completion, never full replacement', async () => {
  vi.mocked(apiService.searchDeezer).mockResolvedValue({ data: [edition] });
  vi.mocked(apiService.checkAlbumsBatch).mockReset().mockResolvedValue([{
    status: 'partial', exists: true, upgrade_available: false, matched_tracks: 23, expected_tracks: 29,
  }]);
  await searchAlbums('Complete album');
  expect(screen.getByText('Partially in Library · 23/29')).toBeInTheDocument();
  expect(apiService.checkAlbumsBatch).toHaveBeenCalledWith([{ artist: 'Drake', title: edition.title, album_id: 200, expected_tracks: 29 }]);
  fireEvent.click(screen.getByRole('button', { name: 'Complete album' }));
  await waitFor(() => expect(apiService.downloadAlbum).toHaveBeenCalledWith('Drake', edition.title, false));
  expect(notifications.confirm).not.toHaveBeenCalled();
});

test('only the separate Replace all action can force a full download', async () => {
  await searchAlbums();
  fireEvent.click(screen.getByRole('button', { name: 'Replace all' }));
  expect(apiService.downloadAlbum).not.toHaveBeenCalled();
  expect(notifications.confirm).toHaveBeenCalledWith(expect.objectContaining({ title: 'Replace all album tracks?', isDangerous: true }));
  await act(async () => notifications.confirm.mock.calls[0][0].onConfirm());
  expect(apiService.downloadAlbum).toHaveBeenCalledWith('Drake', base.title, true);
});

test('a failed edition lookup is unknown, not inherited full or missing', async () => {
  vi.mocked(apiService.checkAlbumsBatch).mockRejectedValueOnce(new Error('Unavailable'));
  await searchAlbums();
  await selectEdition();
  expect(await screen.findByText('Availability unknown')).toBeInTheDocument();
  expect(screen.queryByText('Fully in Library')).not.toBeInTheDocument();
  expect(screen.queryByText('Not in Library')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Download' }));
  await waitFor(() => expect(apiService.downloadAlbum).toHaveBeenCalledWith('Drake', edition.title, false));
  expect(notifications.confirm).not.toHaveBeenCalled();
});

test('a late edition check cannot replace the default after switching back', async () => {
  let finish!: (value: any[]) => void;
  vi.mocked(apiService.checkAlbumsBatch).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await searchAlbums();
  await selectEdition();
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Release Version' }));
  fireEvent.click(screen.getByRole('option', { name: `${base.title} (Default)` }));
  await act(async () => finish([{ status: 'partial', upgrade_available: false }]));
  expect(screen.getByRole('button', { name: 'Check missing tracks' })).toBeEnabled();
  expect(screen.queryByText('Partially in Library')).not.toBeInTheDocument();
});
