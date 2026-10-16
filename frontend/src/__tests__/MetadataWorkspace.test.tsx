import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import MetadataWorkspace from '../components/MetadataWorkspace';
vi.mock('../components/MetadataRebuild', () => ({ default: () => <div>Release credit review</div> }));
vi.mock('../components/NamingConvention', () => ({ default: () => <div>Naming preview</div> }));
afterEach(() => { cleanup(); window.history.replaceState(null, '', '/'); sessionStorage.clear(); });
test('keeps old naming links in the integrated naming stage', async () => {
  window.history.replaceState(null, '', '/naming');
  render(<MetadataWorkspace />);
  expect(screen.getByRole('tab', { name: '2. File & folder names' })).toHaveAttribute('aria-selected', 'true');
  expect(await screen.findByText('Naming preview')).toBeVisible();
  fireEvent.click(screen.getByRole('tab', { name: '1. Release metadata & artist credits' }));
  expect(window.location.pathname).toBe('/metadata-rebuild');
  expect(screen.getByText('Release credit review')).toBeVisible();
});
test('opening onboarding only shows the review instructions, without initiating a write', () => {
  window.history.replaceState(null, '', '/metadata-rebuild?onboarding=1');
  render(<MetadataWorkspace />);
  expect(screen.getByText(/initial inventory only reads existing music/)).toBeInTheDocument();
});
