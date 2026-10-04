import React, { useState } from 'react';
import { Alert, Box, Button, Card, CardContent, CircularProgress, List, ListItem, ListItemText, TextField, Typography } from '@mui/material';
import { Download as DownloadIcon, Link as LinkIcon } from '@mui/icons-material';
import { apiService, getErrorMessage } from '../api';

const PlaylistImport: React.FC = () => {
  const [url, setUrl] = useState('');
  const [playlist, setPlaylist] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ severity: 'error' | 'success'; text: string } | null>(null);

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
    } catch (e) { setMessage({ severity: 'error', text: getErrorMessage(e, 'Import Spotify non avviato.') }); }
    finally { setBusy(false); }
  };

  return <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
    <Box><Typography variant="h4" sx={{ fontWeight: 800 }}>Playlist importing</Typography><Typography color="text.secondary">Importa una playlist Spotify in Navidrome usando il downloader di VeryDisco.</Typography></Box>
    {message && <Alert severity={message.severity}>{message.text}</Alert>}
    <Card><CardContent sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <TextField label="Spotify playlist link" placeholder="https://open.spotify.com/playlist/..." value={url} onChange={e => setUrl(e.target.value)} fullWidth InputProps={{ startAdornment: <LinkIcon sx={{ mr: 1, color: 'text.secondary' }} /> }} />
      <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap' }}><Button variant="outlined" onClick={preview} disabled={busy || !url.trim()}>{busy ? <CircularProgress size={20} /> : 'Preview playlist'}</Button>{playlist && <Button variant="contained" startIcon={<DownloadIcon />} onClick={importPlaylist} disabled={busy}>Import and download</Button>}</Box>
    </CardContent></Card>
    {playlist && <Card><CardContent><Typography variant="h6" sx={{ fontWeight: 750 }}>{playlist.name}</Typography><Typography variant="body2" color="text.secondary">{playlist.track_count} tracce · i brani già presenti vengono riutilizzati.</Typography><List dense sx={{ maxHeight: 420, overflowY: 'auto' }}>{playlist.tracks.map((track: any, index: number) => <ListItem key={`${track.artist}-${track.title}-${index}`}><ListItemText primary={`${track.artist} — ${track.title}`} secondary={track.album || undefined} /></ListItem>)}</List></CardContent></Card>}
    <Alert severity="info">I brani nuovi vengono salvati nell'album <strong>Playlist Tracks</strong> e collegati alla playlist, senza creare album incompleti nelle cartelle degli artisti.</Alert>
  </Box>;
};

export default PlaylistImport;
