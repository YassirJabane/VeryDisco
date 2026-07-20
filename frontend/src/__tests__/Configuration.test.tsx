import { describe, test, vi, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import Configuration from '../components/Configuration';

vi.mock('../api', () => ({
  apiService: {
    getConfig: vi.fn().mockResolvedValue({
      raw_yaml: "listenbrainz:\n  username: \"testuser\"",
      is_configured: true,
      validation_errors: null,
      parsed: {
        listenbrainz: {
          username: "testuser",
          playlist_source: "weekly-exploration",
          token: "secret-token"
        },
        slskd: {
          base_url: "http://slskd:5030",
          api_key: "",
          downloads_dir: "/downloads",
          min_bitrate: 320
        },
        lyrics: {
          provider: "lrclib",
          base_url: "https://lrclib.net"
        },
        schedule: {
          daily_time: "04:00",
          weekly_time: "04:00",
          weekly_day: "tue",
          run_on_startup: true,
          batch_size: 5,
          max_candidate_attempts: 3
        },
        paths: {
          weekly_output_dir: "/data/weekly/current"
        },
        timeouts: {
          http_seconds: 20,
          search_seconds: 30,
          download_seconds: 240
        },
        log_level: "INFO"
      }
    }),
    updateConfig: vi.fn().mockResolvedValue({ status: "success", message: "Config updated" })
  }
}));

describe('Configuration Component', () => {
  test('renders form input fields with loaded values', async () => {
    render(<Configuration />);
    
    // Wait for values to populate
    const input = await screen.findByLabelText(/ListenBrainz Username/i) as HTMLInputElement;
    expect(input).toBeInTheDocument();
    expect(input.value).toBe("testuser");

    const saveButton = await screen.findByRole('button', { name: /Save Changes/i });
    expect(saveButton).toBeInTheDocument();
  });
});
