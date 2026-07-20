import React, { useState } from 'react';
import {
  Box, Typography, Button, Paper, Stack, CircularProgress,
  List, ListItem, ListItemText, Divider, Alert, Chip, Switch, FormControlLabel
} from '@mui/material';
import {
  Rule as RuleIcon,
  PlayArrow as PlayIcon,
  CheckCircle as OkIcon,
} from '@mui/icons-material';
import apiService from '../api';

const NamingConvention: React.FC = () => {
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
      const data = await apiService.scanNamingConventions();
      setResults(data.mismatches || []);
      showToast(`Scan complete. Found ${data.mismatches?.length || 0} files to rename.`);
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
      const paths = results.map(r => r.current_path);
      const res = await apiService.massRenameFiles(paths, dryRun);
      showToast(`Applied renames! Successful: ${res.successful.length}, Failed: ${res.failed.length}`);
      if (!dryRun) {
        setResults(null); // Clear on actual rename
      }
    } catch (e: any) {
      showToast(e?.response?.data?.detail ?? 'Failed to apply renames.', 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Header */}
      <Box display="flex" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={2}>
        <Box display="flex" alignItems="center" gap={1.5}>
          <RuleIcon sx={{ color: 'primary.main', fontSize: 36 }} />
          <Box>
            <Typography variant="h5" sx={{ fontWeight: 800 }}>Naming Conventions</Typography>
            <Typography variant="body2" color="text.secondary">
              Scan your library for files that don't match your configured naming scheme and rename them in bulk.
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
              Found {results.length} files to rename
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
                {dryRun ? 'Test Rename' : 'Apply Renames'}
              </Button>
            )}
          </Stack>
          
          <Divider sx={{ mb: 2 }} />

          {results.length === 0 ? (
            <Typography variant="body1" color="text.secondary" sx={{ py: 4, textAlign: 'center' }}>
              Your library perfectly matches the naming convention!
            </Typography>
          ) : (
            <List sx={{ maxHeight: 600, overflow: 'auto' }}>
              {results.map((item, idx) => (
                <ListItem key={idx} divider={idx < results.length - 1} sx={{ px: 0 }}>
                  <ListItemText
                    primary={
                      <Stack direction="row" spacing={1} alignItems="center">
                        <Chip label="NEW" size="small" color="success" sx={{ height: 20, fontSize: '0.7rem', fontWeight: 700 }} />
                        <Typography variant="body2" fontWeight={700} sx={{ wordBreak: 'break-all' }}>
                          {item.expected_relative}
                        </Typography>
                      </Stack>
                    }
                    secondary={
                      <Stack direction="row" spacing={1} alignItems="center" mt={0.5}>
                        <Chip label="OLD" size="small" color="error" variant="outlined" sx={{ height: 20, fontSize: '0.7rem', fontWeight: 700 }} />
                        <Typography variant="caption" color="text.secondary" sx={{ wordBreak: 'break-all' }}>
                          {item.current_relative}
                        </Typography>
                      </Stack>
                    }
                  />
                </ListItem>
              ))}
            </List>
          )}
        </Paper>
      )}
    </Box>
  );
};

export default NamingConvention;
