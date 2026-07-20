import axios from 'axios';

// Create pre-configured axios instance
const api = axios.create({
  baseURL: '',
  withCredentials: true,  // Required so httpOnly session cookie is sent with every request
  headers: {
    'Content-Type': 'application/json',
  },
});

// ── Auth types ──────────────────────────────────────────────────────────────
export interface AuthUser {
  id: string;
  username: string;
  displayName: string;
  isAdmin: boolean;
  musicDir: string;
}

export interface UserConfig {
  lb_username: string;
  lb_token: string;
  active_playlists: string[];
  music_dir?: string;
  playlist_dir?: string;
  renaming_pattern?: string;
  enabled_features?: { [key: string]: boolean };
}

export interface ConfigData {
  listenbrainz: {
    username: string;
    active_playlists: string[];
    token: string;
  };
  slskd: {
    base_url: string;
    api_key: string;
    downloads_dir: string;
    audio_quality: {
      preset: string;
      custom_qualities: string[];
    };
  };
  navidrome: {
    url: string;
    username: string;
    password: string;
  };
  acoustid: {
    api_key: string;
  };
  lyrics: {
    provider: string;
    base_url: string;
  };
  schedule: {
    daily_time: string;
    weekly_time: string;
    weekly_day: string;
    file_checks_time: string;
    file_checks_day: string;
    run_on_startup: boolean;
    batch_size: number;
    max_candidate_attempts: number;
  };
  paths: {
    weekly_output_dir: string;
    navidrome_playlists_dir: string;
    music_dir: string;
  };
  timeouts: {
    http_seconds: number;
    search_seconds: number;
    download_seconds: number;
  };
  log_level: string;
}

export interface GetConfigResponse {
  raw_yaml: string;
  is_configured: boolean;
  validation_errors: string | null;
  parsed: ConfigData | null;
}

export interface RunProgress {
  status: 'idle' | 'running' | 'completed' | 'failed';
  tracks_found: number;
  tracks_downloaded: number;
  tracks_skipped: number;
  tracks_failed: number;
  started_at: string | null;
  current_source?: string | null;
}

export interface RunRecord {
  id: number;
  timestamp: string;
  status: 'running' | 'completed' | 'failed';
  tracks_found: number;
  tracks_downloaded: number;
  tracks_skipped: number;
  tracks_failed: number;
  error_message: string | null;
}

export interface ActiveTask {
  id: string;
  type: string;
  metadata: Record<string, any>;
  started_at: string;
}

export interface AlbumDownloadQueueItem {
  id: number;
  artist: string;
  title: string;
  album: string;
  status: string;
  added_at: string;
  user_id?: string | null;
}

export interface AcoustidStats {
  total: number;
  scanned: number;
  verified: number;
  failed: number;
  remaining: number;
  running: boolean;
}

export interface GetStatusResponse {
  is_configured: boolean;
  validation_errors: string | null;
  is_syncing: boolean;
  next_run: string | null;
  progress: RunProgress;
  latest_run: RunRecord | null;
  latest_runs: Record<string, RunRecord | null>;
  active_playlists: string[];
}

export interface GetRunsResponse {
  runs: RunRecord[];
  total: number;
}

export interface TrackRecord {
  id: number;
  run_id: number;
  artist: string;
  title: string;
  status: 'downloaded' | 'skipped' | 'failed' | 'pending';
  filename: string | null;
  lyrics_status: 'synced' | 'plain' | 'missing' | 'none';
  error_reason: string | null;
  bitrate: number | null;
  size: number | null;
}

export interface GetTracksResponse {
  tracks: TrackRecord[];
}

// ── Library Manager ──────────────────────────────────────────────────────────
export interface AlbumItem {
  artist: string;
  album: string;
  track_count: number;
  total_size: number; // bytes
  quality: string;
  folder_path: string;
  has_cover?: boolean;
  total_tracks?: number;
  status: 'fully' | 'partially';
}

export interface LibraryTrackItem {
  title: string;
  track_num: number;
  exists: boolean;
  filepath: string | null;
}

// ── Lyrics Manager ───────────────────────────────────────────────────────────
export interface MissingLyricsTrack {
  artist: string;
  title: string;
  album: string;
  filepath: string;
  duration: number;
}

export interface LrcLibCandidate {
  id: number;
  name: string;
  trackName?: string;
  artistName: string;
  albumName: string;
  duration: number;
  instrumental: boolean;
  plainLyrics?: string;
  syncedLyrics?: string;
}

// ── Maintenance / Server Health ───────────────────────────────────────────────
export interface IssueAction {
  name: string;
  action: string;
  label: string;
  params?: Record<string, string>;
}

export interface MaintenanceIssue {
  id: string;
  type: 'duplicate_track' | 'split_album' | 'missing_cover' | 'missing_metadata' | 'misfiled_tracks' | 'orphaned_lyrics' | 'dirty_metadata' | 'acoustid_mismatch';
  severity: 'high' | 'medium' | 'low';
  title: string;
  description: string;
  target_path: string;
  actions: IssueAction[];
}

export const apiService = {
  async getConfig(): Promise<GetConfigResponse> {
    const resp = await api.get<GetConfigResponse>('/api/config');
    return resp.data;
  },

  async updateConfig(config: Record<string, any>): Promise<{ status: string; message: string }> {
    const resp = await api.put<{ status: string; message: string }>('/api/config', config);
    return resp.data;
  },

  async getStatus(): Promise<GetStatusResponse> {
    const resp = await api.get<GetStatusResponse>('/api/status');
    return resp.data;
  },

  async getRuns(limit: number = 20, offset: number = 0): Promise<GetRunsResponse> {
    const resp = await api.get<GetRunsResponse>(`/api/runs?limit=${limit}&offset=${offset}`);
    return resp.data;
  },

  async getTracks(runId: number): Promise<GetTracksResponse> {
    const resp = await api.get<GetTracksResponse>(`/api/runs/${runId}/tracks`);
    return resp.data;
  },

  async triggerSync(): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/trigger');
    return resp.data;
  },

  async stopSync(): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/sync/stop');
    return resp.data;
  },

  async getCurrentPlaylist(source?: string): Promise<{ tracks: any[] }> {
    const url = source ? `/api/playlist/current?source=${source}` : '/api/playlist/current';
    const resp = await api.get<{ tracks: any[] }>(url);
    return resp.data;
  },

  async likeTrack(artist: string, title: string, album: string, score: number = 1): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/playlist/like', { artist, title, album, score });
    return resp.data;
  },
  
  async getFeedback(score?: number, count: number = 100, offset: number = 0): Promise<{ feedback: any[] }> {
    const scoreParam = score !== undefined ? `&score=${score}` : '';
    const resp = await api.get<{ feedback: any[] }>(`/api/playlist/feedback?count=${count}&offset=${offset}${scoreParam}`);
    return resp.data;
  },

  async searchDeezer(query: string, type: 'track' | 'album' | 'artist' = 'track'): Promise<any> {
    const resp = await api.get<any>(`/api/deezer/search?query=${encodeURIComponent(query)}&type=${type}`);
    return resp.data;
  },

  async checkTrackExists(artist: string, title: string, albumId?: number): Promise<any> {
    const albumParam = albumId ? `&album_id=${albumId}` : '';
    const resp = await api.get<any>(`/api/search/check?artist=${encodeURIComponent(artist)}&title=${encodeURIComponent(title)}${albumParam}`);
    return resp.data;
  },

  async downloadTrack(artist: string, title: string, album: string, force: boolean = false): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/download/track', { artist, title, album, force });
    return resp.data;
  },

  async downloadAlbum(artist: string, album: string, force: boolean = false): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/download/album', { artist, album, force });
    return resp.data;
  },

  async searchAlbumCandidates(artist: string, album: string): Promise<any[]> {
    const resp = await api.get<any[]>(`/api/download/album/search?artist=${encodeURIComponent(artist)}&album=${encodeURIComponent(album)}`);
    return resp.data;
  },

  async grabAlbum(artist: string, album: string, username: string, folder: string, files: any[], force: boolean = false): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/download/album/grab', { artist, album, username, folder, files, force });
    return resp.data;
  },

  async searchTrackCandidates(artist: string, title: string, album?: string): Promise<any[]> {
    const albumParam = album ? `&album=${encodeURIComponent(album)}` : '';
    const resp = await api.get<any[]>(`/api/download/track/search?artist=${encodeURIComponent(artist)}&title=${encodeURIComponent(title)}${albumParam}`);
    return resp.data;
  },

  async grabTrack(artist: string, title: string, album: string, username: string, filename: string, size: number): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/download/track/grab', { artist, title, album, username, filename, size });
    return resp.data;
  },

  async downloadMissingTracks(artist: string, album: string, missingTracks: { title: string; track_number?: number }[]): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/download/missing', { artist, album, missing_tracks: missingTracks });
    return resp.data;
  },

  async triggerLibraryScan(): Promise<any> {
    const resp = await api.post<any>('/api/library/scan');
    return resp.data;
  },

  async getLibraryScanProgress(): Promise<any> {
    const resp = await api.get<any>('/api/library/scan/progress');
    return resp.data;
  },

  async getArtistAlbums(artistId: number): Promise<any> {
    const resp = await api.get<any>(`/api/deezer/artist/${artistId}/albums`);
    return resp.data;
  },

  async getPinnedArtists(): Promise<any[]> {
    const resp = await api.get<any[]>('/api/pinned_artists');
    return resp.data;
  },

  async pinArtist(artistName: string, deezerId?: number, pictureUrl?: string): Promise<any> {
    const resp = await api.post<any>('/api/pinned_artists', { artist_name: artistName, deezer_id: deezerId, picture_url: pictureUrl });
    return resp.data;
  },

  async unpinArtist(id: number): Promise<any> {
    const resp = await api.delete<any>(`/api/pinned_artists/${id}`);
    return resp.data;
  },

  async getArtistReleases(artistId: number): Promise<any[]> {
    const resp = await api.get<any[]>(`/api/deezer/artist/${artistId}/releases`);
    return resp.data;
  },

  async testListenBrainz(): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/test/listenbrainz');
    return resp.data;
  },

  async testSlskd(): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/test/slskd');
    return resp.data;
  },
 
  async testNavidrome(): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/test/navidrome');
    return resp.data;
  },

  async getNavidromeStats(): Promise<{ songs: number; albums: number; artists: number }> {
    const resp = await api.get<{ songs: number; albums: number; artists: number }>('/api/navidrome/stats');
    return resp.data;
  },

  async syncNavidromeStarred(): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/navidrome/sync_starred');
    return resp.data;
  },


  async triggerSyncForSource(source: string): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>(`/api/trigger?source=${encodeURIComponent(source)}`);
    return resp.data;
  },

  // ── Lyrics Manager ─────────────────────────────────────────────────────────
  async getMissingLyrics(): Promise<MissingLyricsTrack[]> {
    const resp = await api.get<MissingLyricsTrack[]>('/api/lyrics/missing');
    return resp.data;
  },

  async searchLyrics(artist: string, title: string): Promise<LrcLibCandidate[]> {
    const resp = await api.get<LrcLibCandidate[]>(`/api/lyrics/search?artist=${encodeURIComponent(artist)}&title=${encodeURIComponent(title)}`);
    return resp.data;
  },

  async saveLyrics(filepath: string, lyricsText: string): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/lyrics/save', {
      filepath,
      lyrics_text: lyricsText,
    });
    return resp.data;
  },

  async stageLyrics(artist: string, title: string, lyricsText: string): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/lyrics/stage', {
      artist,
      title,
      lyrics_text: lyricsText,
    });
    return resp.data;
  },

  // ── Library Manager ────────────────────────────────────────────────────────
  async getLibraryAlbums(): Promise<AlbumItem[]> {
    const resp = await api.get<AlbumItem[]>('/api/library/albums');
    return resp.data;
  },

  async getLibraryAlbumTracks(folderPath: string): Promise<LibraryTrackItem[]> {
    const resp = await api.get<LibraryTrackItem[]>(`/api/library/albums/tracks?folder_path=${encodeURIComponent(folderPath)}`);
    return resp.data;
  },

  async deleteLibraryAlbum(folderPath: string): Promise<{ status: string; message: string }> {
    const resp = await api.delete<{ status: string; message: string }>('/api/library/albums', {
      data: { folder_path: folderPath },
    });
    return resp.data;
  },

  // ── Maintenance / Server Health ────────────────────────────────────────────
  async scanMaintenanceIssues(refresh: boolean = false): Promise<MaintenanceIssue[]> {
    const resp = await api.get<MaintenanceIssue[]>(`/api/maintenance/scan${refresh ? '?refresh=true' : ''}`);
    return resp.data;
  },

  async fixMaintenanceIssue(
    issueType: string,
    targetPath: string,
    action: string,
    params?: Record<string, string>
  ): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/maintenance/fix', {
      issue_type: issueType,
      target_path: targetPath,
      action,
      params,
    });
    return resp.data;
  },

  async ignoreMaintenanceIssue(issueType: string, targetPath: string): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/maintenance/ignore', {
      issue_type: issueType,
      target_path: targetPath,
    });
    return resp.data;
  },

  async unignoreMaintenanceIssue(issueType: string, targetPath: string): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>('/api/maintenance/unignore', {
      issue_type: issueType,
      target_path: targetPath,
    });
    return resp.data;
  },

  async previewMaintenanceFix(issueType: string, targetPath: string): Promise<any> {
    const resp = await api.post<any>('/api/maintenance/preview', {
      issue_type: issueType,
      target_path: targetPath,
    });
    return resp.data;
  },

  // ── Auth ───────────────────────────────────────────────────────────────────
  async setup(payload: any): Promise<{ status: string; user: AuthUser }> {
    const resp = await api.post<{ status: string; user: AuthUser }>('/api/setup', payload);
    return resp.data;
  },

  async login(username: string, password: string): Promise<{ status: string; user: AuthUser }> {
    const resp = await api.post<{ status: string; user: AuthUser }>('/api/auth/login', { username, password });
    return resp.data;
  },

  async me(): Promise<AuthUser> {
    const resp = await api.get<AuthUser>('/api/auth/me');
    return resp.data;
  },

  async logout(): Promise<void> {
    await api.post('/api/auth/logout');
  },

  async getUsers(): Promise<{ users: any[] }> {
    const resp = await api.get<{ users: any[] }>('/api/users');
    return resp.data;
  },

  async importNavidromeUsers(): Promise<{ status: string; imported: string[]; count: number }> {
    const resp = await api.post<{ status: string; imported: string[]; count: number }>('/api/users/import');
    return resp.data;
  },

  async getMyConfig(): Promise<UserConfig> {
    const resp = await api.get<UserConfig>('/api/users/me/config');
    return resp.data;
  },

  async saveMyConfig(config: UserConfig): Promise<{ status: string; message: string }> {
    const resp = await api.put<{ status: string; message: string }>('/api/users/me/config', config);
    return resp.data;
  },

  async adminUpdateUserFeatures(userId: string, enabledFeatures: { [key: string]: boolean }): Promise<{ status: string; message: string }> {
    const resp = await api.put<{ status: string; message: string }>(`/api/admin/users/${userId}/features`, { enabled_features: enabledFeatures });
    return resp.data;
  },

  async getActiveTasks(): Promise<{ tasks: ActiveTask[] }> {
    const resp = await api.get<{ tasks: ActiveTask[] }>('/api/tasks');
    return resp.data;
  },

  async stopActiveTask(taskId: string): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>(`/api/tasks/${taskId}/stop`);
    return resp.data;
  },

  async getAlbumDownloads(): Promise<{ downloads: AlbumDownloadQueueItem[] }> {
    const resp = await api.get<{ downloads: AlbumDownloadQueueItem[] }>('/api/downloads/albums');
    return resp.data;
  },

  async deleteAlbumDownload(downloadId: number): Promise<{ status: string; message: string }> {
    const resp = await api.delete<{ status: string; message: string }>(`/api/downloads/albums/${downloadId}`);
    return resp.data;
  },

  async acoustidScan(batchSize: number = 50): Promise<{ status: string; message: string }> {
    const resp = await api.post<{ status: string; message: string }>(`/api/acoustid/scan?batch_size=${batchSize}`);
    return resp.data;
  },

  async getAcoustidStats(): Promise<AcoustidStats> {
    const resp = await api.get<AcoustidStats>('/api/acoustid/stats');
    return resp.data;
  },

  async updateUserPaths(userId: string, musicDir: string, playlistDir: string): Promise<{ status: string; message: string }> {
    const resp = await api.put<{ status: string; message: string }>(`/api/admin/users/${userId}/paths`, {
      music_dir: musicDir,
      playlist_dir: playlistDir,
    });
    return resp.data;
  },

  async getStatsSummary(): Promise<any> {
    const resp = await api.get('/api/stats/summary');
    return resp.data;
  },

  async getMissingArt(): Promise<any[]> {
    const resp = await api.get<any[]>('/api/library/missing-art');
    return resp.data;
  },

  async scanMissingArt(): Promise<any[]> {
    const resp = await api.post<any[]>('/api/library/missing-art/scan');
    return resp.data;
  },

  async searchArt(artist: string, album: string): Promise<any[]> {
    const resp = await api.get<any[]>('/api/library/art/search', { params: { artist, album } });
    return resp.data;
  },

  async saveArt(payload: { folder_path: string; url: string; embed: boolean }): Promise<any> {
    const resp = await api.post('/api/library/art/save', payload);
    return resp.data;
  },

  async getDuplicates(): Promise<any[]> {
    const resp = await api.get<any[]>('/api/library/duplicates');
    return resp.data;
  },

  async scanDuplicates(): Promise<any[]> {
    const resp = await api.post<any[]>('/api/library/duplicates/scan');
    return resp.data;
  },

  async resolveDuplicates(pathsToDelete: string[]): Promise<any> {
    const resp = await api.post('/api/library/duplicates/resolve', { paths_to_delete: pathsToDelete });
    return resp.data;
  },

  async getTrackTags(filepath: string): Promise<any> {
    const resp = await api.get('/api/library/track/tags', { params: { filepath } });
    return resp.data;
  },

  async saveTrackTags(tags: any): Promise<any> {
    const resp = await api.post('/api/library/track/tags', tags);
    return resp.data;
  },

  async getOrganizePreview(): Promise<any[]> {
    const resp = await api.get<any[]>('/api/library/organize/preview');
    return resp.data;
  },

  async executeOrganize(): Promise<any> {
    const resp = await api.post('/api/library/organize');
    return resp.data;
  },

  async getLyricsFile(filepath: string): Promise<any> {
    const resp = await api.get('/api/lyrics/file', { params: { filepath } });
    return resp.data;
  },

  // ── Naming Convention ──────────────────────────────────────────────────────
  async scanNamingConventions(): Promise<any> {
    const resp = await api.get('/api/library/naming/scan');
    return resp.data;
  },

  async massRenameFiles(paths: string[] | null, dryRun: boolean = false): Promise<any> {
    const resp = await api.post('/api/library/naming/rename', { paths, dry_run: dryRun });
    return resp.data;
  },

  // ── Feature Artist Fixer ────────────────────────────────────────────────────
  async scanFeatArtists(): Promise<any> {
    const resp = await api.get('/api/library/feat/scan');
    return resp.data;
  },

  async fixFeatArtists(paths: string[] | null, dryRun: boolean = false): Promise<any> {
    const resp = await api.post('/api/library/feat/fix', { paths, dry_run: dryRun });
    return resp.data;
  },

  // ── MusicBrainz Re-tag ──────────────────────────────────────────────────────
  async retagLibraryMusicBrainz(paths: string[] | null, dryRun: boolean = false, updateCover: boolean = true): Promise<any> {
    const resp = await api.post('/api/library/retag/musicbrainz', {
      paths,
      dry_run: dryRun,
      update_cover: updateCover,
    });
    return resp.data;
  },

  async getRetagStatus(): Promise<any> {
    const resp = await api.get('/api/library/retag/status');
    return resp.data;
  },

  // ── Artist Aliases ──────────────────────────────────────────────────────────
  async getArtistAliases(): Promise<any> {
    const resp = await api.get('/api/admin/artist-aliases');
    return resp.data;
  },

  async updateArtistAliases(aliases: Record<string, string>): Promise<any> {
    const resp = await api.post('/api/admin/artist-aliases', { aliases });
    return resp.data;
  },

  async resolveArtistMusicBrainz(artistName: string): Promise<any> {
    const resp = await api.post('/api/admin/artist-aliases/resolve-musicbrainz', null, {
      params: { artist_name: artistName },
    });
    return resp.data;
  },

  // AcoustID (full library scan, batch_size=0 = all remaining)
  async acoustidScanAll(): Promise<any> {
    const resp = await api.post('/api/acoustid/scan?batch_size=0');
    return resp.data;
  },
};

export default apiService;
