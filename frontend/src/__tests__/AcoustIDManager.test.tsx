import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { AcoustIDManager } from '../components/AcoustIDManager';
import apiService from '../api';

vi.mock('../api', () => ({ default: {
  getAcoustidStats: vi.fn(), acoustidScan: vi.fn(), recheckAcoustid: vi.fn(), replaceLibraryTrack: vi.fn(),
} }));

const stats = (status: 'mismatch' | 'unknown' | 'unavailable' = 'mismatch') => ({
  total: 1, scanned: 1, verified: 0, failed: status === 'mismatch' ? 1 : 0,
  inconclusive: status === 'mismatch' ? 0 : 1, remaining: 0, running: false,
  failures: [{ file_path: '/music/Drake/song.mp3', artist: 'Drake', title: 'Jumbotron Shit Poppin',
    album: 'Her Loss', status, reason: status === 'mismatch' ? 'AcoustID matched this audio to another song' : 'No match found',
    suggestion: 'Recheck first.', scanned_at: '' }],
});

beforeEach(() => { vi.clearAllMocks(); vi.mocked(apiService.getAcoustidStats).mockResolvedValue(stats()); });
afterEach(cleanup);

test('rechecks existing audio and removes a verified failure without queueing a replacement', async () => {
  vi.mocked(apiService.getAcoustidStats).mockResolvedValueOnce(stats()).mockResolvedValue({ ...stats(), verified: 1, failed: 0, failures: [] });
  vi.mocked(apiService.recheckAcoustid).mockResolvedValue({ status: 'verified', reason: 'Verified by MusicBrainz Recording ID match' });
  render(<AcoustIDManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'Recheck audio' }));
  await screen.findByText('Verified by MusicBrainz Recording ID match');
  await waitFor(() => expect(screen.queryByText('Drake — Jumbotron Shit Poppin')).not.toBeInTheDocument());
  expect(apiService.recheckAcoustid).toHaveBeenCalledWith('/music/Drake/song.mp3');
  expect(apiService.replaceLibraryTrack).not.toHaveBeenCalled();
});

test('unknown fingerprints offer a recheck without suggesting replacement', async () => {
  vi.mocked(apiService.getAcoustidStats).mockResolvedValue(stats('unknown'));
  render(<AcoustIDManager />);
  expect(await screen.findByRole('button', { name: 'Recheck audio' })).toBeEnabled();
  expect(screen.queryByRole('button', { name: 'Correct / Replace' })).not.toBeInTheDocument();
  expect(screen.getByText('Unknown fingerprint')).toBeInTheDocument();
});

test('bulk recheck includes already scanned unsuccessful files', async () => {
  vi.mocked(apiService.acoustidScan).mockResolvedValue({ status: 'success', message: 'Started' });
  render(<AcoustIDManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'Recheck all unsuccessful' }));
  await waitFor(() => expect(apiService.acoustidScan).toHaveBeenCalledWith(0, true));
  expect(apiService.replaceLibraryTrack).not.toHaveBeenCalled();
});

test('API errors leave the file available for a later recheck', async () => {
  vi.mocked(apiService.recheckAcoustid).mockRejectedValue(new Error('offline'));
  render(<AcoustIDManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'Recheck audio' }));
  await screen.findByText('Unable to recheck this audio file.');
  expect(screen.getByText('Drake — Jumbotron Shit Poppin')).toBeInTheDocument();
  expect(apiService.replaceLibraryTrack).not.toHaveBeenCalled();
});
