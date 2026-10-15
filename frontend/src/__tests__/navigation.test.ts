import { describe, expect, test } from 'vitest';
import { filterDestinations, resolveTab } from '../navigation';
import { syncProgress } from '../syncProgress';
import type { RunRecord } from '../api';

describe('navigation and truthful progress', () => {
  test('migrates the legacy aliases page instead of rendering blank', () => expect(resolveTab('/aliases')).toBe('metadata-rebuild'));
  test('rejects invalid destinations and admin-only routes for regular users', () => {
    expect(resolveTab({ page: 'config' })).toBe('dashboard');
    expect(resolveTab('/does-not-exist')).toBe('dashboard');
    for (const page of ['config', 'acoustid', 'server-health', 'logs']) expect(resolveTab(page, false)).toBe('dashboard');
    expect(resolveTab('config', true)).toBe('config');
  });
  test('searches page names and groups without case sensitivity', () => {
    const pages = [{ id: 'lyrics', text: 'Lyrics', group: 'Library', icon: null }, { id: 'logs', text: 'Live logs', group: 'System', icon: null }];
    expect(filterDestinations(pages, 'LIBRARY lyrics').map(item => item.id)).toEqual(['lyrics']);
    expect(filterDestinations(pages, 'missing')).toEqual([]);
  });
  test('counts already-present and failed tracks as processed, not just downloads', () => {
    expect(syncProgress({ tracks_found: 50, tracks_downloaded: 15, tracks_skipped: 34, tracks_failed: 1 } as RunRecord)).toEqual({ total: 50, processed: 50, percent: 100 });
    expect(syncProgress(null).percent).toBe(0);
    expect(syncProgress({ tracks_found: 2, tracks_downloaded: 3, tracks_skipped: 0, tracks_failed: 0 } as RunRecord).percent).toBe(100);
  });
});
