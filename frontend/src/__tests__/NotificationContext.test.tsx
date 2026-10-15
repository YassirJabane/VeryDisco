import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { NotificationProvider, useNotification } from '../context/NotificationContext';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test('renders structured validation details as text without crashing the app', () => {
  function Action() {
    const { notify } = useNotification();
    return <button onClick={() => notify({ detail: [{ msg: 'Missing artist' }, { msg: 'Missing title' }] }, 'error')}>Validate</button>;
  }
  render(<NotificationProvider><Action /></NotificationProvider>);
  fireEvent.click(screen.getByText('Validate'));
  expect(screen.getByRole('alert')).toHaveTextContent('Missing artist; Missing title');
});

test('keeps a failed confirmation open and displays an actionable error', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const operation = vi.fn().mockRejectedValue(new Error('Access denied on the music volume'));
  function Action() {
    const { confirm } = useNotification();
    return <button onClick={() => confirm({ title: 'Test operation', message: 'A local test only', onConfirm: operation })}>Open test</button>;
  }
  render(<NotificationProvider><Action /></NotificationProvider>);
  fireEvent.click(screen.getByText('Open test')); fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
  expect(await screen.findByText('Access denied on the music volume')).toBeInTheDocument();
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  expect(operation).toHaveBeenCalledTimes(1);
});
