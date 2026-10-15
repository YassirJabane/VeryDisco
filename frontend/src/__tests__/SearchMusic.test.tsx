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

async function searchAlbums() {
  render(<SearchMusic />);
  fireEvent.mouseDown(screen.getByRole('combobox'));
  fireEvent.click(screen.getByRole('option', { name: 'Album' }));
  fireEvent.change(screen.getByLabelText('Search artist, track or album...'), { target: { value: 'Drake' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await screen.findByRole('button', { name: 'Re-download' });
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

test('a genuinely complete album requires an explicit re-download confirmation', async () => {
  await searchAlbums();
  fireEvent.click(screen.getByRole('button', { name: 'Re-download' }));
  expect(apiService.downloadAlbum).not.toHaveBeenCalled();
  expect(notifications.confirm).toHaveBeenCalledWith(expect.objectContaining({ title: 'Re-download Album?' }));
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
  expect(screen.getByRole('button', { name: 'Re-download' })).toBeEnabled();
  expect(screen.queryByText('Partially in Library')).not.toBeInTheDocument();
});
