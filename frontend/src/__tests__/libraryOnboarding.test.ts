import { beforeEach, expect, test, vi } from 'vitest';
import apiService from '../api';
import { prepareLibraryOnboarding } from '../libraryOnboarding';

vi.mock('../api', () => ({ default: { startMetadataRebuildScan: vi.fn() } }));
beforeEach(() => { vi.resetAllMocks(); sessionStorage.clear(); });

test('starts only a read-only inventory after the setup opt-in', async () => {
  vi.mocked(apiService.startMetadataRebuildScan).mockResolvedValue({ status: 'started' });
  expect(await prepareLibraryOnboarding(true)).toBe('/metadata-rebuild?onboarding=1');
  expect(apiService.startMetadataRebuildScan).toHaveBeenCalledExactlyOnceWith(false, true, {
    audio_features_enabled: true, local_audio_analysis: false, bpm_integer: true,
  });
});
test('skips library scanning when onboarding review is declined', async () => {
  expect(await prepareLibraryOnboarding(false)).toBe('/dashboard');
  expect(apiService.startMetadataRebuildScan).not.toHaveBeenCalled();
});
test('keeps successful setup usable when the optional inventory cannot start', async () => {
  vi.mocked(apiService.startMetadataRebuildScan).mockRejectedValue(new Error('Library offline'));
  expect(await prepareLibraryOnboarding(true)).toBe('/metadata-rebuild?onboarding=1');
  expect(sessionStorage.getItem('vd-onboarding-scan-error')).toMatch(/Setup is complete/);
});
