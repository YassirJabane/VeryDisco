import React from 'react';
import { describe, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AlbumCard } from '../components/LibraryManager';

const album = {
  artist: 'Prince and The Revolution', album: 'Purple Rain', track_count: 6,
  total_tracks: 6, total_size: 0, quality: 'MP3 320kbps',
  folder_path: '/music/Purple Rain', has_cover: true, status: 'fully' as const,
};

const handlers = {
  onDelete: vi.fn(), onToggle: vi.fn(), onDownloadMissing: vi.fn(),
  onEditTags: vi.fn(), onEditLyrics: vi.fn(), onPlayPreview: vi.fn(),
};

describe('Library album completeness', () => {
  test('local 6/6 is unverified, then official 6/9 is partial', () => {
    const view = render(<AlbumCard album={album} expanded={false} tracks={undefined} tracksLoading={false} {...handlers} />);
    expect(screen.getByText('Unverified (6 local)')).toBeInTheDocument();

    const titles = ["Let's Go Crazy", 'Take Me With U', 'The Beautiful Ones', 'Computer Blue',
      'Darling Nikki', 'When Doves Cry', 'I Would Die 4 U', "Baby I'm a Star", 'Purple Rain'];
    const tracks = titles.map((title, index) => ({
      title, track_num: index + 1, exists: index < 6, verified: true,
      filepath: index < 6 ? `/${index + 1}.mp3` : null,
    }));
    view.rerender(<AlbumCard album={album} expanded={false} tracks={tracks} tracksLoading={false} {...handlers} />);
    expect(screen.getByText('Partial (6/9)')).toBeInTheDocument();
    expect(screen.queryByText('Full (6/6)')).not.toBeInTheDocument();
  });
});
