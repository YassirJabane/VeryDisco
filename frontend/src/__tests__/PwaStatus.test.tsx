import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import PwaStatus from '../components/PwaStatus';

const pwa = vi.hoisted(() => ({ refresh: false, setRefresh: vi.fn(), update: vi.fn() }));
vi.mock('virtual:pwa-register/react', () => ({ useRegisterSW: () => ({ needRefresh: [pwa.refresh, pwa.setRefresh], updateServiceWorker: pwa.update }) }));
beforeEach(() => { vi.resetAllMocks(); pwa.refresh = false; vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test('does not reload an open form until the user chooses to activate an update', () => {
  pwa.refresh = true;
  render(<PwaStatus />);
  expect(screen.getByText(/Save any changes before updating/)).toBeInTheDocument();
  expect(pwa.update).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Update now' }));
  expect(pwa.update).toHaveBeenCalledWith(true);
});
test('allows postponing an update without activating it', () => {
  pwa.refresh = true;
  render(<PwaStatus />);
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss update' }));
  expect(pwa.setRefresh).toHaveBeenCalledWith(false);
  expect(pwa.update).not.toHaveBeenCalled();
});
test('explains the server requirement offline and recovers on reconnect', async () => {
  render(<PwaStatus />);
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
  act(() => window.dispatchEvent(new Event('offline')));
  expect(screen.getByText(/library actions need a connection/)).toBeInTheDocument();
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
  act(() => window.dispatchEvent(new Event('online')));
  await waitFor(() => expect(screen.queryByText(/library actions need a connection/)).not.toBeInTheDocument());
});
test('reports a failed update activation instead of losing the active page', async () => {
  pwa.refresh = true;
  pwa.update.mockRejectedValueOnce(new Error('offline'));
  render(<PwaStatus />);
  fireEvent.click(screen.getByRole('button', { name: 'Update now' }));
  expect(await screen.findByText(/update could not be activated/)).toBeInTheDocument();
});
