import React, { useState, useEffect, useCallback } from 'react';
import {
  Box, Typography, Button, Chip, CircularProgress, Alert,
  Card, CardContent, Grid, LinearProgress, Stack, Paper, Divider,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField, List, ListItem, ListItemText
} from '@mui/material';
import FingerprintIcon from '@mui/icons-material/Fingerprint';
import RefreshIcon from '@mui/icons-material/Refresh';
import OkIcon from '@mui/icons-material/CheckCircle';
import WarnIcon from '@mui/icons-material/Warning';
import InfoIcon from '@mui/icons-material/Info';
import apiService, { AcoustidStats } from '../api';

export const AcoustIDManager: React.FC = () => {
  const [acoustidStats, setAcoustidStats] = useState<AcoustidStats | null>(null);
  const [scanning, setScanning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' | 'info' } | null>(null);
  const [checkingPath, setCheckingPath] = useState<string | null>(null);
  const [failureToFix, setFailureToFix] = useState<any | null>(null);
  const [fixArtist, setFixArtist] = useState('');
  const [fixTitle, setFixTitle] = useState('');
  const [fixAlbum, setFixAlbum] = useState('');
  const [queueingFix, setQueueingFix] = useState(false);

  const fetchStats = useCallback(async () => {
    try {
      const stats = await apiService.getAcoustidStats();
      setAcoustidStats(stats);
      setScanning(stats.running);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  const handleStartScan = async (batchSize: number = 50, retryFailed: boolean = false) => {
    setScanning(true);
    try {
      await apiService.acoustidScan(batchSize, retryFailed);
      setToast({ msg: retryFailed ? 'Rechecking previously inconclusive and mismatched tracks. No audio will be replaced.' : batchSize === 0 ? 'AcoustID scan started for remaining new or changed library files only.' : `AcoustID verification scan started for a batch of ${batchSize} tracks.`, type: 'success' });
    } catch (e: any) {
      setScanning(false);
      setToast({ msg: e?.response?.data?.detail ?? 'Failed to start AcoustID scan.', type: 'error' });
    }
  };

  const recheckTrack = async (failure: any) => {
    setCheckingPath(failure.file_path);
    try {
      const result = await apiService.recheckAcoustid(failure.file_path);
      setToast({ msg: result.reason, type: result.status === 'verified' ? 'success' : 'info' });
      if (failureToFix?.file_path === failure.file_path) {
        setFailureToFix(result.status === 'verified' ? null : { ...failure, ...result });
      }
      await fetchStats();
    } catch (error: any) {
      setToast({ msg: error?.response?.data?.detail || 'Unable to recheck this audio file.', type: 'error' });
    } finally {
      setCheckingPath(null);
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

  const progressPercent = acoustidStats && acoustidStats.total > 0
    ? Math.min(100, Math.max(0, (acoustidStats.scanned / acoustidStats.total) * 100)) : 0;

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
                  {!!acoustidStats.inconclusive && <Chip size="small" variant="outlined" label={`${acoustidStats.inconclusive} inconclusive / not checked`} />}
                  <Typography variant="caption" color="text.secondary">
                    Indexed library files only. Explore, playlists and staging are excluded.
                  </Typography>

                  <Box sx={{ pt: 1 }}>
                    <Stack direction="row" justifyContent="space-between" mb={1}>
                      <Typography variant="body2" fontWeight={700} color="text.secondary">
                        Overall Progress: {acoustidStats.scanned} / {acoustidStats.total} files
                      </Typography>
                      <Typography variant="body2" fontWeight={800} color="primary.main">
                        {Math.round(progressPercent)}%
                      </Typography>
                    </Stack>
                    <LinearProgress
                      variant="determinate"
                      value={progressPercent}
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
                      disabled={scanning || !!checkingPath || acoustidStats.remaining === 0}
                      onClick={() => handleStartScan(50)}
                      sx={{ borderRadius: 2.5, fontWeight: 700, textTransform: 'none', px: 3, py: 1 }}
                    >
                      Scan Next 50
                    </Button>
                    <Button
                      variant="outlined"
                      color="primary"
                      disabled={scanning || !!checkingPath || acoustidStats.remaining === 0}
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
            <Stack direction="row" justifyContent="space-between" alignItems="center" gap={2} flexWrap="wrap">
              <Typography variant="h6" fontWeight={800}>Checks needing review</Typography>
              <Button startIcon={<RefreshIcon />} disabled={scanning || !!checkingPath} onClick={() => handleStartScan(0, true)}>Recheck all unsuccessful</Button>
            </Stack>
            <Typography variant="body2" color="text.secondary" mb={2}>
              Recheck the existing audio first. An unknown fingerprint is not verified and can include an altered version such as slowed audio. If you know the file is wrong, you can request a replacement manually. The current file is kept until a replacement is verified.
            </Typography>
            <List disablePadding>
              {acoustidStats.failures.map((failure) => (
                <ListItem key={failure.file_path} divider sx={{ flexWrap: 'wrap', gap: 1 }}>
                  <ListItemText
                    primary={`${failure.artist || 'Unknown artist'} — ${failure.title || 'Unknown title'}`}
                    secondary={<>{failure.reason}<br />{failure.suggestion}</>}
                  />
                  <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                    <Chip size="small" label={{ mismatch: 'Mismatch', unknown: 'Unknown fingerprint', unavailable: 'Service unavailable', skipped: 'Not checked', verified: 'Verified' }[failure.status || 'mismatch']} />
                    <Button size="small" variant="outlined" startIcon={checkingPath === failure.file_path ? <CircularProgress size={14} /> : <RefreshIcon />} disabled={scanning || !!checkingPath} onClick={() => recheckTrack(failure)}>Recheck audio</Button>
                    {['mismatch', 'unknown'].includes(failure.status || '') && <Button size="small" variant="outlined" disabled={scanning || !!checkingPath} onClick={() => openFailureFix(failure)}>Correct / Replace</Button>}
                  </Stack>
                </ListItem>
              ))}
            </List>
          </CardContent>
        </Card>
      )}

      <Dialog open={Boolean(failureToFix)} onClose={() => !queueingFix && !checkingPath && setFailureToFix(null)} fullWidth maxWidth="sm">
        <DialogTitle>Review and replace audio</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1.5 }}>
          <TextField label="Artist" value={fixArtist} onChange={event => setFixArtist(event.target.value)} />
          <TextField label="Title" value={fixTitle} onChange={event => setFixTitle(event.target.value)} />
          <TextField label="Album" value={fixAlbum} onChange={event => setFixAlbum(event.target.value)} />
          <Alert severity="info">{failureToFix?.reason}</Alert>
          {failureToFix?.status === 'unknown' && <Alert severity="warning">AcoustID could not identify this audio. This does not prove it is wrong. Request a replacement only if you have confirmed the file or version is incorrect.</Alert>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => recheckTrack(failureToFix)} disabled={queueingFix || !!checkingPath || scanning}>Recheck existing audio</Button>
          <Button onClick={() => setFailureToFix(null)} disabled={queueingFix || !!checkingPath}>Cancel</Button>
          <Button variant="contained" onClick={queueReplacement} disabled={queueingFix || !!checkingPath || !['mismatch', 'unknown'].includes(failureToFix?.status || '') || !fixArtist.trim() || !fixTitle.trim()}>
            {queueingFix ? 'Queueing…' : 'Replace safely'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default AcoustIDManager;
