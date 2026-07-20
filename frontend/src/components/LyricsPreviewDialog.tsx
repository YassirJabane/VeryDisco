import React, { useState, useEffect } from 'react';
import {
  Dialog, DialogTitle, DialogContent, DialogActions, Button,
  Box, Typography, TextField, CircularProgress, Grid, Card,
  CardContent, Stack, Chip, Divider, Paper
} from '@mui/material';
import {
  Search as SearchIcon,
  Timer as TimerIcon,
  CheckCircle as MatchIcon,
  Edit as EditIcon,
  Save as SaveIcon,
  CloudDownload as DownloadIcon,
} from '@mui/icons-material';
import apiService, { LrcLibCandidate } from '../api';

interface LyricsPreviewDialogProps {
  open: boolean;
  onClose: () => void;
  defaultArtist: string;
  defaultTitle: string;
  defaultAlbum?: string;
  localDuration?: number; // in seconds
  filepath?: string; // if saving directly to disk
  defaultLyricsText?: string;
  defaultLyricsType?: string;
  onSaveSuccess?: () => void;
  onDownloadQueued?: () => void; // if downloading from search
}

export const LyricsPreviewDialog: React.FC<LyricsPreviewDialogProps> = ({
  open,
  onClose,
  defaultArtist,
  defaultTitle,
  defaultAlbum = '',
  localDuration = 0,
  filepath,
  defaultLyricsText = '',
  defaultLyricsType = '',
  onSaveSuccess,
  onDownloadQueued,
}) => {
  const [artist, setArtist] = useState(defaultArtist);
  const [title, setTitle] = useState(defaultTitle);
  const [candidates, setCandidates] = useState<LrcLibCandidate[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedCandidate, setSelectedCandidate] = useState<LrcLibCandidate | null>(null);
  const [lyricsContent, setLyricsContent] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [saving, setSaving] = useState(false);

  const fetchCandidates = async (searchArtist: string, searchTitle: string) => {
    setLoading(true);
    setSelectedCandidate(null);
    setLyricsContent('');
    setIsEditing(false);
    try {
      const data = await apiService.searchLyrics(searchArtist, searchTitle);
      setCandidates(data || []);
      if (data && data.length > 0) {
        const sorted = [...data].sort((a, b) => {
          if (localDuration > 0) {
            const diffA = Math.abs(a.duration - localDuration);
            const diffB = Math.abs(b.duration - localDuration);
            return diffA - diffB;
          }
          return 0;
        });
        const best = sorted[0];
        setSelectedCandidate(best);
        setLyricsContent(best.syncedLyrics || best.plainLyrics || '');
      }
    } catch (err) {
      console.error('Failed to search lyrics', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) {
      setArtist(defaultArtist);
      setTitle(defaultTitle);
      if (defaultLyricsText) {
        setLyricsContent(defaultLyricsText);
        setCandidates([]);
        setSelectedCandidate(null);
      } else {
        fetchCandidates(defaultArtist, defaultTitle);
      }
    }
  }, [open, defaultArtist, defaultTitle, defaultLyricsText]);

  const handleManualSearch = (e: React.FormEvent) => {
    e.preventDefault();
    fetchCandidates(artist, title);
  };

  const handleSelectCandidate = (cand: LrcLibCandidate) => {
    setSelectedCandidate(cand);
    setLyricsContent(cand.syncedLyrics || cand.plainLyrics || '');
    setIsEditing(false);
  };

  const handleSaveToDisk = async () => {
    if (!filepath || !lyricsContent.trim()) return;
    setSaving(true);
    try {
      await apiService.saveLyrics(filepath, lyricsContent);
      if (onSaveSuccess) onSaveSuccess();
      onClose();
    } catch (err) {
      alert('Failed to save lyrics.');
    } finally {
      setSaving(false);
    }
  };

  const handleStageAndDownload = async () => {
    setSaving(true);
    try {
      await apiService.stageLyrics(defaultArtist, defaultTitle, lyricsContent);
      await apiService.downloadTrack(defaultArtist, defaultTitle, defaultAlbum, true);
      if (onDownloadQueued) onDownloadQueued();
      onClose();
    } catch (err) {
      alert('Failed to stage lyrics/queue download.');
    } finally {
      setSaving(false);
    }
  };

  const formatDuration = (secs: number) => {
    if (!secs) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  const getDurationDiffColor = (candSecs: number) => {
    if (!localDuration) return 'text.secondary';
    const diff = Math.abs(candSecs - localDuration);
    if (diff <= 5) return '#81c784';
    if (diff <= 15) return '#ffb74d';
    return '#ef5350';
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ fontWeight: 800 }}>
        Lyrics Search & Preview — {defaultArtist} - {defaultTitle}
      </DialogTitle>
      <DialogContent dividers>
        <Grid container spacing={3}>
          <Grid item xs={12} md={5} sx={{ borderRight: { md: '1px solid divider' } }}>
            <Box component="form" onSubmit={handleManualSearch} mb={3}>
              <Typography variant="subtitle2" fontWeight={700} mb={1.5}>
                Manual Search Query
              </Typography>
              <Stack spacing={1.5}>
                <TextField
                  label="Artist"
                  size="small"
                  fullWidth
                  value={artist}
                  onChange={(e) => setArtist(e.target.value)}
                />
                <TextField
                  label="Title"
                  size="small"
                  fullWidth
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
                <Button
                  type="submit"
                  variant="outlined"
                  size="medium"
                  startIcon={<SearchIcon />}
                  fullWidth
                >
                  Search LRCLIB
                </Button>
              </Stack>
            </Box>

            <Divider sx={{ mb: 2 }} />

            <Typography variant="subtitle2" fontWeight={700} mb={1.5}>
              Candidates Found ({candidates.length})
            </Typography>

            {loading ? (
              <Box display="flex" justifyContent="center" py={4}>
                <CircularProgress size={24} />
              </Box>
            ) : candidates.length === 0 ? (
              <Typography variant="body2" color="text.secondary" fontStyle="italic">
                No lyrics found matching query.
              </Typography>
            ) : (
              <Box sx={{ maxHeight: '280px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 1.5, p: 0.5 }}>
                {candidates.map((cand, idx) => {
                  const isSelected = selectedCandidate?.id === cand.id;
                  const diff = localDuration ? Math.abs(cand.duration - localDuration) : 0;
                  const candidateTitle = cand.trackName || cand.name || title || `Candidate ${idx + 1}`;
                  const candidateArtist = cand.artistName || artist || 'Unknown Artist';
                  const candidateAlbum = cand.albumName || 'Single';

                  return (
                    <Paper
                      key={cand.id || idx}
                      elevation={0}
                      onClick={() => handleSelectCandidate(cand)}
                      sx={{
                        p: 1.5,
                        cursor: 'pointer',
                        borderRadius: 2.5,
                        border: '2px solid',
                        borderColor: isSelected ? 'primary.main' : 'divider',
                        bgcolor: isSelected
                          ? ((theme: any) => theme.palette.mode === 'dark' ? 'rgba(144, 202, 249, 0.16)' : 'rgba(25, 118, 210, 0.08)')
                          : ((theme: any) => theme.palette.mode === 'dark' ? '#222129' : '#f9f9fb'),
                        transition: 'all 0.2s ease-in-out',
                        '&:hover': {
                          borderColor: 'primary.main',
                        },
                      }}
                    >
                      <Typography variant="body2" fontWeight={800} color="text.primary">
                        {candidateTitle}
                      </Typography>
                      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.3, fontWeight: 500 }}>
                        {candidateArtist} • {candidateAlbum}
                      </Typography>
                      <Box display="flex" alignItems="center" justifyContent="space-between" mt={1} flexWrap="wrap" gap={1}>
                        <Chip
                          label={cand.syncedLyrics ? 'Synced LRC' : 'Plain Text'}
                          size="small"
                          color={cand.syncedLyrics ? 'success' : 'info'}
                          sx={{ height: 20, fontSize: '0.68rem', fontWeight: 700 }}
                        />
                        <Stack direction="row" alignItems="center" spacing={0.5}>
                          <TimerIcon sx={{ fontSize: 14, color: getDurationDiffColor(cand.duration) }} />
                          <Typography variant="caption" sx={{ color: getDurationDiffColor(cand.duration), fontWeight: 700 }}>
                            {formatDuration(cand.duration)}
                          </Typography>
                          {localDuration > 0 && diff <= 5 && (
                            <MatchIcon sx={{ fontSize: 14, color: '#81c784' }} />
                          )}
                        </Stack>
                      </Box>
                    </Paper>
                  );
                })}
              </Box>
            )}
          </Grid>

          <Grid item xs={12} md={7} display="flex" flexDirection="column" sx={{ height: '480px' }}>
            <Box display="flex" justifyContent="space-between" alignItems="center" mb={1.5}>
              <Typography variant="subtitle2" fontWeight={700}>
                Lyrics Preview
              </Typography>
              <Button
                size="small"
                startIcon={<EditIcon />}
                onClick={() => setIsEditing(!isEditing)}
                variant={isEditing ? 'contained' : 'outlined'}
              >
                {isEditing ? 'Preview Mode' : 'Edit Text'}
              </Button>
            </Box>

            {isEditing ? (
              <TextField
                multiline
                fullWidth
                value={lyricsContent}
                onChange={(e) => setLyricsContent(e.target.value)}
                sx={{
                  flexGrow: 1,
                  '& .MuiInputBase-root': {
                    height: '100%',
                    fontFamily: 'JetBrains Mono, monospace',
                    fontSize: '0.85rem',
                    alignItems: 'flex-start',
                    alignContent: 'flex-start',
                  },
                }}
              />
            ) : (
              <Box
                sx={{
                  flexGrow: 1,
                  p: 2,
                  bgcolor: '#0a090d',
                  borderRadius: 2,
                  border: '1px solid divider',
                  overflowY: 'auto',
                  fontFamily: 'JetBrains Mono, monospace',
                  fontSize: '0.85rem',
                  color: '#e5e5e9',
                  whiteSpace: 'pre-wrap',
                }}
              >
                {lyricsContent.trim() ? (
                  lyricsContent
                ) : (
                  <Typography variant="body2" color="text.secondary" fontStyle="italic">
                    -- Select a candidate or edit text to view preview --
                  </Typography>
                )}
              </Box>
            )}
          </Grid>
        </Grid>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose} variant="outlined">
          Cancel
        </Button>
        {filepath ? (
          <Button
            onClick={handleSaveToDisk}
            variant="contained"
            color="success"
            startIcon={saving ? <CircularProgress size={16} color="inherit" /> : <SaveIcon />}
            disabled={saving || !lyricsContent.trim()}
          >
            Save to Disk
          </Button>
        ) : (
          <Button
            onClick={handleStageAndDownload}
            variant="contained"
            color="primary"
            startIcon={saving ? <CircularProgress size={16} color="inherit" /> : <DownloadIcon />}
            disabled={saving || !lyricsContent.trim()}
          >
            Stage & Download
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
};

export default LyricsPreviewDialog;
