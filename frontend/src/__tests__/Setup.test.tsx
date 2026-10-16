import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import Setup from '../components/Setup';
import { apiService } from '../api';

vi.mock('../api', () => ({ apiService: { setup: vi.fn() }, default: { startMetadataRebuildScan: vi.fn() } }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ refreshUser: vi.fn() }) }));
beforeEach(() => { vi.clearAllMocks(); vi.mocked(apiService.setup).mockResolvedValue({ status: 'test-only', user: {} as any }); });
afterEach(cleanup);

test('Next only advances the setup wizard; it cannot submit the newly rendered final button', async () => {
  render(<Setup />);
  fireEvent.change(screen.getByLabelText('Navidrome Server URL'), { target: { value: 'http://navidrome:4533' } });
  fireEvent.change(screen.getByLabelText('Navidrome Admin Username'), { target: { value: 'demo' } });
  fireEvent.change(screen.getByLabelText('Navidrome Admin Password'), { target: { value: 'demo-password' } });
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(screen.getByRole('checkbox', { name: 'Scan my existing library after setup' })).toBeChecked();
  expect(apiService.setup).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Complete Setup' }));
  await waitFor(() => expect(apiService.setup).toHaveBeenCalledTimes(1));
});
