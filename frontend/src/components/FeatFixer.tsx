import React, { useState } from 'react';
import {
  Box, Typography, Button, Paper, Stack, CircularProgress,
  List, ListItem, ListItemText, Divider, Alert, Chip, Switch, FormControlLabel
} from '@mui/material';
import {
  RecentActors as ArtistsIcon,
  PlayArrow as PlayIcon,
  CheckCircle as OkIcon,
} from '@mui/icons-material';
import apiService from '../api';

const FeatFixer: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [results, setResults] = useState<any[] | null>(null);
  const [dryRun, setDryRun] = useState(true);

  const showToast = (msg: string, type: 'success' | 'error' = 'success') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  };

  const handleScan = async () => {
    setLoading(true);
    setResults(null);
    try {
      const data = await apiService.scanFeatArtists();
      setResults(data.affected || []);
      showToast(`Scan complete. Found ${data.count || 0} tracks with features in the artist tag.`);
    } catch (e: any) {
      showToast(e?.response?.data?.detail ?? 'Failed to scan files.', 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleApply = async () => {
    if (!results) return;
    setLoading(true);
    try {
      const paths = results.map(r => r.path);
      const res = await apiService.fixFeatArtists(paths, dryRun);
      showToast(`Applied fixes! Successful: ${res.successful.length}, Failed: ${res.failed.length}`);
      if (!dryRun) {
        setResults(null);
      }
    } catch (e: any) {
      showToast(e?.response?.data?.detail ?? 'Failed to apply fixes.', 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Header */}
      <Box display="flex" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={2}>
        <Box display="flex" alignItems="center" gap={1.5}>
          <ArtistsIcon sx={{ color: 'primary.main', fontSize: 36 }} />
          <Box>
            <Typography variant="h5" sx={{ fontWeight: 800 }}>Feature Artist Fixer</Typography>
            <Typography variant="body2" color="text.secondary">
              Move "feat." or "ft." strings from the Artist tag into the Title tag to keep your artist index clean.
            </Typography>
          </Box>
        </Box>
        <Stack direction="row" spacing={2} alignItems="center">
          <FormControlLabel
            control={<Switch checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} color="primary" />}
            label="Dry Run (Test Only)"
          />
          <Button
            variant="contained"
            startIcon={loading ? <CircularProgress size={16} color="inherit" /> : <PlayIcon />}
            onClick={handleScan}
            disabled={loading}
            sx={{ borderRadius: 2.5, fontWeight: 700, textTransform: 'none' }}
          >
            Scan Library
          </Button>
        </Stack>
      </Box>

      {toast && (
        <Alert severity={toast.type} onClose={() => setToast(null)} sx={{ borderRadius: 2 }}>
          {toast.msg}
        </Alert>
      )}

      {/* Results Card */}
      {results !== null && (
        <Paper sx={{ p: 3, borderRadius: 4, border: '1px solid', borderColor: 'divider', boxShadow: 'none' }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" mb={2}>
            <Typography variant="h6" fontWeight={700}>
              Found {results.length} tracks to fix
            </Typography>
            {results.length > 0 && (
              <Button
                variant="contained"
                color="secondary"
                startIcon={<OkIcon />}
                onClick={handleApply}
                disabled={loading}
                sx={{ borderRadius: 2, fontWeight: 700, textTransform: 'none' }}
              >
                {dryRun ? 'Test Fixes' : 'Apply Fixes'}
              </Button>
            )}
          </Stack>
          
          <Divider sx={{ mb: 2 }} />

          {results.length === 0 ? (
            <Typography variant="body1" color="text.secondary" sx={{ py: 4, textAlign: 'center' }}>
              No tracks found with "feat." in the artist tag!
            </Typography>
          ) : (
            <List sx={{ maxHeight: 600, overflow: 'auto' }}>
              {results.map((item, idx) => (
                <ListItem key={idx} divider={idx < results.length - 1} sx={{ px: 0, flexDirection: 'column', alignItems: 'flex-start' }}>
                  <Typography variant="caption" color="text.secondary" sx={{ mb: 1, wordBreak: 'break-all' }}>
                    {item.file_path}
                  </Typography>
                  <Stack direction="row" spacing={4} width="100%">
                    <Box flex={1} sx={{ borderRight: 1, borderColor: 'divider', pr: 2 }}>
                      <Typography variant="caption" color="error.main" fontWeight={700} display="block" mb={0.5}>
                        CURRENT TAGS
                      </Typography>
                      <Typography variant="body2"><strong>Artist:</strong> {item.current_artist}</Typography>
                      <Typography variant="body2"><strong>Title:</strong> {item.current_title}</Typography>
                    </Box>
                    <Box flex={1}>
                      <Typography variant="caption" color="success.main" fontWeight={700} display="block" mb={0.5}>
                        PROPOSED TAGS
                      </Typography>
                      <Typography variant="body2"><strong>Artist:</strong> {item.proposed_artist}</Typography>
                      <Typography variant="body2"><strong>Title:</strong> {item.proposed_title}</Typography>
                    </Box>
                  </Stack>
                </ListItem>
              ))}
            </List>
          )}
        </Paper>
      )}
    </Box>
  );
};

export default FeatFixer;
