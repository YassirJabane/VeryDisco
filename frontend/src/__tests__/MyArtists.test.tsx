import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import MyArtists from '../components/MyArtists';
import apiService from '../api';

const { notify } = vi.hoisted(() => ({ notify: vi.fn() }));
vi.mock('../api', () => ({ default: { getPinnedArtists: vi.fn() }, getErrorMessage: (_error: unknown, fallback: string) => fallback }));
vi.mock('../context/NotificationContext', () => ({ useNotification: () => ({ notify, confirm: vi.fn() }) }));
beforeEach(() => { vi.resetAllMocks(); vi.mocked(apiService.getPinnedArtists).mockResolvedValue([{ id: 1, artist_name: 'Existing artist', picture_url: '' }]); });
afterEach(cleanup);

test('requests an explicit library discovery refresh without hiding the current artists', async () => {
  render(<MyArtists />);
  await screen.findByText('Existing artist');
  let finish!: (data: any[]) => void;
  vi.mocked(apiService.getPinnedArtists).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: 'Update artists' }));
  expect(apiService.getPinnedArtists).toHaveBeenLastCalledWith(true);
  expect(screen.getByText('Existing artist')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Updating artists…' })).toBeDisabled();
  finish([{ id: 2, artist_name: 'Newly discovered artist', picture_url: '' }]);
  expect(await screen.findByText('Newly discovered artist')).toBeInTheDocument();
});
test('preserves the displayed list and reports a failed refresh', async () => {
  render(<MyArtists />);
  await screen.findByText('Existing artist');
  vi.mocked(apiService.getPinnedArtists).mockRejectedValueOnce(new Error('Provider unavailable'));
  fireEvent.click(screen.getByRole('button', { name: 'Update artists' }));
  await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.stringContaining('current list'), 'error'));
  expect(screen.getByText('Existing artist')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Update artists' })).toBeEnabled();
});
