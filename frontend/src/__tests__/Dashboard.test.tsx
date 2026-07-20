import { describe, test, vi, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import Dashboard from '../components/Dashboard';

vi.mock('../api', () => ({
  apiService: {
    getStatus: vi.fn().mockResolvedValue({
      is_configured: true,
      validation_errors: null,
      is_syncing: false,
      next_run: "2026-07-13T03:00:00Z",
      progress: {
        status: "idle",
        tracks_found: 0,
        tracks_downloaded: 0,
        tracks_skipped: 0,
        tracks_failed: 0,
        started_at: null
      },
      latest_run: {
        id: 1,
        timestamp: "2026-07-06T03:00:00Z",
        status: "completed",
        tracks_found: 10,
        tracks_downloaded: 8,
        tracks_skipped: 2,
        tracks_failed: 0,
        error_message: null
      }
    }),
    triggerSync: vi.fn(),
  }
}));

describe('Dashboard Component', () => {
  test('renders dashboard headers and sync button', async () => {
    render(<Dashboard onNavigateToConfig={() => {}} />);
    
    const header = await screen.findByText('Dashboard');
    expect(header).toBeInTheDocument();

    const syncButton = await screen.findByRole('button', { name: /Sync Now/i });
    expect(syncButton).toBeInTheDocument();
    expect(syncButton).not.toBeDisabled();
  });
});
