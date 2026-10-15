import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import PageErrorBoundary from '../components/PageErrorBoundary';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
test('keeps navigation alive and allows retry after a render error', () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const expectedError = (event: ErrorEvent) => { if (event.error?.message === 'failed') event.preventDefault(); };
  window.addEventListener('error', expectedError);
  let broken = true;
  function Page() { if (broken) throw new Error('failed'); return <div>Recovered page</div>; }
  try {
    render(<><nav>Navigation still works</nav><PageErrorBoundary><Page /></PageErrorBoundary></>);
  } finally { window.removeEventListener('error', expectedError); }
  expect(screen.getByText('Navigation still works')).toBeInTheDocument();
  expect(screen.getByText('This page hit a snag')).toBeInTheDocument();
  broken = false; fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  expect(screen.getByText('Recovered page')).toBeInTheDocument();
});
