import React, { useState, useCallback, useEffect, useMemo } from 'react';
import {
  Box, Typography, Button, CircularProgress, Alert,
  InputAdornment, TextField, Stack, Paper, Chip,
  Dialog, DialogTitle, DialogContent, DialogContentText, DialogActions,
  IconButton, Tooltip, LinearProgress, Divider, Avatar, Collapse,
} from '@mui/material';
import {
  LibraryMusic as LibraryIcon,
  Search as SearchIcon,
  Delete as DeleteIcon,
  Refresh as RefreshIcon,
  Folder as FolderIcon,
  MusicNote as TrackIcon,
  Storage as SizeIcon,
  KeyboardArrowDown as ExpandIcon,
  KeyboardArrowUp as CollapseIcon,
  PlayArrow as PlayIcon,
  Edit as EditIcon,
  Lyrics as LyricsIcon,
  CompareArrows as OrganizeIcon,
} from '@mui/icons-material';
import apiService, { AlbumItem, LibraryTrackItem } from '../api';
import LyricsPreviewDialog from './LyricsPreviewDialog';

// ── helpers ───────────────────────────────────────────────────────────────────
function fmtBytes(bytes?: number): string {
  if (!bytes || isNaN(bytes) || bytes <= 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function qualityChipColor(q: string): 'default' | 'success' | 'warning' | 'info' {
  const ql = (q || '').toLowerCase();
  if (ql.startsWith('flac')) return 'success';
  if (ql.includes('320')) return 'warning';
  return 'info';
}

// ── Album Card Component ─────────────────────────────────────────────────────
const AlbumCard: React.FC<{
  album: AlbumItem;
  onDelete: (album: AlbumItem) => void;
  expanded: boolean;
  onToggle: () => void;
  tracks: LibraryTrackItem[] | undefined;
  tracksLoading: boolean;
  onDownloadMissing: () => void;
  onEditTags: (track: LibraryTrackItem) => void;
  onEditLyrics: (track: LibraryTrackItem) => void;
  onPlayPreview: (track: LibraryTrackItem) => void;
}> = ({ album, onDelete, expanded, onToggle, tracks, tracksLoading, onDownloadMissing, onEditTags, onEditLyrics, onPlayPreview }) => {
  return (
    <Paper
      elevation={0}
      sx={{
        border: '1px solid',
        borderColor: 'divider',
        borderRadius: 2,
        overflow: 'hidden',
        background: expanded ? 'rgba(255, 255, 255, 0.02)' : 'transparent',
        transition: 'background .2s, box-shadow .2s',
        '&:hover': {
          boxShadow: 2,
          background: 'rgba(255, 255, 255, 0.04)'
        },
      }}
    >
      {/* Clickable Header Row */}
      <Box
        onClick={onToggle}
        sx={{
          px: 2.5,
          py: 2,
          display: 'flex',
          flexDirection: 'column',
          gap: 1.5,
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        {/* Top Row: Title/Artist on Left, Cover Art & Trash on Right */}
        <Box display="flex" justifyContent="space-between" alignItems="flex-start" gap={2}>
          <Box flex={1} minWidth={0}>
            <Typography variant="subtitle1" fontWeight={800} noWrap sx={{ lineHeight: 1.2 }}>
              {album.album}
            </Typography>
            <Typography variant="caption" color="text.secondary" display="block" noWrap sx={{ mt: 0.3, fontWeight: 600 }}>
              {album.artist}
            </Typography>
          </Box>

          <Stack direction="row" alignItems="center" spacing={1.5} flexShrink={0}>
            <Avatar
              src={`/api/library/albums/cover?folder_path=${encodeURIComponent(album.folder_path)}`}
              variant="rounded"
              sx={{
                width: 48,
                height: 48,
                borderRadius: 2,
                flexShrink: 0,
                bgcolor: 'primary.dark'
              }}
            >
              <FolderIcon />
            </Avatar>

            <Tooltip title="Delete album from disk">
              <IconButton
                size="small"
                color="error"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(album);
                }}
                sx={{ flexShrink: 0 }}
              >
                <DeleteIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Stack>
        </Box>

        {/* Bottom Row: Expand Chevron Arrow on Left, Aligned with Chips */}
        <Stack direction="row" alignItems="center" spacing={1.5} flexWrap="wrap">
          <Box display="flex" alignItems="center" sx={{ color: 'text.secondary' }}>
            {expanded ? <CollapseIcon fontSize="small" /> : <ExpandIcon fontSize="small" />}
          </Box>

          <Chip
            label={
              album.status === 'fully'
                ? `Full (${album.total_tracks && album.total_tracks > 0 ? `${album.track_count}/${album.total_tracks}` : album.track_count})`
                : `Partial (${album.total_tracks && album.total_tracks > 0 ? `${album.track_count}/${album.total_tracks}` : album.track_count})`
            }
            size="small"
            color={album.status === 'fully' ? 'success' : 'warning'}
            sx={{ fontWeight: 800, fontSize: '0.68rem', height: 22 }}
          />

          {album.quality && (
            <Chip
              label={album.quality}
              size="small"
              color={qualityChipColor(album.quality)}
              variant="outlined"
              sx={{ fontWeight: 800, fontSize: '0.68rem', height: 22 }}
            />
          )}

          <Typography variant="caption" color="text.secondary" fontWeight={600} sx={{ ml: 'auto', display: { xs: 'none', sm: 'block' } }}>
            {fmtBytes(album.total_size)}
          </Typography>
        </Stack>
      </Box>

      {/* Expanded Tracklist panel */}
      <Collapse in={expanded} timeout="auto" unmountOnExit>
        <Divider />
        <Box sx={{ pl: { xs: 2, sm: 9 }, pr: { xs: 1, sm: 4 }, py: 2, bgcolor: 'rgba(0, 0, 0, 0.1)' }}>
          <Stack direction="row" alignItems="center" justifyContent="space-between" mb={1.5}>
            <Typography variant="caption" color="text.secondary" fontWeight={700}>
              ALBUM TRACKLIST
            </Typography>
            {album.status === 'partially' && (
              <Button
                variant="outlined"
                color="primary"
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  onDownloadMissing();
                }}
                sx={{ py: 0.2, px: 1.2, fontSize: '0.65rem', borderRadius: 2, textTransform: 'none', fontWeight: 700 }}
              >
                Download Missing Tracks
              </Button>
            )}
          </Stack>
          
          {tracksLoading ? (
            <Stack direction="row" alignItems="center" spacing={1.5} py={1}>
              <CircularProgress size={16} color="primary" />
              <Typography variant="caption" color="text.secondary">Fetching track details...</Typography>
            </Stack>
          ) : !tracks || tracks.length === 0 ? (
            <Typography variant="caption" color="text.secondary">No tracks available.</Typography>
          ) : (
            <Stack spacing={1} sx={{ maxWidth: '100%' }}>
              {tracks.map((t, idx) => (
                <Box
                  key={idx}
                  display="flex"
                  alignItems="center"
                  justifyContent="space-between"
                  sx={{
                    py: 1,
                    borderBottom: '1px solid rgba(255, 255, 255, 0.03)',
                    '&:last-child': { borderBottom: 'none' }
                  }}
                >
                  <Typography
                    variant="body2"
                    sx={{
                      color: t.exists ? 'text.primary' : 'text.disabled',
                      fontWeight: t.exists ? 600 : 400,
                      fontFamily: 'monospace',
                      fontSize: '0.85rem'
                    }}
                  >
                    {t.track_num.toString().padStart(2, '0')}. {t.title}
                  </Typography>
                  <Box display="flex" alignItems="center" gap={1.5}>
                    {!t.exists ? (
                      <Chip
                        label="MISSING"
                        size="small"
                        color="error"
                        variant="outlined"
                        sx={{ height: 16, fontSize: '0.55rem', fontWeight: 800, borderRadius: 1 }}
                      />
                    ) : (
                      <>
                        <Chip
                          label="ON DISK"
                          size="small"
                          color="success"
                          variant="outlined"
                          sx={{ height: 16, fontSize: '0.55rem', fontWeight: 800, borderRadius: 1, opacity: 0.6 }}
                        />
                        <Tooltip title="Preview Audio">
                          <IconButton size="small" onClick={() => onPlayPreview(t)} color="primary">
                            <PlayIcon sx={{ fontSize: 16 }} />
                          </IconButton>
                        </Tooltip>
                        <Tooltip title="Edit Tags">
                          <IconButton size="small" onClick={() => onEditTags(t)}>
                            <EditIcon sx={{ fontSize: 16 }} />
                          </IconButton>
                        </Tooltip>
                        <Tooltip title="Edit Lyrics">
                          <IconButton size="small" onClick={() => onEditLyrics(t)}>
                            <LyricsIcon sx={{ fontSize: 16 }} />
                          </IconButton>
                        </Tooltip>
                      </>
                    )}
                  </Box>
                </Box>
              ))}
            </Stack>
          )}
        </Box>
      </Collapse>
    </Paper>
  );
};

// ── Main component ─────────────────────────────────────────────────────────────
const LibraryManager: React.FC = () => {
  const [albums, setAlbums] = useState<AlbumItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [query, setQuery] = useState('');
  const [visibleCount, setVisibleCount] = useState(15);
  const [deleteTarget, setDeleteTarget] = useState<AlbumItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  // Expanded tracklist states
  const [expandedPath, setExpandedPath] = useState<string | null>(null);
  const [tracksMap, setTracksMap] = useState<Record<string, LibraryTrackItem[]>>({});
  const [tracksLoading, setTracksLoading] = useState(false);

  // Reorganization state
  const [orgPreviewOpen, setOrgPreviewOpen] = useState(false);
  const [orgPreviewList, setOrgPreviewList] = useState<any[]>([]);
  const [orgLoading, setOrgLoading] = useState(false);

  // Inline Tag Editor state
  const [tagEditorOpen, setTagEditorOpen] = useState(false);
  const [editingTrack, setEditingTrack] = useState<LibraryTrackItem | null>(null);
  const [editArtist, setEditArtist] = useState('');
  const [editAlbum, setEditAlbum] = useState('');
  const [editTitle, setEditTitle] = useState('');
  const [editTrack, setEditTrack] = useState('');
  const [editYear, setEditYear] = useState('');
  const [editGenre, setEditGenre] = useState('');
  const [savingTags, setSavingTags] = useState(false);

  // Inline Lyrics Editor state
  const [lyricsEditorOpen, setLyricsEditorOpen] = useState(false);
  const [lyricsTrack, setLyricsTrack] = useState<LibraryTrackItem | null>(null);
  const [lyricsTrackArtist, setLyricsTrackArtist] = useState('');
  const [localLyricsText, setLocalLyricsText] = useState('');
  const [localLyricsType, setLocalLyricsType] = useState('');
  const [loadingLyrics, setLoadingLyrics] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [scanProgress, setScanProgress] = useState<{ status: string; processed: number; total: number; percentage: number; error?: string } | null>(null);

  const showToast = (msg: string, type: 'success' | 'error' = 'success') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  };

  const loadAlbums = useCallback(async (forceScan: boolean = false) => {
    if (forceScan) {
      setScanning(true);
      setScanProgress({ status: 'scanning', processed: 0, total: 0, percentage: 0 });
      try {
        await apiService.triggerLibraryScan();
        const interval = setInterval(async () => {
          try {
            const prog = await apiService.getLibraryScanProgress();
            setScanProgress(prog);
            if (prog.status === 'completed' || prog.status === 'failed') {
              clearInterval(interval);
              setScanning(false);
              setScanProgress(null);
              setLoading(true);
              const data = await apiService.getLibraryAlbums();
              setAlbums(data);
              setLoading(false);
              if (prog.status === 'completed') {
                showToast("Library scan completed successfully.");
              } else {
                showToast(`Library scan failed: ${prog.error || 'Unknown error'}`, 'error');
              }
            }
          } catch {
            clearInterval(interval);
            setScanning(false);
            setScanProgress(null);
          }
        }, 2000);
      } catch {
        setScanning(false);
        setScanProgress(null);
        showToast('Failed to start library scan.', 'error');
      }
    } else {
      setLoading(true);
      try {
        const data = await apiService.getLibraryAlbums();
        setAlbums(data);
      } catch {
        showToast('Failed to load library albums.', 'error');
      } finally {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => { loadAlbums(false); }, [loadAlbums]);
  useEffect(() => { setVisibleCount(15); }, [query]);

  const handleToggleAlbum = async (folderPath: string) => {
    if (expandedPath === folderPath) {
      setExpandedPath(null);
      return;
    }
    setExpandedPath(folderPath);
    if (!tracksMap[folderPath]) {
      setTracksLoading(true);
      try {
        const tracks = await apiService.getLibraryAlbumTracks(folderPath);
        setTracksMap(prev => ({ ...prev, [folderPath]: tracks }));
      } catch {
        showToast('Failed to load tracks details.', 'error');
      } finally {
        setTracksLoading(false);
      }
    }
  };

  const handleDownloadMissing = async (album: AlbumItem) => {
    const albumTracks = tracksMap[album.folder_path];
    if (!albumTracks) {
      showToast('Tracklist details not loaded yet. Try again.', 'error');
      return;
    }
    const missing = albumTracks
      .filter(t => !t.exists)
      .map(t => ({ title: t.title, track_number: t.track_num }));

    if (missing.length === 0) {
      showToast('No missing tracks found for this album.');
      return;
    }

    try {
      await apiService.downloadMissingTracks(album.artist, album.album, missing);
      showToast(`Queued ${missing.length} missing tracks for search and download!`);
    } catch (err: any) {
      showToast(err?.response?.data?.detail ?? 'Failed to queue missing tracks.', 'error');
    }
  };

  // ── Tag Editor actions ────────────────────────────────────────────────────
  const handleOpenTagEditor = async (track: LibraryTrackItem) => {
    if (!track.filepath) return;
    setEditingTrack(track);
    setTagEditorOpen(true);
    setSavingTags(true);
    try {
      const tags = await apiService.getTrackTags(track.filepath);
      setEditArtist(tags.artist || '');
      setEditAlbum(tags.album || '');
      setEditTitle(tags.title || '');
      setEditTrack(tags.track || '');
      setEditYear(tags.year || '');
      setEditGenre(tags.genre || '');
    } catch {
      showToast('Failed to load track tags.', 'error');
    } finally {
      setSavingTags(false);
    }
  };

  const handleSaveTags = async () => {
    if (!editingTrack || !editingTrack.filepath) return;
    setSavingTags(true);
    try {
      await apiService.saveTrackTags({
        filepath: editingTrack.filepath,
        artist: editArtist,
        album: editAlbum,
        title: editTitle,
        track: editTrack,
        year: editYear,
        genre: editGenre
      });
      showToast('Tags saved successfully!');
      setTagEditorOpen(false);
      // Reload album tracklist
      if (expandedPath) {
        const tracks = await apiService.getLibraryAlbumTracks(expandedPath);
        setTracksMap(prev => ({ ...prev, [expandedPath]: tracks }));
      }
    } catch {
      showToast('Failed to save track tags.', 'error');
    } finally {
      setSavingTags(false);
    }
  };

  // ── Lyrics Editor actions ─────────────────────────────────────────────────
  const handleOpenLyricsEditor = async (track: LibraryTrackItem, artist: string) => {
    if (!track.filepath) return;
    setLyricsTrack(track);
    setLyricsTrackArtist(artist);
    setLyricsEditorOpen(true);
    setLoadingLyrics(true);
    try {
      const res = await apiService.getLyricsFile(track.filepath);
      setLocalLyricsText(res.lyrics_text);
      setLocalLyricsType(res.type);
    } catch {
      setLocalLyricsText('');
      setLocalLyricsType('missing');
    } finally {
      setLoadingLyrics(false);
    }
  };

  const handleLyricsSaveSuccess = () => {
    showToast("Lyrics updated successfully.");
    setLyricsEditorOpen(false);
    setLyricsTrack(null);
  };

  // ── Reorganize actions ────────────────────────────────────────────────────
  const handleOpenOrganize = async () => {
    setOrgPreviewOpen(true);
    setOrgLoading(true);
    try {
      const list = await apiService.getOrganizePreview();
      setOrgPreviewList(list);
    } catch {
      showToast('Failed to load organization preview.', 'error');
    } finally {
      setOrgLoading(false);
    }
  };

  const handleExecuteOrganize = async () => {
    setOrgLoading(true);
    try {
      const res = await apiService.executeOrganize();
      showToast(`Successfully reorganized library. Relocated ${res.moved_count} tracks!`);
      setOrgPreviewOpen(false);
      loadAlbums(true);
    } catch {
      showToast('Failed to reorganize library.', 'error');
    } finally {
      setOrgLoading(false);
    }
  };

  // ── Audio Preview Trigger ─────────────────────────────────────────────────
  const handlePlayPreview = (track: LibraryTrackItem, artist: string) => {
    if (!track.filepath) return;
    window.dispatchEvent(new CustomEvent("verydisco-play", {
      detail: {
        filepath: track.filepath,
        title: track.title,
        artist: artist
      }
    }));
  };

  // ── filtered list ──────────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    if (!query.trim()) return albums;
    const q = query.toLowerCase();
    return albums.filter(
      a => (a.album || '').toLowerCase().includes(q) || (a.artist || '').toLowerCase().includes(q)
    );
  }, [albums, query]);

  // ── stats ──────────────────────────────────────────────────────────────────
  const totalTracks = useMemo(() => albums.reduce((s, a) => s + a.track_count, 0), [albums]);
  const totalSize = useMemo(() => albums.reduce((s, a) => s + a.total_size, 0), [albums]);

  // ── delete confirm ─────────────────────────────────────────────────────────
  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await apiService.deleteLibraryAlbum(deleteTarget.folder_path);
      setAlbums(prev => prev.filter(a => a.folder_path !== deleteTarget.folder_path));
      showToast(`Deleted "${deleteTarget.album}" and triggered Navidrome rescan.`);
    } catch (e: any) {
      showToast(e?.response?.data?.detail ?? 'Delete failed.', 'error');
    } finally {
      setDeleting(false);
      setDeleteTarget(null);
    }
  };

  // ── group by artist ────────────────────────────────────────────────────────
  const grouped = useMemo(() => {
    const map: Record<string, AlbumItem[]> = {};
    for (const a of filtered) {
      if (!map[a.artist]) map[a.artist] = [];
      map[a.artist].push(a);
    }
    return Object.entries(map).sort(([a], [b]) => a.localeCompare(b));
  }, [filtered]);

  const paginatedGrouped = useMemo(() => {
    return grouped.slice(0, visibleCount);
  }, [grouped, visibleCount]);

  return (
    <Box>
      {/* Header */}
      <Stack direction={{ xs: 'column', sm: 'row' }} alignItems={{ xs: 'flex-start', sm: 'center' }} justifyContent="space-between" mb={3} gap={2}>
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <LibraryIcon sx={{ color: 'primary.main', fontSize: 32 }} />
          <Box>
            <Typography variant="h5" fontWeight={800}>Library Manager</Typography>
            <Typography variant="body2" color="text.secondary">
              Browse, search and manage every release on disk
            </Typography>
          </Box>
        </Stack>
        <Stack direction="row" spacing={1.5}>
          <Button
            variant="outlined"
            startIcon={<OrganizeIcon />}
            onClick={handleOpenOrganize}
            sx={{ borderRadius: 3, fontWeight: 700, textTransform: 'none' }}
          >
            Organize Files
          </Button>
          <Button
            variant="contained"
            startIcon={loading ? <CircularProgress size={16} color="inherit" /> : <RefreshIcon />}
            disabled={loading}
            onClick={() => loadAlbums(true)}
            sx={{ borderRadius: 3, fontWeight: 700, textTransform: 'none' }}
          >
            Refresh
          </Button>
        </Stack>
      </Stack>

      {scanning && scanProgress && (
        <Paper sx={{ p: 2.5, mb: 3, border: '1px solid', borderColor: 'divider', borderRadius: 3, background: theme => theme.palette.mode === 'dark' ? '#1c1b22' : '#ffffff' }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1.5}>
            <Typography variant="body2" sx={{ fontWeight: 700 }}>
              Scanning library files... ({scanProgress.processed} / {scanProgress.total} tracks)
            </Typography>
            <Typography variant="body2" color="primary.main" sx={{ fontWeight: 800 }}>
              {scanProgress.percentage}%
            </Typography>
          </Stack>
          <LinearProgress variant="determinate" value={scanProgress.percentage} sx={{ height: 8, borderRadius: 4 }} />
        </Paper>
      )}

      {loading && !scanning && <LinearProgress sx={{ borderRadius: 4, mb: 2 }} />}

      {/* Stats bar */}
      {albums.length > 0 && (
        <Stack direction="row" spacing={3} mb={3} flexWrap="wrap">
          <Stack direction="row" alignItems="center" spacing={0.8}>
            <FolderIcon sx={{ color: 'primary.main', fontSize: 18 }} />
            <Typography variant="body2" fontWeight={700}>{albums.length}</Typography>
            <Typography variant="body2" color="text.secondary">albums</Typography>
          </Stack>
          <Stack direction="row" alignItems="center" spacing={0.8}>
            <TrackIcon sx={{ color: 'secondary.main', fontSize: 18 }} />
            <Typography variant="body2" fontWeight={700}>{totalTracks}</Typography>
            <Typography variant="body2" color="text.secondary">tracks</Typography>
          </Stack>
          <Stack direction="row" alignItems="center" spacing={0.8}>
            <SizeIcon sx={{ color: 'text.secondary', fontSize: 18 }} />
            <Typography variant="body2" fontWeight={700}>{fmtBytes(totalSize)}</Typography>
            <Typography variant="body2" color="text.secondary">total</Typography>
          </Stack>
        </Stack>
      )}

      {toast && (
        <Alert severity={toast.type} sx={{ mb: 2, borderRadius: 2 }} onClose={() => setToast(null)}>
          {toast.msg}
        </Alert>
      )}

      {/* Search */}
      <TextField
        fullWidth
        size="small"
        placeholder="Search artist or album…"
        value={query}
        onChange={e => setQuery(e.target.value)}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <SearchIcon fontSize="small" />
            </InputAdornment>
          ),
        }}
        sx={{ mb: 2.5, '& .MuiOutlinedInput-root': { borderRadius: 3 } }}
      />

      {/* Empty states */}
      {!loading && albums.length === 0 && (
        <Paper
          elevation={0}
          sx={{ p: 8, textAlign: 'center', border: '1px dashed', borderColor: 'divider', borderRadius: 3 }}
        >
          <LibraryIcon sx={{ fontSize: 64, color: 'text.disabled', mb: 2 }} />
          <Typography variant="h6" color="text.secondary" fontWeight={600}>Library is empty</Typography>
          <Typography variant="body2" color="text.disabled">
            Download some albums from the Search tab to get started.
          </Typography>
        </Paper>
      )}

      {!loading && albums.length > 0 && filtered.length === 0 && (
        <Typography color="text.secondary" sx={{ mt: 2 }}>No results for "{query}"</Typography>
      )}

      {/* Grouped album list */}
      <Stack spacing={3}>
        {paginatedGrouped.map(([artist, artistAlbums]) => (
          <Box key={artist}>
            <Stack direction="row" alignItems="center" spacing={1} mb={1}>
              <Typography variant="subtitle2" fontWeight={800} color="text.secondary">
                {artist.toUpperCase()}
               </Typography>
              <Chip
                label={artistAlbums.length}
                size="small"
                sx={{ height: 18, fontSize: '0.65rem', fontWeight: 700 }}
              />
            </Stack>
            <Stack spacing={1.5}>
              {artistAlbums.map(a => (
                <AlbumCard
                  key={a.folder_path}
                  album={a}
                  onDelete={setDeleteTarget}
                  expanded={expandedPath === a.folder_path}
                  onToggle={() => handleToggleAlbum(a.folder_path)}
                  tracks={tracksMap[a.folder_path]}
                  tracksLoading={expandedPath === a.folder_path && tracksLoading}
                  onDownloadMissing={() => handleDownloadMissing(a)}
                  onEditTags={handleOpenTagEditor}
                  onEditLyrics={(track) => handleOpenLyricsEditor(track, a.artist)}
                  onPlayPreview={(track) => handlePlayPreview(track, a.artist)}
                />
              ))}
            </Stack>
          </Box>
        ))}
      </Stack>

      {grouped.length > visibleCount && (
        <Box display="flex" justifyContent="center" mt={4} mb={2}>
          <Button 
            variant="outlined" 
            onClick={() => setVisibleCount(prev => prev + 15)}
            sx={{ borderRadius: 3, fontWeight: 700, px: 4, py: 1, textTransform: 'none' }}
          >
            Load More Artists ({grouped.length - visibleCount} remaining)
          </Button>
        </Box>
      )}

      {/* Reorganize Library Preview Dialog */}
      <Dialog
        open={orgPreviewOpen}
        onClose={() => !orgLoading && setOrgPreviewOpen(false)}
        maxWidth="md"
        fullWidth
        PaperProps={{ sx: { borderRadius: 3 } }}
      >
        <DialogTitle sx={{ fontWeight: 800 }}>Reorganize Music Library</DialogTitle>
        <DialogContent dividers>
          <DialogContentText mb={2}>
            This tool will automatically restructure your music folders matching your personal renaming pattern. Below is a preview of the changes:
          </DialogContentText>
          {orgLoading ? (
            <Box display="flex" justifyContent="center" p={4}><CircularProgress /></Box>
          ) : orgPreviewList.length === 0 ? (
            <Typography variant="body2" color="text.secondary" fontStyle="italic">
              Your entire library is already perfectly structured! No renaming needed.
            </Typography>
          ) : (
            <Stack spacing={2}>
              {orgPreviewList.map((p, idx) => (
                <Paper key={idx} sx={{ p: 2, bgcolor: 'action.hover', border: '1px solid', borderColor: 'divider', borderRadius: 2 }}>
                  <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 700 }}>FROM</Typography>
                  <Typography variant="body2" sx={{ fontFamily: 'monospace', mb: 1, wordBreak: 'break-all' }}>{p.src}</Typography>
                  <Typography variant="caption" color="primary.main" sx={{ fontWeight: 700 }}>TO</Typography>
                  <Typography variant="body2" sx={{ fontFamily: 'monospace', wordBreak: 'break-all', fontWeight: 600 }}>{p.dst}</Typography>
                </Paper>
              ))}
            </Stack>
          )}
        </DialogContent>
        <DialogActions sx={{ p: 2.5 }}>
          <Button onClick={() => setOrgPreviewOpen(false)} disabled={orgLoading}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleExecuteOrganize}
            disabled={orgLoading || orgPreviewList.length === 0}
            startIcon={orgLoading ? <CircularProgress size={16} /> : <OrganizeIcon />}
            sx={{ borderRadius: 2 }}
          >
            Apply Changes
          </Button>
        </DialogActions>
      </Dialog>

      {/* Tag Editor Dialog */}
      <Dialog
        open={tagEditorOpen}
        onClose={() => !savingTags && setTagEditorOpen(false)}
        PaperProps={{ sx: { borderRadius: 3, width: 420 } }}
      >
        <DialogTitle sx={{ fontWeight: 800 }}>Edit Track Tags</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1.5 }}>
          {savingTags && !editArtist ? (
            <Box display="flex" justifyContent="center" p={2}><CircularProgress size={24} /></Box>
          ) : (
            <>
              <TextField label="Track Title" fullWidth value={editTitle} onChange={e => setEditTitle(e.target.value)} size="small" />
              <TextField label="Artist" fullWidth value={editArtist} onChange={e => setEditArtist(e.target.value)} size="small" />
              <TextField label="Album" fullWidth value={editAlbum} onChange={e => setEditAlbum(e.target.value)} size="small" />
              <Stack direction="row" spacing={2}>
                <TextField label="Track #" fullWidth value={editTrack} onChange={e => setEditTrack(e.target.value)} size="small" />
                <TextField label="Year" fullWidth value={editYear} onChange={e => setEditYear(e.target.value)} size="small" />
              </Stack>
              <TextField label="Genre" fullWidth value={editGenre} onChange={e => setEditGenre(e.target.value)} size="small" />
            </>
          )}
        </DialogContent>
        <DialogActions sx={{ p: 2.5 }}>
          <Button onClick={() => setTagEditorOpen(false)} disabled={savingTags}>Cancel</Button>
          <Button variant="contained" onClick={handleSaveTags} disabled={savingTags} sx={{ borderRadius: 2 }}>
            {savingTags ? 'Saving…' : 'Save Tags'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Lyrics Editor Wrapper Dialog */}
      {lyricsTrack && (
        <LyricsPreviewDialog
          open={lyricsEditorOpen}
          onClose={() => {
            setLyricsEditorOpen(false);
            setLyricsTrack(null);
          }}
          defaultArtist={lyricsTrackArtist || ''}
          defaultTitle={lyricsTrack.title || ''}
          filepath={lyricsTrack.filepath || ''}
          defaultLyricsText={localLyricsText}
          defaultLyricsType={localLyricsType}
          onSaveSuccess={handleLyricsSaveSuccess}
        />
      )}

      {/* Delete confirmation dialog */}
      <Dialog
        open={!!deleteTarget}
        onClose={() => !deleting && setDeleteTarget(null)}
        PaperProps={{ sx: { borderRadius: 3, minWidth: { xs: '90vw', sm: 380 } } }}
      >
        <DialogTitle sx={{ fontWeight: 800, pb: 1 }}>Delete Album?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This will <strong>permanently delete</strong> all files in:
          </DialogContentText>
          <Paper
            elevation={0}
            sx={{ mt: 1.5, p: 1.5, borderRadius: 2, bgcolor: 'action.hover', fontFamily: 'monospace', fontSize: '0.8rem', wordBreak: 'break-all' }}
          >
            {deleteTarget?.folder_path}
          </Paper>
          <DialogContentText sx={{ mt: 1.5, color: 'error.main', fontWeight: 600 }}>
            This action cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button
            onClick={() => setDeleteTarget(null)}
            disabled={deleting}
            sx={{ borderRadius: 2, textTransform: 'none' }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            color="error"
            disabled={deleting}
            startIcon={deleting ? <CircularProgress size={14} color="inherit" /> : <DeleteIcon />}
            onClick={confirmDelete}
            sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 700 }}
          >
            {deleting ? 'Deleting…' : 'Delete Permanently'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default LibraryManager;
