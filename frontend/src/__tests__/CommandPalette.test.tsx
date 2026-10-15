import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import CommandPalette from '../components/CommandPalette';

afterEach(cleanup);
const destinations = [{ id: 'lyrics', text: 'Lyrics', group: 'Library', icon: null }, { id: 'metadata-rebuild', text: 'Metadata rebuild', group: 'Library', icon: null }];

test('keyboard selection opens the selected destination', () => {
  const navigate = vi.fn(); const close = vi.fn();
  render(<CommandPalette open destinations={destinations} onClose={close} onNavigate={navigate} />);
  const input = screen.getByRole('combobox', { name: 'Search pages' });
  fireEvent.keyDown(input, { key: 'ArrowDown' }); fireEvent.keyDown(input, { key: 'Enter' });
  expect(navigate).toHaveBeenCalledWith('metadata-rebuild'); expect(close).toHaveBeenCalled();
});

test('empty search results cannot trigger stale navigation', () => {
  const navigate = vi.fn();
  render(<CommandPalette open destinations={destinations} onClose={vi.fn()} onNavigate={navigate} />);
  const input = screen.getByRole('combobox', { name: 'Search pages' });
  fireEvent.change(input, { target: { value: 'nothing-matches' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(screen.getByText('No matching pages')).toBeInTheDocument(); expect(navigate).not.toHaveBeenCalled();
});
