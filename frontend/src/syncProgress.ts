import type { RunRecord } from './api';

export function syncProgress(run: RunRecord | null | undefined) {
  const count = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? Math.max(0, value) : 0;
  const total = count(run?.tracks_found);
  const processed = count(run?.tracks_downloaded) + count(run?.tracks_skipped) + count(run?.tracks_failed);
  return { total, processed, percent: total ? Math.min(100, Math.round(processed / total * 100)) : 0 };
}
