import React, { useState, useEffect, useCallback } from 'react';
import {
  Box, Typography, Button, Chip, CircularProgress, Alert,
  Card, CardContent, Grid, LinearProgress, Stack, Paper, Divider,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField, List, ListItem, ListItemText
} from '@mui/material';
import {
  Fingerprint as FingerprintIcon,
  Refresh as RefreshIcon,
  CheckCircle as OkIcon,
  Warning as WarnIcon,
  Info as InfoIcon
} from '@mui/icons-material';
import apiService, { AcoustidStats } from '../api';

export const AcoustIDManager: React.FC = () => {
  const [acoustidStats, setAcoustidStats] = useState<AcoustidStats | null>(null);
  const [scanning, setScanning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [failureToFix, setFailureToFix] = useState<any | null>(null);
  const [fixArtist, setFixArtist] = useState('');
  const [fixTitle, setFixTitle] = useState('');
  const [fixAlbum, setFixAlbum] = useState('');
  const [queueingFix, setQueueingFix] = useState(false);

  const fetchStats = useCallback(async () => {
    try {
      const stats = await apiService.getAcoustidStats();
      setAcoustidStats(stats);
      if (stats.running) {
        setScanning(true);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  const handleStartScan = async (batchSize: number = 50) => {
    setScanning(true);
    try {
      await apiService.acoustidScan(batchSize);
      setToast({ msg: `AcoustID verification scan started for a batch of ${batchSize} tracks.`, type: 'success' });
    } catch (e: any) {
      setScanning(false);
      setToast({ msg: e?.response?.data?.detail ?? 'Failed to start AcoustID scan.', type: 'error' });
    }
  };

  const openFailureFix = (failure: any) => {
    setFailureToFix(failure);
    setFixArtist(failure.artist || '');
    setFixTitle(failure.title || '');
    setFixAlbum(failure.album || '');
  };

  const queueReplacement = async () => {
    if (!failureToFix) return;
    setQueueingFix(true);
    try {
      const result = await apiService.replaceLibraryTrack(failureToFix.file_path, fixArtist, fixTitle, fixAlbum);
      setToast({ msg: result.message, type: 'success' });
      setFailureToFix(null);
    } catch (error: any) {
      setToast({ msg: error?.response?.data?.detail || 'Unable to queue replacement.', type: 'error' });
    } finally {
      setQueueingFix(false);
    }
  };

  useEffect(() => {
    fetchStats();
  }, [fetchStats]);

  useEffect(() => {
    let interval: any = null;
    if (scanning) {
      interval = setInterval(async () => {
        try {
          const stats = await apiService.getAcoustidStats();
          setAcoustidStats(stats);
          if (!stats.running) {
            setScanning(false);
          }
        } catch {
          setScanning(false);
        }
      }, 3000);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [scanning]);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Header */}
      <Box display="flex" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={2}>
        <Box display="flex" alignItems="center" gap={1.5}>
          <FingerprintIcon sx={{ color: 'primary.main', fontSize: 36 }} />
          <Box>
            <Typography variant="h5" sx={{ fontWeight: 800 }}>AcoustID Verification</Typography>
            <Typography variant="body2" color="text.secondary">
              Cross-reference audio fingerprints with Chromaprint & AcoustID database to ensure metadata accuracy.
            </Typography>
          </Box>
        </Box>
        <Button
          variant="outlined"
          startIcon={loading ? <CircularProgress size={16} color="inherit" /> : <RefreshIcon />}
          onClick={fetchStats}
          disabled={loading}
          sx={{ borderRadius: 2.5, fontWeight: 700, textTransform: 'none' }}
        >
          Refresh Stats
        </Button>
      </Box>

      {toast && (
        <Alert severity={toast.type} onClose={() => setToast(null)} sx={{ borderRadius: 2 }}>
          {toast.msg}
        </Alert>
      )}

      {/* Main Stats Card */}
      <Card sx={{ borderRadius: 4, border: '1px solid', borderColor: 'divider', boxShadow: 'none' }}>
        <CardContent sx={{ p: { xs: 2.5, sm: 4 } }}>
          {loading ? (
            <Box display="flex" justifyContent="center" py={6}>
              <CircularProgress />
            </Box>
          ) : acoustidStats ? (
            <Grid container spacing={3}>
              <Grid item xs={12} md={7}>
                <Stack spacing={2}>
                  <Box display="flex" alignItems="center" gap={1}>
                    <Typography variant="h6" fontWeight={700}>Verification Progress</Typography>
                    {scanning && (
                      <Chip
                        icon={<CircularProgress size={12} color="inherit" />}
                        label="Scanning in Progress..."
                        color="primary"
                        size="small"
                        sx={{ fontWeight: 700 }}
                      />
                    )}
                  </Box>
                  <Typography variant="body2" color="text.secondary">
                    Fingerprinting matches your audio files against millions of registered tracks. Files with AcoustID mismatches (corrupted or mislabeled audio) are flagged in Server Health. Tracks simply not found in the AcoustID database are <strong>not</strong> flagged as issues.
                  </Typography>

                  <Box sx={{ pt: 1 }}>
                    <Stack direction="row" justifyContent="space-between" mb={1}>
                      <Typography variant="body2" fontWeight={700} color="text.secondary">
                        Overall Progress: {acoustidStats.scanned} / {acoustidStats.total} files
                      </Typography>
                      <Typography variant="body2" fontWeight={800} color="primary.main">
                        {acoustidStats.total > 0 ? Math.round((acoustidStats.scanned / acoustidStats.total) * 100) : 0}%
                      </Typography>
                    </Stack>
                    <LinearProgress
                      variant="determinate"
                      value={acoustidStats.total > 0 ? (acoustidStats.scanned / acoustidStats.total) * 100 : 0}
                      sx={{ height: 10, borderRadius: 5, bgcolor: 'divider' }}
                    />
                  </Box>
                </Stack>
              </Grid>

              <Grid item xs={12} md={5}>
                <Paper variant="outlined" sx={{ p: 3, borderRadius: 3, display: 'flex', flexDirection: 'column', gap: 2.5, height: '100%', justifyContent: 'center' }}>
                  <Stack direction="row" spacing={3} justifyContent="space-around" alignItems="center">
                    <Box textAlign="center">
                      <Typography variant="h4" fontWeight={800} color="success.main">
                        {acoustidStats.verified}
                      </Typography>
                      <Typography variant="caption" color="text.secondary" fontWeight={700}>
                        Verified
                      </Typography>
                    </Box>
                    <Divider orientation="vertical" flexItem />
                    <Box textAlign="center">
                      <Typography variant="h4" fontWeight={800} color="error.main">
                        {acoustidStats.failed}
                      </Typography>
                      <Typography variant="caption" color="text.secondary" fontWeight={700}>
                        Mismatches
                      </Typography>
                    </Box>
                    <Divider orientation="vertical" flexItem />
                    <Box textAlign="center">
                      <Typography variant="h4" fontWeight={800} color="text.secondary">
                        {acoustidStats.remaining}
                      </Typography>
                      <Typography variant="caption" color="text.secondary" fontWeight={700}>
                        Remaining
                      </Typography>
                    </Box>
                  </Stack>

                  <Divider />

                  <Stack direction="row" spacing={2} justifyContent="center" flexWrap="wrap">
                    <Button
                      variant="contained"
                      color="primary"
                      startIcon={scanning ? <CircularProgress size={16} color="inherit" /> : <FingerprintIcon />}
                      disabled={scanning || acoustidStats.remaining === 0}
                      onClick={() => handleStartScan(50)}
                      sx={{ borderRadius: 2.5, fontWeight: 700, textTransform: 'none', px: 3, py: 1 }}
                    >
                      Scan Next 50
                    </Button>
                    <Button
                      variant="outlined"
                      color="primary"
                      disabled={scanning || acoustidStats.remaining === 0}
                      onClick={() => handleStartScan(0)}
                      sx={{ borderRadius: 2.5, fontWeight: 700, textTransform: 'none', px: 3, py: 1 }}
                    >
                      Scan All Remaining ({acoustidStats.remaining})
                    </Button>
                  </Stack>
                </Paper>
              </Grid>
            </Grid>
          ) : (
            <Alert severity="warning">Unable to load AcoustID statistics.</Alert>
          )}
        </CardContent>
      </Card>

      {acoustidStats && acoustidStats.failures?.length > 0 && (
        <Card sx={{ borderRadius: 4, border: '1px solid', borderColor: 'divider', boxShadow: 'none' }}>
          <CardContent>
            <Typography variant="h6" fontWeight={800}>Failed verification</Typography>
            <Typography variant="body2" color="text.secondary" mb={2}>
              Review the detected mismatch. You can correct artist/title before safely replacing the audio; the current file remains until a verified replacement is ready.
            </Typography>
            <List disablePadding>
              {acoustidStats.failures.map((failure) => (
                <ListItem key={failure.file_path} divider secondaryAction={
                  <Button size="small" variant="outlined" onClick={() => openFailureFix(failure)}>Correct / Replace</Button>
                }>
                  <ListItemText
                    primary={`${failure.artist || 'Unknown artist'} — ${failure.title || 'Unknown title'}`}
                    secondary={<>{failure.reason}<br />{failure.suggestion}</>}
                    sx={{ pr: 18 }}
                  />
                </ListItem>
              ))}
            </List>
          </CardContent>
        </Card>
      )}

      <Dialog open={Boolean(failureToFix)} onClose={() => !queueingFix && setFailureToFix(null)} fullWidth maxWidth="sm">
        <DialogTitle>Correct AcoustID mismatch</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1.5 }}>
          <TextField label="Artist" value={fixArtist} onChange={event => setFixArtist(event.target.value)} />
          <TextField label="Title" value={fixTitle} onChange={event => setFixTitle(event.target.value)} />
          <TextField label="Album" value={fixAlbum} onChange={event => setFixAlbum(event.target.value)} />
          <Alert severity="info">{failureToFix?.reason}</Alert>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setFailureToFix(null)} disabled={queueingFix}>Cancel</Button>
          <Button variant="contained" onClick={queueReplacement} disabled={queueingFix || !fixArtist.trim() || !fixTitle.trim()}>
            {queueingFix ? 'Queueing…' : 'Replace safely'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default AcoustIDManager;
