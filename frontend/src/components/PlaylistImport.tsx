import React, { useEffect, useState, useMemo } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Collapse,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  List,
  ListItem,
  ListItemAvatar,
  ListItemText,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import Avatar from './ThumbnailAvatar';
import DeleteIcon from '@mui/icons-material/Delete';
import DownloadIcon from '@mui/icons-material/Download';
import EditIcon from '@mui/icons-material/Edit';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import LinkIcon from '@mui/icons-material/Link';
import RefreshIcon from '@mui/icons-material/Refresh';
import SearchIcon from '@mui/icons-material/Search';
import SyncIcon from '@mui/icons-material/Sync';
import { apiService, getErrorMessage } from '../api';

interface TrackItem {
  artist: string;
  title: string;
  album?: string;
  duration?: number | null;
}

interface TrackStatus {
  exists: boolean;
  quality_status?: string;
  path?: string;
  existing_quality?: string;
}

const getTrackKey = (artist: string, title: string) =>
  `${artist.trim().toLowerCase()}:::${title.trim().toLowerCase()}`;

const getFilenameOnly = (filepath: string) => {
  const parts = filepath.split(/[\\/]/);
  return parts[parts.length - 1];
};

const formatSize = (bytes: number) => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

const PlaylistImport: React.FC = () => {
  const theme = useTheme();
  const [url, setUrl] = useState('');
  const [previewData, setPreviewData] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ severity: 'error' | 'success' | 'info'; text: string } | null>(null);
  const [saved, setSaved] = useState<any[]>([]);
  const [expandedPlaylists, setExpandedPlaylists] = useState<Record<number, boolean>>({});
  
  // Track status cache
  const [statusMap, setStatusMap] = useState<Map<string, TrackStatus>>(new Map());
  const [checkingStatus, setCheckingStatus] = useState(false);

  // Edit playlist state
  const [editing, setEditing] = useState<any | null>(null);
  const [newArtist, setNewArtist] = useState('');
  const [newTitle, setNewTitle] = useState('');
  const [newAlbum, setNewAlbum] = useState('');

  // Delete dialog state
  const [deleteTarget, setDeleteTarget] = useState<any | null>(null);

  // Manual search dialog state (Soulseek grab)
  const [manualSearchOpen, setManualSearchOpen] = useState(false);
  const [searchTrack, setSearchTrack] = useState<TrackItem | null>(null);
  const [searchingCandidates, setSearchingCandidates] = useState(false);
  const [trackCandidates, setTrackCandidates] = useState<any[]>([]);
  const [grabbingKey, setGrabbingKey] = useState<string | null>(null);

  const checkTracksBatch = async (tracks: TrackItem[]) => {
    if (!tracks || tracks.length === 0) return;
    setCheckingStatus(true);
    try {
      const itemsToCheck = tracks.map(t => ({ artist: t.artist, title: t.title }));
      const results = await apiService.checkAlbumsBatch(itemsToCheck);
      setStatusMap(prev => {
        const next = new Map(prev);
        results.forEach((res, idx) => {
          const t = tracks[idx];
          if (t) {
            next.set(getTrackKey(t.artist, t.title), res);
          }
        });
        return next;
      });
    } catch (err) {
      console.warn('Batch check failed:', err);
    } finally {
      setCheckingStatus(false);
    }
  };

  const loadSaved = async () => {
    try {
      const list = await apiService.getImportedPlaylists();
      setSaved(list);
      // Collect all tracks across saved playlists to check their status
      const allTracks: TrackItem[] = [];
      list.forEach((p: any) => {
        if (Array.isArray(p.tracks)) {
          allTracks.push(...p.tracks);
        }
      });
      if (allTracks.length > 0) {
        void checkTracksBatch(allTracks);
      }
    } catch {
      setSaved([]);
    }
  };

  useEffect(() => {
    void loadSaved();
  }, []);

  const handlePreview = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const data = await apiService.previewSpotifyPlaylist(url);
      setPreviewData(data);
      if (Array.isArray(data.tracks)) {
        void checkTracksBatch(data.tracks);
      }
    } catch (e) {
      setMessage({ severity: 'error', text: getErrorMessage(e, 'Playlist Spotify non disponibile.') });
    } finally {
      setBusy(false);
    }
  };

  const handleImport = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const result = await apiService.importSpotifyPlaylist(url);
      setMessage({
        severity: 'success',
        text: `Import avviato per "${result.name}". La playlist è registrata su Navidrome e i download sono in corso.`,
      });
      await loadSaved();
    } catch (e) {
      setMessage({ severity: 'error', text: getErrorMessage(e, 'Import Spotify non avviato.') });
    } finally {
      setBusy(false);
    }
  };

  const handleSyncPlaylist = async (item: any) => {
    try {
      const res = await apiService.syncImportedPlaylist(item.id);
      setMessage({ severity: 'success', text: res.message || `Sincronizzazione avviata per "${item.name}".` });
    } catch (error) {
      setMessage({ severity: 'error', text: getErrorMessage(error, 'Errore avvio sincronizzazione.') });
    }
  };

  const handleDeletePlaylist = async () => {
    if (!deleteTarget) return;
    setBusy(true);
    try {
      const res = await apiService.deleteImportedPlaylist(deleteTarget.id);
      setMessage({ severity: 'success', text: res.message || 'Playlist eliminata.' });
      setDeleteTarget(null);
      await loadSaved();
    } catch (error) {
      setMessage({ severity: 'error', text: getErrorMessage(error, 'Errore durante l’eliminazione.') });
    } finally {
      setBusy(false);
    }
  };

  const toggleExpand = (id: number, tracks?: TrackItem[]) => {
    setExpandedPlaylists(prev => {
      const nextState = !prev[id];
      if (nextState && tracks && tracks.length > 0) {
        void checkTracksBatch(tracks);
      }
      return { ...prev, [id]: nextState };
    });
  };

  const addTrackToEditing = () => {
    if (!editing || !newArtist.trim() || !newTitle.trim()) return;
    const newTr: TrackItem = {
      artist: newArtist.trim(),
      title: newTitle.trim(),
      album: newAlbum.trim(),
      duration: null,
    };
    setEditing({ ...editing, tracks: [...editing.tracks, newTr] });
    setNewArtist('');
    setNewTitle('');
    setNewAlbum('');
    void checkTracksBatch([newTr]);
  };

  const saveEdits = async () => {
    if (!editing) return;
    setBusy(true);
    try {
      await apiService.updateImportedPlaylist(editing.id, editing);
      setMessage({ severity: 'success', text: `Playlist “${editing.name}” salvata e aggiornata in Navidrome.` });
      setEditing(null);
      await loadSaved();
    } catch (error) {
      setMessage({ severity: 'error', text: getErrorMessage(error, 'Playlist non salvata.') });
    } finally {
      setBusy(false);
    }
  };

  // Manual search dialog handlers
  const handleOpenManualSearch = async (track: TrackItem) => {
    setSearchTrack(track);
    setManualSearchOpen(true);
    setSearchingCandidates(true);
    setTrackCandidates([]);
    try {
      const candidates = await apiService.searchTrackCandidates(track.artist, track.title, track.album);
      setTrackCandidates(candidates || []);
    } catch (err: any) {
      setMessage({
        severity: 'error',
        text: err.response?.data?.detail || `Ricerca candidati fallita per "${track.title}".`,
      });
    } finally {
      setSearchingCandidates(false);
    }
  };

  const handleGrabTrack = async (cand: any) => {
    if (!searchTrack) return;
    const key = `${cand.username}-${cand.filename}`;
    setGrabbingKey(key);
    try {
      await apiService.grabTrack(
        searchTrack.artist,
        searchTrack.title,
        searchTrack.album || '',
        cand.username,
        cand.filename,
        cand.size,
        true // force download
      );
      setMessage({
        severity: 'success',
        text: `Download avviato dal peer "${cand.username}" per "${searchTrack.title}".`,
      });
      setManualSearchOpen(false);
      // Re-check status after a short delay
      setTimeout(() => {
        void checkTracksBatch([searchTrack]);
      }, 4000);
    } catch (err: any) {
      setMessage({
        severity: 'error',
        text: err.response?.data?.detail || 'Impossibile avviare il download da questo peer.',
      });
    } finally {
      setGrabbingKey(null);
    }
  };

  // Helper to render track library status chip matching Explore page
  const renderTrackStatusChip = (track: TrackItem) => {
    const key = getTrackKey(track.artist, track.title);
    const lib = statusMap.get(key);

    if (lib && lib.exists) {
      if (lib.quality_status === 'worse') {
        return (
          <Chip
            label="Upgrade"
            size="small"
            sx={{ bgcolor: 'orange', color: 'white', fontWeight: 700, height: 22, fontSize: '0.72rem' }}
          />
        );
      }
      if (lib.path && lib.path.toLowerCase().includes('explore')) {
        return (
          <Chip
            label="In Explore"
            size="small"
            color="info"
            sx={{ fontWeight: 700, height: 22, fontSize: '0.72rem' }}
          />
        );
      }
      return (
        <Chip
          label="In Library"
          size="small"
          color="success"
          sx={{ fontWeight: 700, height: 22, fontSize: '0.72rem' }}
        />
      );
    }

    return (
      <Chip
        label="Missing"
        size="small"
        color="error"
        sx={{ fontWeight: 700, height: 22, fontSize: '0.72rem' }}
      />
    );
  };

  // Compute summary stats for a track array
  const getPlaylistStats = (tracks: TrackItem[]) => {
    let inLib = 0;
    let inExplore = 0;
    let missing = 0;
    tracks.forEach(t => {
      const key = getTrackKey(t.artist, t.title);
      const st = statusMap.get(key);
      if (st && st.exists) {
        if (st.path && st.path.toLowerCase().includes('explore')) {
          inExplore++;
        } else {
          inLib++;
        }
      } else {
        missing++;
      }
    });
    return { inLib, inExplore, missing, total: tracks.length };
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <Box>
        <Typography variant="h4" sx={{ fontWeight: 800 }}>
          Playlist importing
        </Typography>
        <Typography color="text.secondary">
          Importa una playlist Spotify in Navidrome usando il downloader di VeryDisco. Controlla lo stato dei brani
          ed effettua ricerche manuali su Soulseek se necessario.
        </Typography>
      </Box>

      {message && (
        <Alert severity={message.severity} onClose={() => setMessage(null)}>
          {message.text}
        </Alert>
      )}

      {/* Input Link Card */}
      <Card sx={{ borderRadius: 3, boxShadow: '0 4px 20px rgba(0,0,0,0.06)' }}>
        <CardContent sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <TextField
            label="Spotify playlist link"
            placeholder="https://open.spotify.com/playlist/..."
            value={url}
            onChange={e => setUrl(e.target.value)}
            fullWidth
            InputProps={{
              startAdornment: <LinkIcon sx={{ mr: 1, color: 'text.secondary' }} />,
            }}
          />
          <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap' }}>
            <Button
              variant="outlined"
              onClick={handlePreview}
              disabled={busy || !url.trim()}
              startIcon={busy ? <CircularProgress size={18} /> : undefined}
              sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600 }}
            >
              {busy ? 'Caricamento...' : 'Preview playlist'}
            </Button>
            {previewData && (
              <Button
                variant="contained"
                startIcon={<DownloadIcon />}
                onClick={handleImport}
                disabled={busy}
                sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 700 }}
              >
                Import and download
              </Button>
            )}
          </Box>
        </CardContent>
      </Card>

      {/* Preview Card */}
      {previewData && (
        <Card sx={{ borderRadius: 3, boxShadow: '0 4px 20px rgba(0,0,0,0.08)' }}>
          <CardContent>
            <Stack
              direction={{ xs: 'column', sm: 'row' }}
              spacing={2.5}
              alignItems={{ xs: 'flex-start', sm: 'center' }}
              justifyContent="space-between"
              mb={2.5}
            >
              <Stack direction="row" spacing={2.5} alignItems="center">
                {previewData.artwork_url && (
                  <Avatar
                    src={previewData.thumbnail_url || previewData.artwork_url}
                    variant="rounded"
                    sx={{ width: 92, height: 92, boxShadow: '0 4px 12px rgba(0,0,0,0.15)' }}
                  />
                )}
                <Box>
                  <Typography variant="h5" sx={{ fontWeight: 800 }}>
                    {previewData.name}
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                    {previewData.tracks?.length || 0} tracce · {previewData.description || 'Nessuna descrizione'}
                  </Typography>
                </Box>
              </Stack>

              {/* Summary Stats */}
              {previewData.tracks && (
                <Stack direction="row" spacing={1} flexWrap="wrap" alignItems="center">
                  {(() => {
                    const stats = getPlaylistStats(previewData.tracks);
                    return (
                      <>
                        <Chip
                          label={`${stats.inLib} in Libreria`}
                          color="success"
                          size="small"
                          sx={{ fontWeight: 700 }}
                        />
                        {stats.inExplore > 0 && (
                          <Chip
                            label={`${stats.inExplore} in Explore`}
                            color="info"
                            size="small"
                            sx={{ fontWeight: 700 }}
                          />
                        )}
                        <Chip
                          label={`${stats.missing} Mancanti`}
                          color={stats.missing === 0 ? 'default' : 'error'}
                          size="small"
                          sx={{ fontWeight: 700 }}
                        />
                        <Tooltip title="Ricarica stato libreria">
                          <IconButton
                            size="small"
                            onClick={() => checkTracksBatch(previewData.tracks)}
                            disabled={checkingStatus}
                          >
                            <RefreshIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </>
                    );
                  })()}
                </Stack>
              )}
            </Stack>

            <Divider sx={{ my: 1.5 }} />

            {/* Tracklist with Status and Manual Search */}
            <List dense sx={{ maxHeight: 460, overflowY: 'auto' }}>
              {previewData.tracks?.map((track: TrackItem, index: number) => (
                <ListItem
                  key={`${track.artist}-${track.title}-${index}`}
                  divider
                  secondaryAction={
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Tooltip title="Cerca e scarica brano da Soulseek">
                        <Button
                          size="small"
                          variant="outlined"
                          color="primary"
                          startIcon={<DownloadIcon fontSize="small" />}
                          onClick={() => handleOpenManualSearch(track)}
                          sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600, py: 0.3 }}
                        >
                          Manual Grab
                        </Button>
                      </Tooltip>
                    </Stack>
                  }
                >
                  <ListItemAvatar sx={{ minWidth: 40 }}>
                    <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 700 }}>
                      #{index + 1}
                    </Typography>
                  </ListItemAvatar>
                  <ListItemText
                    primary={
                      <Box display="flex" alignItems="center" gap={1.2} flexWrap="wrap">
                        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                          {track.artist} — {track.title}
                        </Typography>
                        {renderTrackStatusChip(track)}
                      </Box>
                    }
                    secondary={track.album || undefined}
                  />
                </ListItem>
              ))}
            </List>
          </CardContent>
        </Card>
      )}

      {/* Saved Imported Playlists Section */}
      {saved.length > 0 && (
        <Card sx={{ borderRadius: 3, boxShadow: '0 4px 20px rgba(0,0,0,0.06)' }}>
          <CardContent>
            <Box display="flex" justifyContent="space-between" alignItems="center" mb={1.5}>
              <Box>
                <Typography variant="h6" fontWeight={800}>
                  Imported playlists
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Gestisci metadati, controlla lo stato dei singoli brani o effettua download manuali.
                </Typography>
              </Box>
            </Box>

            <List disablePadding>
              {saved.map(item => {
                const isExpanded = !!expandedPlaylists[item.id];
                const stats = getPlaylistStats(item.tracks || []);

                return (
                  <Paper
                    key={item.id}
                    variant="outlined"
                    sx={{
                      mb: 2,
                      borderRadius: 2.5,
                      overflow: 'hidden',
                      borderColor: theme.palette.divider,
                    }}
                  >
                    <ListItem
                      sx={{ py: 1.5, px: 2 }}
                      secondaryAction={
                        <Stack direction="row" spacing={0.5} alignItems="center">
                          <Tooltip title="Sincronizza playlist">
                            <IconButton aria-label="Sync playlist" onClick={() => handleSyncPlaylist(item)}>
                              <SyncIcon />
                            </IconButton>
                          </Tooltip>
                          <Tooltip title="Modifica playlist">
                            <IconButton aria-label="Edit playlist" onClick={() => setEditing(structuredClone(item))}>
                              <EditIcon />
                            </IconButton>
                          </Tooltip>
                          <Tooltip title="Elimina playlist">
                            <IconButton
                              aria-label="Delete playlist"
                              color="error"
                              onClick={() => setDeleteTarget(item)}
                            >
                              <DeleteIcon />
                            </IconButton>
                          </Tooltip>
                          <IconButton
                            aria-label="Toggle tracks"
                            onClick={() => toggleExpand(item.id, item.tracks)}
                          >
                            {isExpanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                          </IconButton>
                        </Stack>
                      }
                    >
                      <ListItemAvatar>
                        <Avatar
                          src={item.thumbnail_url || item.artwork_url}
                          variant="rounded"
                          sx={{ width: 56, height: 56, mr: 1, boxShadow: '0 2px 8px rgba(0,0,0,0.1)' }}
                        />
                      </ListItemAvatar>
                      <ListItemText
                        primary={
                          <Box display="flex" alignItems="center" gap={1.2} flexWrap="wrap">
                            <Typography variant="subtitle1" fontWeight={800}>
                              {item.name}
                            </Typography>
                            <Chip
                              label={`${stats.inLib}/${stats.total} in Libreria`}
                              color={stats.inLib === stats.total ? 'success' : 'default'}
                              size="small"
                              sx={{ fontWeight: 700, height: 20, fontSize: '0.7rem' }}
                            />
                            {stats.inExplore > 0 && (
                              <Chip
                                label={`${stats.inExplore} in Explore`}
                                color="info"
                                size="small"
                                sx={{ fontWeight: 700, height: 20, fontSize: '0.7rem' }}
                              />
                            )}
                            {stats.missing > 0 && (
                              <Chip
                                label={`${stats.missing} Mancanti`}
                                color="error"
                                size="small"
                                sx={{ fontWeight: 700, height: 20, fontSize: '0.7rem' }}
                              />
                            )}
                          </Box>
                        }
                        secondary={
                          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                            {item.tracks?.length || 0} tracce · {item.description || 'Nessuna descrizione'}
                          </Typography>
                        }
                      />
                    </ListItem>

                    {/* Expandable Track List */}
                    <Collapse in={isExpanded} timeout="auto" unmountOnExit>
                      <Divider />
                      <Box sx={{ p: 2, bgcolor: theme.palette.mode === 'dark' ? '#18171f' : '#fafafa' }}>
                        <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
                          <Typography variant="subtitle2" fontWeight={750} color="text.secondary">
                            Tracce della playlist
                          </Typography>
                          <Tooltip title="Aggiorna stato tracce">
                            <IconButton
                              size="small"
                              onClick={() => checkTracksBatch(item.tracks)}
                              disabled={checkingStatus}
                            >
                              <RefreshIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        </Box>
                        <List dense disablePadding sx={{ maxHeight: 380, overflowY: 'auto' }}>
                          {item.tracks?.map((track: TrackItem, idx: number) => (
                            <ListItem
                              key={`${track.artist}-${track.title}-${idx}`}
                              divider={idx < item.tracks.length - 1}
                              secondaryAction={
                                <Tooltip title="Cerca e scarica brano da Soulseek">
                                  <Button
                                    size="small"
                                    variant="outlined"
                                    color="primary"
                                    startIcon={<DownloadIcon fontSize="small" />}
                                    onClick={() => handleOpenManualSearch(track)}
                                    sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600, py: 0.2 }}
                                  >
                                    Manual Grab
                                  </Button>
                                </Tooltip>
                              }
                            >
                              <ListItemAvatar sx={{ minWidth: 36 }}>
                                <Typography variant="caption" color="text.secondary" fontWeight={700}>
                                  #{idx + 1}
                                </Typography>
                              </ListItemAvatar>
                              <ListItemText
                                primary={
                                  <Box display="flex" alignItems="center" gap={1.2} flexWrap="wrap">
                                    <Typography variant="body2" fontWeight={700}>
                                      {track.artist} — {track.title}
                                    </Typography>
                                    {renderTrackStatusChip(track)}
                                  </Box>
                                }
                                secondary={track.album || undefined}
                              />
                            </ListItem>
                          ))}
                        </List>
                      </Box>
                    </Collapse>
                  </Paper>
                );
              })}
            </List>
          </CardContent>
        </Card>
      )}

      {/* Edit Playlist Card */}
      {editing && (
        <Card sx={{ borderRadius: 3, boxShadow: '0 4px 24px rgba(0,0,0,0.1)' }}>
          <CardContent sx={{ display: 'flex', flexDirection: 'column', gap: 2.5 }}>
            <Typography variant="h6" fontWeight={800}>
              Modifica playlist: {editing.name}
            </Typography>
            <Stack direction="row" spacing={2.5} alignItems="center">
              <Avatar
                src={editing.thumbnail_url || editing.artwork_url}
                variant="rounded"
                sx={{ width: 80, height: 80, boxShadow: '0 2px 8px rgba(0,0,0,0.15)' }}
              />
              <TextField
                label="Nome playlist"
                value={editing.name}
                onChange={e => setEditing({ ...editing, name: e.target.value })}
                fullWidth
              />
            </Stack>
            <TextField
              label="Descrizione"
              value={editing.description || ''}
              onChange={e => setEditing({ ...editing, description: e.target.value })}
              fullWidth
            />
            <TextField
              label="Artwork URL"
              value={editing.artwork_url || ''}
              onChange={e => setEditing({ ...editing, artwork_url: e.target.value, thumbnail_url: '' })}
              fullWidth
            />

            <Divider />
            <Typography variant="subtitle1" fontWeight={750}>
              Aggiungi traccia manualmente
            </Typography>
            <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5}>
              <TextField
                label="Artista"
                value={newArtist}
                onChange={e => setNewArtist(e.target.value)}
                sx={{ flex: 1 }}
              />
              <TextField
                label="Titolo"
                value={newTitle}
                onChange={e => setNewTitle(e.target.value)}
                sx={{ flex: 1 }}
              />
              <TextField
                label="Album (opzionale)"
                value={newAlbum}
                onChange={e => setNewAlbum(e.target.value)}
                sx={{ flex: 1 }}
              />
              <Button
                variant="outlined"
                startIcon={<AddIcon />}
                onClick={addTrackToEditing}
                sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600, px: 3 }}
              >
                Aggiungi
              </Button>
            </Stack>

            <Typography variant="subtitle2" color="text.secondary" sx={{ mt: 1 }}>
              Tracce nella playlist ({editing.tracks?.length || 0})
            </Typography>
            <List dense sx={{ maxHeight: 360, overflowY: 'auto', border: `1px solid ${theme.palette.divider}`, borderRadius: 2 }}>
              {editing.tracks?.map((track: TrackItem, index: number) => (
                <ListItem
                  key={`${track.artist}-${track.title}-${index}`}
                  divider={index < editing.tracks.length - 1}
                  secondaryAction={
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Tooltip title="Cerca e scarica brano">
                        <IconButton size="small" onClick={() => handleOpenManualSearch(track)}>
                          <DownloadIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Rimuovi traccia">
                        <IconButton
                          size="small"
                          color="error"
                          onClick={() =>
                            setEditing({
                              ...editing,
                              tracks: editing.tracks.filter((_: any, i: number) => i !== index),
                            })
                          }
                        >
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </Stack>
                  }
                >
                  <ListItemAvatar sx={{ minWidth: 36 }}>
                    <Typography variant="caption" color="text.secondary" fontWeight={700}>
                      #{index + 1}
                    </Typography>
                  </ListItemAvatar>
                  <ListItemText
                    primary={
                      <Box display="flex" alignItems="center" gap={1.2} flexWrap="wrap">
                        <Typography variant="body2" fontWeight={700}>
                          {track.artist} — {track.title}
                        </Typography>
                        {renderTrackStatusChip(track)}
                      </Box>
                    }
                    secondary={track.album}
                  />
                </ListItem>
              ))}
            </List>

            <Stack direction="row" justifyContent="flex-end" spacing={1.5} sx={{ mt: 1 }}>
              <Button
                onClick={() => setEditing(null)}
                sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600 }}
              >
                Annulla
              </Button>
              <Button
                variant="contained"
                onClick={saveEdits}
                disabled={busy}
                sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 700 }}
              >
                Salva playlist
              </Button>
            </Stack>
          </CardContent>
        </Card>
      )}

      {/* Manual Search Dialog (Soulseek) */}
      <Dialog
        open={manualSearchOpen}
        onClose={() => setManualSearchOpen(false)}
        maxWidth="xl"
        fullWidth
        PaperProps={{
          sx: {
            borderRadius: 4,
            background: theme.palette.mode === 'dark' ? '#1c1b22' : '#ffffff',
            boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
          },
        }}
      >
        <DialogTitle sx={{ fontWeight: 800, pb: 1 }}>
          Ricerca Soulseek: {searchTrack ? `${searchTrack.artist} — ${searchTrack.title}` : ''}
        </DialogTitle>
        <DialogContent dividers>
          {searchingCandidates ? (
            <Box display="flex" flexDirection="column" alignItems="center" justifyContent="center" py={8} gap={2}>
              <CircularProgress size={50} />
              <Typography variant="body2" color="text.secondary">
                Ricerca candidati su Soulseek in corso...
              </Typography>
            </Box>
          ) : trackCandidates.length === 0 ? (
            <Typography variant="body1" color="text.secondary" align="center" sx={{ py: 6 }}>
              Nessun candidato trovato su Soulseek per questo brano. Assicurati che Slskd sia attivo.
            </Typography>
          ) : (
            <TableContainer component={Paper} sx={{ borderRadius: 3, boxShadow: 'none', border: `1px solid ${theme.palette.divider}` }}>
              <Table size="medium">
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ fontWeight: 700 }}>Peer</TableCell>
                    <TableCell sx={{ fontWeight: 700 }}>Filename</TableCell>
                    <TableCell sx={{ fontWeight: 700 }} align="right">
                      Durata
                    </TableCell>
                    <TableCell sx={{ fontWeight: 700 }} align="right">
                      Dimensione
                    </TableCell>
                    <TableCell sx={{ fontWeight: 700 }} align="right">
                      Bitrate
                    </TableCell>
                    <TableCell sx={{ fontWeight: 700 }} align="center">
                      Azione
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {trackCandidates.map((cand, idx) => {
                    const isGrabbing = grabbingKey === `${cand.username}-${cand.filename}`;
                    return (
                      <TableRow key={idx} hover>
                        <TableCell sx={{ fontWeight: 600 }}>{cand.username}</TableCell>
                        <TableCell sx={{ maxWidth: 400, wordBreak: 'break-all' }}>
                          <Tooltip title={cand.filename}>
                            <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                              {getFilenameOnly(cand.filename)}
                            </Typography>
                          </Tooltip>
                        </TableCell>
                        <TableCell align="right">
                          {cand.length
                            ? `${Math.floor(cand.length / 60)}:${Math.floor(cand.length % 60)
                                .toString()
                                .padStart(2, '0')}`
                            : '--:--'}
                        </TableCell>
                        <TableCell align="right">{formatSize(cand.size)}</TableCell>
                        <TableCell align="right">
                          {cand.bitrate ? (
                            <Chip
                              label={`${cand.bitrate} kbps`}
                              size="small"
                              color={cand.bitrate >= 320 ? 'success' : 'default'}
                              variant="outlined"
                              sx={{ fontWeight: 600 }}
                            />
                          ) : (
                            <Typography variant="caption" color="text.secondary">
                              VBR/Sconosciuto
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell align="center">
                          <Button
                            variant="contained"
                            size="small"
                            onClick={() => handleGrabTrack(cand)}
                            disabled={grabbingKey !== null}
                            startIcon={isGrabbing ? <CircularProgress size={14} color="inherit" /> : <DownloadIcon />}
                            sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600 }}
                          >
                            {isGrabbing ? 'Scaricamento...' : 'Grab'}
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </DialogContent>
        <DialogActions sx={{ p: 2.5 }}>
          <Button
            onClick={() => setManualSearchOpen(false)}
            variant="outlined"
            sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600 }}
          >
            Chiudi
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog
        open={Boolean(deleteTarget)}
        onClose={() => setDeleteTarget(null)}
        PaperProps={{ sx: { borderRadius: 3, p: 1 } }}
      >
        <DialogTitle sx={{ fontWeight: 800 }}>Elimina playlist</DialogTitle>
        <DialogContent>
          <Typography>
            Sei sicuro di voler eliminare la playlist <strong>{deleteTarget?.name}</strong> da VeryDisco e Navidrome?
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setDeleteTarget(null)} sx={{ textTransform: 'none', fontWeight: 600 }}>
            Annulla
          </Button>
          <Button
            color="error"
            variant="contained"
            onClick={handleDeletePlaylist}
            disabled={busy}
            sx={{ textTransform: 'none', fontWeight: 700 }}
          >
            Elimina
          </Button>
        </DialogActions>
      </Dialog>

      <Alert severity="info">
        I brani nuovi scaricati per le playlist vengono salvati nell'album <strong>Playlist Tracks</strong> e collegati
        automaticamente alla playlist M3U in Navidrome, preservando le cartelle ufficiali della tua libreria.
      </Alert>
    </Box>
  );
};

export default PlaylistImport;
