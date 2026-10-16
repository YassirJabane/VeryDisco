import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { Explore } from '../components/Explore';
import { apiService } from '../api';

vi.mock('../api', () => ({ apiService: {
  getStatus: vi.fn(), getCurrentPlaylist: vi.fn(), getFeedback: vi.fn(),
} }));
vi.mock('../context/NotificationContext', () => ({ useNotification: () => ({ notify: vi.fn() }) }));

const daily = '2026-10-22T04:00:00Z';
const weekly = '2026-10-27T04:00:00Z';
const format = (value: string) => new Date(value).toLocaleString(undefined, {
  weekday: 'long', year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
});
const status = () => ({
  is_configured: true, validation_errors: null, is_syncing: false, next_run: daily,
  next_runs: { 'daily-jams': daily, 'weekly-jams': weekly, 'weekly-exploration': weekly },
  active_playlists: ['daily-jams', 'weekly-jams', 'weekly-exploration'],
  progress: { status: 'idle' as const, tracks_found: 0, tracks_downloaded: 0, tracks_skipped: 0, tracks_failed: 0, started_at: null },
  latest_run: null, latest_runs: {},
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(apiService.getStatus).mockResolvedValue(status());
  vi.mocked(apiService.getCurrentPlaylist).mockResolvedValue({ tracks: [] } as any);
  vi.mocked(apiService.getFeedback).mockResolvedValue({ feedback: [] } as any);
});
afterEach(cleanup);

test('weekly tabs show the weekly job timestamp instead of tomorrow daily run', async () => {
  render(<Explore />);
  await screen.findByText(format(daily));
  fireEvent.click(screen.getByRole('tab', { name: 'WEEKLY JAMS' }));
  expect(await screen.findByText(format(weekly))).toBeInTheDocument();
  expect(screen.queryByText(format(daily))).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: 'WEEKLY EXPLORATION' }));
  expect(screen.getByText(format(weekly))).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: 'DAILY JAMS' }));
  expect(screen.getByText(format(daily))).toBeInTheDocument();
});

test('manual-only weekly source never displays a scheduled date from another job', async () => {
  vi.mocked(apiService.getStatus).mockResolvedValue({ ...status(), active_playlists: ['daily-jams'],
    next_runs: { 'daily-jams': daily, 'weekly-jams': null, 'weekly-exploration': null } });
  render(<Explore />);
  await screen.findByText(format(daily));
  fireEvent.click(screen.getByRole('tab', { name: /WEEKLY JAMS/ }));
  await waitFor(() => expect(screen.queryByText('NEXT SCHEDULED RUN:')).not.toBeInTheDocument());
});

test('missing source schedule does not fall back to the global next run', async () => {
  vi.mocked(apiService.getStatus).mockResolvedValue({ ...status(), next_runs: undefined });
  render(<Explore />);
  await screen.findByRole('tab', { name: 'WEEKLY JAMS' });
  fireEvent.click(screen.getByRole('tab', { name: 'WEEKLY JAMS' }));
  expect(screen.queryByText('NEXT SCHEDULED RUN:')).not.toBeInTheDocument();
});
