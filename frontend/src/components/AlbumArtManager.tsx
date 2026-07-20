import React, { useEffect, useState } from 'react';
import {
  Box, Card, CardContent, Typography, Grid, CircularProgress,
  Alert, Button, Checkbox, FormControlLabel, Dialog, DialogTitle,
  DialogContent, DialogActions, TextField, List, ListItem, ListItemText,
  IconButton, Tooltip, Badge, Chip
} from '@mui/material';
import {
  PhotoLibrary as ArtIcon,
  Search as SearchIcon,
  CheckCircle as OkIcon,
  FolderOpen as FolderIcon,
  Close as CloseIcon
} from '@mui/icons-material';
import { apiService } from '../api';

interface MissingAlbum {
  artist_name: string;
  album_name: string;
  folder_path: string;
  folders: string[];
  bitrate: number;
  format: string;
}

interface ArtCandidate {
  artist: string;
  album: string;
  url: string;
  thumbnail: string;
  resolution: string;
  source: string;
  release_date: string;
}

const AlbumArtManager: React.FC = () => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [albums, setAlbums] = useState<MissingAlbum[]>([]);
  const [selectedAlbum, setSelectedAlbum] = useState<MissingAlbum | null>(null);

  // Search state
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<ArtCandidate[]>([]);
  const [embedTags, setEmbedTags] = useState(true);

  // Custom search override
  const [artistQuery, setArtistQuery] = useState('');
  const [albumQuery, setAlbumQuery] = useState('');

  const [scanning, setScanning] = useState(false);

  const loadMissing = () => {
    setLoading(true);
    setError(null);
    apiService.getMissingArt()
      .then(res => setAlbums(res))
      .catch(() => setError("Failed to fetch albums missing artwork."))
      .finally(() => setLoading(false));
  };

  const handleManualScan = () => {
    setScanning(true);
    setError(null);
    apiService.scanMissingArt()
      .then(res => setAlbums(res))
      .catch(() => setError("Failed to scan for missing artwork."))
      .finally(() => setScanning(false));
  };

  useEffect(() => {
    loadMissing();
  }, []);

  const handleOpenSearch = (alb: MissingAlbum) => {
    setSelectedAlbum(alb);
    setArtistQuery(alb.artist_name);
    setAlbumQuery(alb.album_name);
    setCandidates([]);
    setSearchError(null);
    setSearchOpen(true);
    triggerSearch(alb.artist_name, alb.album_name);
  };

  const triggerSearch = async (artist: string, album: string) => {
    setSearchLoading(true);
    setSearchError(null);
    try {
      const res = await apiService.searchArt(artist, album);
      setCandidates(res);
      if (res.length === 0) {
        setSearchError("No cover artwork candidates found.");
      }
    } catch (e) {
      setSearchError("Failed to fetch artwork candidates.");
    } finally {
      setSearchLoading(false);
    }
  };

  const handleSaveArt = async (candidate: ArtCandidate) => {
    if (!selectedAlbum) return;
    setSearchLoading(true);
    try {
      await apiService.saveArt({
        folder_path: selectedAlbum.folder_path,
        url: candidate.url,
        embed: embedTags
      });
      // Remove from list
      setAlbums(prev => prev.filter(a => a.folder_path !== selectedAlbum.folder_path));
      setSearchOpen(false);
      setSelectedAlbum(null);
    } catch (e) {
      setSearchError("Failed to download and apply artwork.");
    } finally {
      setSearchLoading(false);
    }
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Header */}
      <Box display="flex" justifyContent="space-between" alignItems="center">
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 800 }}>Album Art Finder</Typography>
          <Typography variant="body2" color="text.secondary">
            Identify album directories missing cover artwork and grab high-res replacements
          </Typography>
        </Box>
        <Button
          variant="contained"
          color="primary"
          onClick={handleManualScan}
          disabled={scanning || loading}
          startIcon={scanning ? <CircularProgress size={20} color="inherit" /> : <SearchIcon />}
        >
          {scanning ? 'Scanning...' : 'Manual Scan'}
        </Button>
      </Box>

      {loading ? (
        <Box display="flex" justifyContent="center" p={6}><CircularProgress /></Box>
      ) : error ? (
        <Alert severity="error">{error}</Alert>
      ) : albums.length === 0 ? (
        <Card sx={{ borderRadius: 4, textAlign: 'center', p: 6 }}>
          <OkIcon color="success" sx={{ fontSize: 56, mb: 2 }} />
          <Typography variant="h6" sx={{ fontWeight: 700 }}>No Missing Art Found</Typography>
          <Typography variant="body2" color="text.secondary">All albums in your library have folder or cover artwork, or the cache is empty. Try running a Manual Scan if you recently added files.</Typography>
        </Card>
      ) : (
        <Grid container spacing={{ xs: 2, sm: 3 }}>
          {albums.map(alb => (
            <Grid item xs={12} sm={6} md={4} key={alb.folder_path}>
              <Card sx={{ borderRadius: 4, height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'space-between', border: '1px solid', borderColor: 'divider' }}>
                <CardContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
                  <Box display="flex" justifyContent="space-between" alignItems="start">
                    <Typography variant="h6" sx={{ fontWeight: 700, fontSize: '1.1rem', lineHeight: 1.3 }} noWrap>
                      {alb.album_name}
                    </Typography>
                    <Chip label={alb.format} size="small" variant="outlined" sx={{ fontWeight: 700 }} />
                  </Box>
                  <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 600 }} noWrap>
                    {alb.artist_name}
                  </Typography>
                  <Box display="flex" alignItems="center" gap={1} sx={{ mt: 1 }}>
                    <FolderIcon fontSize="small" color="action" />
                    <Typography variant="caption" color="text.secondary" sx={{ fontFamily: 'monospace' }} noWrap>
                      {alb.folder_path.split(/[\\/]/).pop()}
                    </Typography>
                  </Box>
                </CardContent>
                <Box sx={{ p: 2, borderTop: '1px solid', borderColor: 'divider', display: 'flex', justifyContent: 'flex-end' }}>
                  <Button
                    variant="contained"
                    size="small"
                    startIcon={<SearchIcon />}
                    onClick={() => handleOpenSearch(alb)}
                    sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600 }}
                  >
                    Find Artwork
                  </Button>
                </Box>
              </Card>
            </Grid>
          ))}
        </Grid>
      )}

      {/* Art Search Modal */}
      <Dialog open={searchOpen} onClose={() => setSearchOpen(false)} maxWidth="md" fullWidth sx={{ '& .MuiDialog-paper': { borderRadius: 4 } }}>
        <DialogTitle sx={{ m: 0, p: 2, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>Find Artwork</Typography>
          <IconButton onClick={() => setSearchOpen(false)}><CloseIcon /></IconButton>
        </DialogTitle>
        <DialogContent dividers sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          {selectedAlbum && (
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center' }}>
              <TextField
                label="Artist"
                value={artistQuery}
                onChange={e => setArtistQuery(e.target.value)}
                size="small"
                sx={{ flex: 1, minWidth: 150 }}
              />
              <TextField
                label="Album"
                value={albumQuery}
                onChange={e => setAlbumQuery(e.target.value)}
                size="small"
                sx={{ flex: 1, minWidth: 150 }}
              />
              <Button
                variant="outlined"
                onClick={() => triggerSearch(artistQuery, albumQuery)}
                disabled={searchLoading}
                startIcon={<SearchIcon />}
                sx={{ borderRadius: 2 }}
              >
                Search
              </Button>
            </Box>
          )}

          <FormControlLabel
            control={<Checkbox checked={embedTags} onChange={e => setEmbedTags(e.target.checked)} />}
            label="Embed artwork inside audio files (tags)"
          />

          {searchLoading ? (
            <Box display="flex" justifyContent="center" p={4}><CircularProgress /></Box>
          ) : searchError ? (
            <Alert severity="error">{searchError}</Alert>
          ) : (
            <Grid container spacing={{ xs: 1.5, sm: 2 }}>
              {candidates.map((c, idx) => (
                <Grid item xs={12} sm={6} md={4} key={idx}>
                  <Card sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 3, overflow: 'hidden' }}>
                    <Box sx={{ position: 'relative', width: '100%', pt: '100%', bgcolor: 'grey.900' }}>
                      <img
                        src={c.thumbnail || c.url}
                        alt="cover art"
                        style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', objectFit: 'cover' }}
                      />
                      <Box sx={{ position: 'absolute', top: 8, right: 8, display: 'flex', gap: 1 }}>
                        <Chip label={c.source} size="small" color="primary" sx={{ fontWeight: 700 }} />
                      </Box>
                      <Box sx={{ position: 'absolute', bottom: 8, left: 8 }}>
                        <Chip label={c.resolution} size="small" variant="filled" sx={{ bgcolor: 'rgba(0,0,0,0.6)', color: '#fff', fontWeight: 700 }} />
                      </Box>
                    </Box>
                    <CardContent sx={{ p: 2, display: 'flex', flexDirection: 'column', gap: 1 }}>
                      <Typography variant="body2" sx={{ fontWeight: 700 }} noWrap>{c.album}</Typography>
                      <Typography variant="caption" color="text.secondary" noWrap>{c.artist}</Typography>
                      {c.release_date && (
                        <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5 }}>
                          Released: {c.release_date.split("T")[0]}
                        </Typography>
                      )}
                      <Button
                        variant="contained"
                        size="small"
                        onClick={() => handleSaveArt(c)}
                        sx={{ mt: 2, borderRadius: 2 }}
                        fullWidth
                      >
                        Apply Artwork
                      </Button>
                    </CardContent>
                  </Card>
                </Grid>
              ))}
            </Grid>
          )}
        </DialogContent>
      </Dialog>
    </Box>
  );
};

export default AlbumArtManager;
