import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import GlobalPlayer, { formatPlaybackTime } from '../components/GlobalPlayer';

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const track = { filepath: '/music/song.mp3', title: 'Song', artist: 'Artist' };

test('handles blocked autoplay and does not expose an invalid duration', async () => {
  vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValue(new DOMException('blocked', 'NotAllowedError'));
  const view = render(<GlobalPlayer track={track} onClose={vi.fn()} />);
  expect(await screen.findByText('Press play to start your preview')).toBeInTheDocument();
  const audio = view.container.querySelector('audio')!;
  expect(audio.volume).toBe(.8);
  Object.defineProperty(audio, 'duration', { value: Infinity, configurable: true }); fireEvent.durationChange(audio);
  expect(screen.getByLabelText('Seek playback position')).toBeDisabled();
  expect(formatPlaybackTime(Infinity)).toBe('0:00');
});

test('resets the previous track position and duration when changing tracks', async () => {
  const view = render(<GlobalPlayer track={track} onClose={vi.fn()} />);
  const audio = view.container.querySelector('audio')!;
  Object.defineProperty(audio, 'duration', { value: 100, configurable: true }); fireEvent.durationChange(audio);
  audio.currentTime = 75; fireEvent.timeUpdate(audio);
  view.rerender(<GlobalPlayer track={{ ...track, filepath: '/music/next.mp3', title: 'Next' }} onClose={vi.fn()} />);
  await waitFor(() => expect(screen.getByLabelText('Seek playback position')).toHaveAttribute('aria-valuenow', '0'));
  expect(screen.getByLabelText('Seek playback position')).toBeDisabled();
});
