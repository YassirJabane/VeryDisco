import React from 'react';
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import ThumbnailAvatar from '../components/ThumbnailAvatar';
import { resolveTrackArtwork } from '../trackArtwork';

vi.mock('../trackArtwork', () => ({ resolveTrackArtwork: vi.fn(), trackKey: () => 'key' }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

test('does not give MUI a URL until its avatar approaches the viewport', async () => {
  let intersect!: (entries: any[]) => void;
  const disconnect = vi.fn();
  vi.stubGlobal('IntersectionObserver', class {
    constructor(callback: any) { intersect = callback; }
    observe() {}
    disconnect = disconnect;
  });
  const view = render(<ThumbnailAvatar src="https://example.org/cover.jpg" />);
  expect(view.container.querySelector('img')).toBeNull();
  const { act } = await import('@testing-library/react');
  act(() => intersect([{ isIntersecting: true }]));
  await waitFor(() => expect(view.container.querySelector('img')?.src).toBe('https://example.org/cover.jpg'));
  expect(disconnect).toHaveBeenCalled();
  expect(resolveTrackArtwork).not.toHaveBeenCalled();
});

test('falls back to exact track artwork when the CAA image fails', async () => {
  vi.mocked(resolveTrackArtwork).mockResolvedValue(new Map([['key', 'https://example.org/fallback.jpg']]));
  const track = { artist: 'Artist', title: 'Song', album: 'Album', artwork: 'https://coverartarchive.org/release/abc/front-250' };
  const view = render(<ThumbnailAvatar src={track.artwork} track={track} />);
  fireEvent.error(view.container.querySelector('img')!);
  await waitFor(() => expect(view.container.querySelector('img')?.src).toBe('https://example.org/fallback.jpg'));
  expect(resolveTrackArtwork).toHaveBeenCalledWith([expect.objectContaining({ artist: 'Artist', title: 'Song', album: 'Album', artwork: '' })]);
});
