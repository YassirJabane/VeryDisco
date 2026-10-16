import apiService from './api';

/** Called only after a successful setup submission, never by simply opening a URL. */
export async function prepareLibraryOnboarding(reviewLibrary: boolean): Promise<string> {
  if (!reviewLibrary) return '/dashboard';
  sessionStorage.removeItem('vd-onboarding-scan-error');
  try {
    await apiService.startMetadataRebuildScan(false, true, {
      audio_features_enabled: true, local_audio_analysis: false, bpm_integer: true,
    });
  } catch {
    sessionStorage.setItem('vd-onboarding-scan-error', 'Setup is complete, but the initial library scan could not start. Start a read-only scan below when your music library is available.');
  }
  return '/metadata-rebuild?onboarding=1';
}
