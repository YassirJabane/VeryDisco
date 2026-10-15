import React from 'react';
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { LiveLogs } from '../components/LiveLogs';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen?: () => void;
  onerror?: () => void;
  onmessage?: (event: { data: string; lastEventId: string }) => void;
  close = vi.fn();
  constructor(public url: string) { FakeEventSource.instances.push(this); }
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

test('requests startup history and resumes from the last persisted log id', async () => {
  vi.useFakeTimers();
  localStorage.clear();
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  Element.prototype.scrollIntoView = vi.fn();
  const view = render(<LiveLogs />);
  const first = FakeEventSource.instances[0];
  expect(first.url).toBe('/api/logs/stream?limit=0&after=0');
  act(() => first.onmessage?.({
    data: JSON.stringify({ id: 17, timestamp: '2026-10-15', level: 'INFO', message: 'Startup message', run_id: null }),
    lastEventId: '17',
  }));
  expect(screen.getByText('Startup message')).toBeInTheDocument();
  act(() => first.onerror?.());
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(FakeEventSource.instances[1].url).toBe('/api/logs/stream?limit=0&after=17');
  view.unmount();
  expect(FakeEventSource.instances[1].close).toHaveBeenCalled();
});
