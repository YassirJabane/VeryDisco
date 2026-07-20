import React, { useState } from 'react';
import {
  Box, Typography, Button, Paper, Stack, CircularProgress,
  List, ListItem, ListItemText, Divider, Alert, Switch, FormControlLabel,
  LinearProgress
} from '@mui/material';
import {
  LibraryMusic as LibraryIcon,
  PlayArrow as PlayIcon,
} from '@mui/icons-material';
import apiService from '../api';

const RetagManager: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [dryRun, setDryRun] = useState(true);
  const [updateCover, setUpdateCover] = useState(true);
  const [status, setStatus] = useState<any>(null);

  const showToast = (msg: string, type: 'success' | 'error' = 'success') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  };

  const checkStatus = async () => {
    try {
      const data = await apiService.getRetagStatus();
      const summary = data?.last_summary || (data?.running ? { status: 'running', processed: 0, total: 0, logs: [] } : null);
      setStatus(summary);
      if (data?.running) {
        setTimeout(checkStatus, 3000);
      } else {
        setLoading(false);
      }
    } catch (e) {
      console.error(e);
      setLoading(false);
    }
  };

  const handleStart = async () => {
    setLoading(true);
    try {
      await apiService.retagLibraryMusicBrainz(null, dryRun, updateCover);
      showToast(`Started MusicBrainz retag scan.`);
      checkStatus();
    } catch (e: any) {
      showToast(e?.response?.data?.detail ?? 'Failed to start retag scan.', 'error');
      setLoading(false);
    }
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Header */}
      <Box display="flex" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={2}>
        <Box display="flex" alignItems="center" gap={2}>
          <Box sx={{ p: 1.5, borderRadius: 3, bgcolor: 'primary.main', color: 'primary.contrastText', display: 'flex' }}>
            <LibraryIcon />
          </Box>
          <Box>
            <Typography variant="h5" sx={{ fontWeight: 800 }}>MusicBrainz Retagger</Typography>
            <Typography variant="body2" color="text.secondary">
              Scan your entire library and correct misspellings, update missing metadata, and optionally download high-quality cover art using MusicBrainz.
            </Typography>
          </Box>
        </Box>
        <Stack direction="row" spacing={2} alignItems="center">
          <FormControlLabel
            control={<Switch checked={updateCover} onChange={(e) => setUpdateCover(e.target.checked)} color="primary" />}
            label="Fetch Cover Art"
          />
          <FormControlLabel
            control={<Switch checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} color="primary" />}
            label="Dry Run (Test Only)"
          />
          <Button
            variant="contained"
            disabled={loading}
            startIcon={loading ? <CircularProgress size={18} color="inherit" /> : <PlayIcon />}
            onClick={handleStart}
            sx={{ borderRadius: 2.5, px: 3, py: 1, textTransform: 'none', fontWeight: 700 }}
          >
            Start Full Scan
          </Button>
        </Stack>
      </Box>

      {toast && (
        <Alert severity={toast.type} onClose={() => setToast(null)} sx={{ borderRadius: 2 }}>
          {toast.msg}
        </Alert>
      )}

      {/* Status Card */}
      {status && status.status && (
        <Paper sx={{ p: 3, borderRadius: 4, border: '1px solid', borderColor: 'divider', boxShadow: 'none' }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
            <Typography variant="h6" fontWeight={700}>
              Status: {(status.status || 'running').toUpperCase()}
            </Typography>
            <Typography variant="body2" fontWeight={800} color="primary.main">
              {(status.total || 0) > 0 ? Math.round(((status.processed || 0) / status.total) * 100) : 0}%
            </Typography>
          </Stack>
          
          <Typography variant="body2" color="text.secondary" mb={2}>
            Processed: {status.processed || 0} / {status.total || 0}
          </Typography>
          
          <LinearProgress
            variant="determinate"
            value={(status.total || 0) > 0 ? ((status.processed || 0) / status.total) * 100 : 0}
            sx={{ height: 10, borderRadius: 5, mb: 3 }}
          />

          <Divider sx={{ mb: 2 }} />

          <Typography variant="subtitle2" fontWeight={700} mb={1}>Recent Logs:</Typography>
          <Box sx={{ maxHeight: 300, overflow: 'auto', bgcolor: 'background.default', p: 2, borderRadius: 2, border: '1px solid', borderColor: 'divider' }}>
            {!(status.logs && status.logs.length) ? (
              <Typography variant="body2" color="text.secondary">No logs yet...</Typography>
            ) : (
              <List dense disablePadding>
                {status.logs.map((log: string, idx: number) => (
                  <ListItem key={idx} sx={{ px: 0, py: 0.5 }}>
                    <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem">
                      {log}
                    </Typography>
                  </ListItem>
                ))}
              </List>
            )}
          </Box>
        </Paper>
      )}
    </Box>
  );
};

export default RetagManager;
