import React, { useState, useEffect } from 'react';
import {
  Box, Card, CardContent, Typography, Button, Table, TableBody,
  TableCell, TableContainer, TableHead, TableRow, Paper,
  CircularProgress, Alert, InputAdornment, TextField, List, Divider, Chip
} from '@mui/material';
import {
  Lyrics as LyricsIcon,
  Search as SearchIcon,
  Refresh as RefreshIcon
} from '@mui/icons-material';
import apiService, { MissingLyricsTrack } from '../api';
import LyricsPreviewDialog from './LyricsPreviewDialog';

export const LyricsManager: React.FC = () => {
  const [tracks, setTracks] = useState<MissingLyricsTrack[]>([]);
  const [loading, setLoading] = useState(false);
  const [filterText, setFilterText] = useState('');
  const [selectedTrack, setSelectedTrack] = useState<MissingLyricsTrack | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);

  const fetchMissing = async (forceScan: boolean = false) => {
    setLoading(true);
    try {
      if (forceScan) {
        await apiService.triggerLibraryScan();
      }
      const data = await apiService.getMissingLyrics();
      setTracks(data || []);
    } catch (err) {
      console.error('Failed to fetch missing lyrics tracks', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMissing(false);
  }, []);

  const handleOpenSearch = (track: MissingLyricsTrack) => {
    setSelectedTrack(track);
    setPreviewOpen(true);
  };

  const handleMarkInstrumental = async (filepath: string) => {
    try {
      await apiService.markInstrumental(filepath);
      setTracks((prev) =>
        prev.map((t) => (t.filepath === filepath ? { ...t, is_instrumental: true } : t))
      );
    } catch (e) {
      console.error(e);
    }
  };

  const handleSaveSuccess = () => {
    // Remove track from local state
    if (selectedTrack) {
      setTracks((prev) => prev.filter((t) => t.filepath !== selectedTrack.filepath));
    }
    setSelectedTrack(null);
  };

  const formatDuration = (secs: number) => {
    if (!secs) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  const filtered = tracks.filter((t) => {
    const q = filterText.toLowerCase();
    return (
      (t.artist || '').toLowerCase().includes(q) ||
      (t.title || '').toLowerCase().includes(q) ||
      (t.album || '').toLowerCase().includes(q)
    );
  });

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <Box display="flex" justifyContent="space-between" alignItems="flex-start" flexWrap="wrap" gap={2}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 800 }}>Lyrics Manager</Typography>
          <Typography variant="body2" color="text.secondary">
            Identify library tracks missing synced or plain lyrics sidecars (.lrc) and fetch them
          </Typography>
        </Box>
        <Button
          variant="outlined"
          startIcon={loading ? <CircularProgress size={16} color="inherit" /> : <RefreshIcon />}
          onClick={() => fetchMissing(true)}
          disabled={loading}
        >
          Refresh Scan
        </Button>
      </Box>

      <Card sx={{ borderRadius: 4 }}>
        <CardContent sx={{ p: 3 }}>
          <TextField
            fullWidth
            placeholder="Search tracks by artist, title or album..."
            variant="outlined"
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            sx={{ mb: 3 }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon />
                </InputAdornment>
              ),
            }}
          />

          {loading ? (
            <Box display="flex" justifyContent="center" py={6}>
              <CircularProgress />
            </Box>
          ) : filtered.length === 0 ? (
            <Alert severity="success" sx={{ borderRadius: 2 }}>
              {tracks.length === 0 ? 'All tracks in your library have lyrics!' : 'No matching tracks found for filter.'}
            </Alert>
          ) : (
            <>
              {/* Desktop View Table */}
              <Box sx={{ display: { xs: 'none', md: 'block' } }}>
                <TableContainer component={Paper} elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 3 }}>
                  <Table>
                    <TableHead sx={{ bgcolor: 'action.hover' }}>
                      <TableRow>
                        <TableCell sx={{ fontWeight: 700 }}>Track Title</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>Artist</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>Album</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>Duration</TableCell>
                        <TableCell sx={{ fontWeight: 700 }} align="right">Actions</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {filtered.map((track) => (
                        <TableRow key={track.filepath} hover>
                          <TableCell sx={{ fontWeight: 600 }}>{track.title}</TableCell>
                          <TableCell>{track.artist}</TableCell>
                          <TableCell>{track.album}</TableCell>
                          <TableCell>{formatDuration(track.duration)}</TableCell>
                          <TableCell align="right">
                            {track.is_instrumental ? (
                              <Chip label="Instrumental" color="default" />
                            ) : (
                              <Box display="flex" gap={1} justifyContent="flex-end" alignItems="center">
                                <Chip label="Missing" color="error" size="small" />
                                <Button
                                  variant="outlined"
                                  size="small"
                                  onClick={() => handleMarkInstrumental(track.filepath)}
                                  sx={{ textTransform: 'none' }}
                                >
                                  Mark Instrumental
                                </Button>
                                <Button
                                  variant="contained"
                                  color="primary"
                                  startIcon={<LyricsIcon />}
                                  onClick={() => handleOpenSearch(track)}
                                  sx={{ textTransform: 'none', borderRadius: 2, fontWeight: 700 }}
                                >
                                  Find Lyrics
                                </Button>
                              </Box>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              </Box>

              {/* Mobile View Card List */}
              <Box sx={{ display: { xs: 'block', md: 'none' } }}>
                <List sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  {filtered.map((track) => (
                    <Card key={track.filepath} variant="outlined" sx={{ borderRadius: 3, border: '1px solid', borderColor: 'divider' }}>
                      <CardContent sx={{ p: 2, display: 'flex', flexDirection: 'column', gap: 1.5 }}>
                        <Box>
                          <Typography variant="subtitle1" sx={{ fontWeight: 700, lineHeight: 1.2 }}>{track.title}</Typography>
                          <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 600, mt: 0.5 }}>{track.artist}</Typography>
                        </Box>
                        <Divider />
                        <Box display="flex" justifyContent="space-between" alignItems="center">
                          <Typography variant="caption" color="text.secondary" sx={{ maxWidth: '60%' }} noWrap>
                            {track.album || 'Single'} • {formatDuration(track.duration)}
                          </Typography>
                          {track.is_instrumental ? (
                            <Chip label="Instrumental" color="default" size="small" />
                          ) : (
                            <Box display="flex" gap={1} alignItems="center">
                              <Button
                                variant="outlined"
                                size="small"
                                onClick={() => handleMarkInstrumental(track.filepath)}
                                sx={{ textTransform: 'none' }}
                              >
                                Mark Instrumental
                              </Button>
                              <Button
                                variant="contained"
                                color="primary"
                                size="small"
                                startIcon={<LyricsIcon sx={{ fontSize: 16 }} />}
                                onClick={() => handleOpenSearch(track)}
                                sx={{ textTransform: 'none', borderRadius: 2, fontWeight: 700 }}
                              >
                                Find Lyrics
                              </Button>
                            </Box>
                          )}
                        </Box>
                      </CardContent>
                    </Card>
                  ))}
                </List>
              </Box>
            </>
          )}
        </CardContent>
      </Card>

      {selectedTrack && (
        <LyricsPreviewDialog
          open={previewOpen}
          onClose={() => {
            setPreviewOpen(false);
            setSelectedTrack(null);
          }}
          defaultArtist={selectedTrack.artist}
          defaultTitle={selectedTrack.title}
          localDuration={selectedTrack.duration}
          filepath={selectedTrack.filepath}
          onSaveSuccess={handleSaveSuccess}
        />
      )}
    </Box>
  );
};

export default LyricsManager;
