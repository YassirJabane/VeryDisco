import React, { useEffect, useState } from 'react';
import { Alert, Avatar, Box, Button, Card, CardContent, CircularProgress, Divider, IconButton, List, ListItem, ListItemAvatar, ListItemText, Stack, TextField, Typography } from '@mui/material';
import { Add as AddIcon, Delete as DeleteIcon, Download as DownloadIcon, Edit as EditIcon, Link as LinkIcon, Sync as SyncIcon } from '@mui/icons-material';
import { apiService, getErrorMessage } from '../api';

const PlaylistImport: React.FC = () => {
  const [url, setUrl] = useState('');
  const [playlist, setPlaylist] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ severity: 'error' | 'success'; text: string } | null>(null);
  const [saved, setSaved] = useState<any[]>([]);
  const [editing, setEditing] = useState<any | null>(null);
  const [newArtist, setNewArtist] = useState('');
  const [newTitle, setNewTitle] = useState('');
  const [newAlbum, setNewAlbum] = useState('');

  const loadSaved = async () => {
    try { setSaved(await apiService.getImportedPlaylists()); } catch { setSaved([]); }
  };
  useEffect(() => { void loadSaved(); }, []);

  const preview = async () => {
    setBusy(true); setMessage(null);
    try { setPlaylist(await apiService.previewSpotifyPlaylist(url)); }
    catch (e) { setMessage({ severity: 'error', text: getErrorMessage(e, 'Playlist Spotify non disponibile.') }); }
    finally { setBusy(false); }
  };

  const importPlaylist = async () => {
    setBusy(true); setMessage(null);
    try {
      const result = await apiService.importSpotifyPlaylist(url);
      setMessage({ severity: 'success', text: `Import avviato: ${result.name}. Controlla Attività per l'avanzamento.` });
      await loadSaved();
    } catch (e) { setMessage({ severity: 'error', text: getErrorMessage(e, 'Import Spotify non avviato.') }); }
    finally { setBusy(false); }
  };

  const addTrack = () => {
    if (!editing || !newArtist.trim() || !newTitle.trim()) return;
    setEditing({ ...editing, tracks: [...editing.tracks, { artist: newArtist.trim(), title: newTitle.trim(), album: newAlbum.trim(), duration: null }] });
    setNewArtist(''); setNewTitle(''); setNewAlbum('');
  };

  const saveEdits = async () => {
    if (!editing) return;
    setBusy(true);
    try {
      await apiService.updateImportedPlaylist(editing.id, editing);
      setMessage({ severity: 'success', text: `Playlist “${editing.name}” salvata.` });
      setEditing(null); await loadSaved();
    } catch (error) { setMessage({ severity: 'error', text: getErrorMessage(error, 'Playlist non salvata.') }); }
    finally { setBusy(false); }
  };

  return <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
    <Box><Typography variant="h4" sx={{ fontWeight: 800 }}>Playlist importing</Typography><Typography color="text.secondary">Importa una playlist Spotify in Navidrome usando il downloader di VeryDisco.</Typography></Box>
    {message && <Alert severity={message.severity}>{message.text}</Alert>}
    <Card><CardContent sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <TextField label="Spotify playlist link" placeholder="https://open.spotify.com/playlist/..." value={url} onChange={e => setUrl(e.target.value)} fullWidth InputProps={{ startAdornment: <LinkIcon sx={{ mr: 1, color: 'text.secondary' }} /> }} />
      <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap' }}><Button variant="outlined" onClick={preview} disabled={busy || !url.trim()}>{busy ? <CircularProgress size={20} /> : 'Preview playlist'}</Button>{playlist && <Button variant="contained" startIcon={<DownloadIcon />} onClick={importPlaylist} disabled={busy}>Import and download</Button>}</Box>
    </CardContent></Card>
    {playlist && <Card><CardContent><Stack direction="row" spacing={2} alignItems="center">{playlist.artwork_url && <Avatar src={playlist.artwork_url} variant="rounded" sx={{ width: 88, height: 88 }} />}<Box><Typography variant="h6" sx={{ fontWeight: 750 }}>{playlist.name}</Typography><Typography variant="body2" color="text.secondary">{playlist.track_count} tracce · i brani già presenti vengono riutilizzati.</Typography></Box></Stack><List dense sx={{ maxHeight: 420, overflowY: 'auto' }}>{playlist.tracks.map((track: any, index: number) => <ListItem key={`${track.artist}-${track.title}-${index}`}><ListItemText primary={`${track.artist} — ${track.title}`} secondary={track.album || undefined} /></ListItem>)}</List></CardContent></Card>}

    {saved.length > 0 && <Card><CardContent><Typography variant="h6" fontWeight={800}>Imported playlists</Typography><Typography variant="body2" color="text.secondary" mb={1}>Edit metadata and tracks, then sync only the additions.</Typography><List>{saved.map(item => <ListItem key={item.id} divider secondaryAction={<Stack direction="row"><IconButton aria-label="Edit playlist" onClick={() => setEditing(structuredClone(item))}><EditIcon /></IconButton><IconButton aria-label="Sync playlist" onClick={async () => { try { const result = await apiService.syncImportedPlaylist(item.id); setMessage({ severity: 'success', text: result.message }); } catch (error) { setMessage({ severity: 'error', text: getErrorMessage(error) }); } }}><SyncIcon /></IconButton></Stack>}><ListItemAvatar><Avatar src={item.artwork_url} variant="rounded" /></ListItemAvatar><ListItemText primary={item.name} secondary={`${item.tracks.length} tracks · ${item.description || 'No description'}`} /></ListItem>)}</List></CardContent></Card>}

    {editing && <Card><CardContent sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}><Stack direction="row" spacing={2} alignItems="center"><Avatar src={editing.artwork_url} variant="rounded" sx={{ width: 72, height: 72 }} /><TextField label="Playlist name" value={editing.name} onChange={event => setEditing({ ...editing, name: event.target.value })} fullWidth /></Stack><TextField label="Description" value={editing.description || ''} onChange={event => setEditing({ ...editing, description: event.target.value })} fullWidth /><TextField label="Artwork URL" value={editing.artwork_url || ''} onChange={event => setEditing({ ...editing, artwork_url: event.target.value })} fullWidth /><Divider /><Typography fontWeight={750}>Add a track</Typography><Stack direction={{ xs: 'column', md: 'row' }} spacing={1}><TextField label="Artist" value={newArtist} onChange={event => setNewArtist(event.target.value)} /><TextField label="Title" value={newTitle} onChange={event => setNewTitle(event.target.value)} /><TextField label="Album" value={newAlbum} onChange={event => setNewAlbum(event.target.value)} /><Button startIcon={<AddIcon />} onClick={addTrack}>Add</Button></Stack><List dense sx={{ maxHeight: 360, overflowY: 'auto' }}>{editing.tracks.map((track: any, index: number) => <ListItem key={`${track.artist}-${track.title}-${index}`} secondaryAction={<IconButton onClick={() => setEditing({ ...editing, tracks: editing.tracks.filter((_: any, i: number) => i !== index) })}><DeleteIcon /></IconButton>}><ListItemText primary={`${track.artist} — ${track.title}`} secondary={track.album} /></ListItem>)}</List><Stack direction="row" justifyContent="flex-end" spacing={1}><Button onClick={() => setEditing(null)}>Cancel</Button><Button variant="contained" onClick={saveEdits} disabled={busy}>Save playlist</Button></Stack></CardContent></Card>}
    <Alert severity="info">I brani nuovi vengono salvati nell'album <strong>Playlist Tracks</strong> e collegati alla playlist, senza creare album incompleti nelle cartelle degli artisti.</Alert>
  </Box>;
};

export default PlaylistImport;
