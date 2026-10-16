import React, { useState } from 'react';
import {
  Box, Typography, Button, Paper, Stack, CircularProgress,
  List, ListItem, ListItemText, Divider, Alert, Chip, Switch, FormControlLabel
} from '@mui/material';
import RuleIcon from '@mui/icons-material/Rule';
import PlayIcon from '@mui/icons-material/PlayArrow';
import OkIcon from '@mui/icons-material/CheckCircle';
import apiService from '../api';

interface NamingMismatch {
  current_path: string;
  current_relative: string;
  expected_folder: string;
  expected_filename: string;
  expected_relative: string;
  artist: string;
  album: string;
  title: string;
  track_num: number;
}

const normalizePath = (value: string) => value.replace(/\\/g, '/').replace(/\/+$/, '').toLocaleLowerCase();
const hasFolderMismatch = (item: NamingMismatch) => {
  const currentFolder = normalizePath(item.current_relative).replace(/\/[^/]+$/, '');
  const expectedFolder = normalizePath(item.expected_folder);
  return Boolean(expectedFolder) && currentFolder !== expectedFolder && !currentFolder.endsWith(`/${expectedFolder}`);
};

const NamingConvention: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [results, setResults] = useState<NamingMismatch[] | null>(null);
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
      const count = data.mismatches?.length || 0;
      showToast(`Scan complete. Found ${count} file${count === 1 ? '' : 's'} to rename.`);
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
      const successful = res.renamed?.length || 0;
      const failed = res.errors?.length || 0;
      showToast(
        dryRun
          ? `Preview complete: ${successful} rename${successful === 1 ? '' : 's'} ready${failed ? `, ${failed} failed` : ''}.`
          : `Rename complete: ${successful} succeeded${failed ? `, ${failed} failed` : ''}.`,
        failed ? 'error' : 'success',
      );
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
      <Box display="flex" justifyContent="space-between" alignItems={{ xs: 'stretch', md: 'center' }} flexDirection={{ xs: 'column', md: 'row' }} gap={2}>
        <Box display="flex" alignItems="center" gap={1.5}>
          <RuleIcon sx={{ color: 'primary.main', fontSize: 36 }} />
          <Box>
            <Typography variant="h5" sx={{ fontWeight: 800 }}>Naming Conventions</Typography>
            <Typography variant="body2" color="text.secondary">
              Scan your library for files that don't match your configured naming scheme and rename them in bulk.
            </Typography>
          </Box>
        </Box>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ xs: 'stretch', sm: 'center' }}>
          <FormControlLabel
            control={<Switch checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} color="primary" />}
            label="Preview changes only"
            sx={{ m: 0 }}
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
        <Alert severity={toast.type} onClose={() => setToast(null)} sx={{ borderRadius: 2 }} role="status">
          {toast.msg}
        </Alert>
      )}

      {/* Results Card */}
      {results !== null && (
        <Paper sx={{ p: { xs: 2, sm: 3 }, borderRadius: 4, border: '1px solid', borderColor: 'divider', boxShadow: 'none' }}>
          <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ xs: 'stretch', sm: 'center' }} gap={1.5} mb={2}>
            <Box>
              <Typography variant="h6" fontWeight={700}>
                {results.length === 0 ? 'Naming check complete' : `${results.length} file${results.length === 1 ? ' needs' : 's need'} attention`}
              </Typography>
              {results.length > 0 && <Typography variant="body2" color="text.secondary">Every mismatched folder and filename is listed below before anything is changed.</Typography>}
            </Box>
            {results.length > 0 && (
              <Button
                variant="contained"
                color="secondary"
                startIcon={<OkIcon />}
                onClick={handleApply}
                disabled={loading}
                sx={{ borderRadius: 2, fontWeight: 700, textTransform: 'none' }}
              >
                {dryRun ? 'Preview renames' : 'Rename files'}
              </Button>
            )}
          </Stack>
          
          <Divider sx={{ mb: 2 }} />

          {results.length === 0 ? (
            <Typography variant="body1" color="text.secondary" sx={{ py: 4, textAlign: 'center' }}>
              Your library perfectly matches the naming convention!
            </Typography>
          ) : (
            <List sx={{ maxHeight: 600, overflow: 'auto' }} aria-label="Files with naming mismatches">
              {results.map((item, idx) => (
                <ListItem key={item.current_path || `${item.expected_relative}-${idx}`} divider={idx < results.length - 1} sx={{ px: 0, alignItems: 'flex-start' }}>
                  <ListItemText
                    primary={
                      <Stack spacing={0.75}>
                        <Stack direction="row" spacing={1} alignItems="flex-start">
                          <Chip label="CURRENT" size="small" color="error" variant="outlined" sx={{ height: 20, fontSize: '0.65rem', fontWeight: 700, mt: 0.1 }} />
                          <Typography variant="body2" color="text.secondary" sx={{ wordBreak: 'break-all', fontFamily: 'monospace' }}>
                            {item.current_relative}
                          </Typography>
                        </Stack>
                        <Stack direction="row" spacing={1} alignItems="flex-start">
                          <Chip label="EXPECTED" size="small" color="success" sx={{ height: 20, fontSize: '0.65rem', fontWeight: 700, mt: 0.1 }} />
                          <Typography variant="body2" fontWeight={700} sx={{ wordBreak: 'break-all', fontFamily: 'monospace' }}>
                            {item.expected_relative}
                          </Typography>
                        </Stack>
                      </Stack>
                    }
                    secondary={
                      <Stack direction="row" spacing={0.75} mt={1} flexWrap="wrap" useFlexGap>
                        {hasFolderMismatch(item) && <Chip label="Folder path" size="small" variant="outlined" />}
                        {item.current_relative.split(/[\\/]/).pop() !== item.expected_filename && <Chip label="Filename" size="small" variant="outlined" />}
                        {(item.artist || item.title) && <Typography variant="caption" color="text.secondary" sx={{ alignSelf: 'center' }}>{item.artist}{item.artist && item.title ? ' — ' : ''}{item.title}</Typography>}
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
