import React, { useEffect, useState } from 'react';
import {
  Box, Card, CardContent, Typography, Button, Radio,
  CircularProgress, Alert, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Paper, Chip, List, Stack
} from '@mui/material';
import {
  Delete as DeleteIcon,
  CheckCircle as OkIcon,
  FolderZip as KeepIcon
} from '@mui/icons-material';
import { apiService } from '../api';
import { useNotification } from '../context/NotificationContext';

interface DuplicateTrack {
  path: string;
  artist: string;
  title: string;
  album: string;
  size: number;
  bitrate: number;
  format: string;
}

interface DuplicateGroup {
  key: string;
  artist: string;
  title: string;
  best_track: DuplicateTrack;
  tracks: DuplicateTrack[];
}

const DuplicatesManager: React.FC = () => {
  const { notify, confirm } = useNotification();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [groups, setGroups] = useState<DuplicateGroup[]>([]);
  const [deleting, setDeleting] = useState(false);

  // Map of group.key -> path of the track the user wants to KEEP
  const [keptPaths, setKeptPaths] = useState<Record<string, string>>({});

  const loadDuplicates = () => {
    setLoading(true);
    setError(null);
    apiService.getDuplicates()
      .then(res => {
        setGroups(res || []);
        // Pre-select the recommended winner (index 0) to keep for each group
        const initialKept: Record<string, string> = {};
        (res || []).forEach((group: DuplicateGroup) => {
          if (group.tracks && group.tracks.length > 0) {
            initialKept[group.key] = group.tracks[0].path;
          }
        });
        setKeptPaths(initialKept);
      })
      .catch(() => setError("Failed to scan library for duplicate tracks."))
      .finally(() => setLoading(false));
  };

  const [scanning, setScanning] = useState(false);

  useEffect(() => {
    loadDuplicates();
  }, []);

  const handleManualScan = () => {
    setScanning(true);
    setError(null);
    apiService.scanDuplicates()
      .then(res => {
        setGroups(res || []);
        const initialKept: Record<string, string> = {};
        (res || []).forEach((group: DuplicateGroup) => {
          if (group.tracks && group.tracks.length > 0) {
            initialKept[group.key] = group.tracks[0].path;
          }
        });
        setKeptPaths(initialKept);
      })
      .catch(() => setError("Failed to scan library for duplicate tracks."))
      .finally(() => setScanning(false));
  };

  const handleSelectKeeper = (groupKey: string, trackPath: string) => {
    setKeptPaths(prev => ({
      ...prev,
      [groupKey]: trackPath
    }));
  };

  const handleResolveGroup = async (group: DuplicateGroup) => {
    const keeperPath = keptPaths[group.key];
    const pathsToDelete = group.tracks
      .map(t => t.path)
      .filter(p => p !== keeperPath);

    if (pathsToDelete.length === 0) {
      notify("No duplicate files to clean in this group.", "info");
      return;
    }

    confirm({
      title: 'Clean Duplicate Tracks',
      message: `Delete ${pathsToDelete.length} duplicate file(s) and keep the selected track? Navidrome playlists will automatically link to the kept track.`,
      confirmText: 'Delete Duplicates',
      onConfirm: async () => {
        setDeleting(true);
        try {
          await apiService.resolveDuplicates(pathsToDelete);
          notify(`Deleted ${pathsToDelete.length} duplicate file(s).`, "success");
          loadDuplicates();
        } catch {
          setError("Failed to delete selected duplicates.");
          notify("Failed to delete selected duplicates.", "error");
        } finally {
          setDeleting(false);
        }
      }
    });
  };

  const handleResolveAll = async () => {
    const allPathsToDelete: string[] = [];
    groups.forEach(group => {
      const keeperPath = keptPaths[group.key];
      group.tracks.forEach(t => {
        if (t.path !== keeperPath) {
          allPathsToDelete.push(t.path);
        }
      });
    });

    if (allPathsToDelete.length === 0) {
      notify("No duplicate files to clean.", "info");
      return;
    }

    confirm({
      title: 'Clean ALL Library Duplicates',
      message: `Clean ALL ${allPathsToDelete.length} duplicate files across your library? Kept files will remain untouched and playlist links will be preserved.`,
      confirmText: 'Clean All Duplicates',
      onConfirm: async () => {
        setDeleting(true);
        try {
          await apiService.resolveDuplicates(allPathsToDelete);
          notify(`Cleaned all ${allPathsToDelete.length} duplicate file(s).`, "success");
          loadDuplicates();
        } catch {
          setError("Failed to resolve duplicate files.");
          notify("Failed to resolve duplicate files.", "error");
        } finally {
          setDeleting(false);
        }
      }
    });
  };

  const formatSize = (bytes: number) => {
    const mb = bytes / (1024 * 1024);
    return `${mb.toFixed(2)} MB`;
  };

  const cardBg = React.useMemo(() => (theme: any) => theme.palette.mode === 'dark' ? '#1c1b22' : '#ffffff', []);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Header */}
      <Box display="flex" justifyContent="space-between" alignItems="flex-start" flexWrap="wrap" gap={2}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 800 }}>Duplicate Cleaner</Typography>
          <Typography variant="body2" color="text.secondary">
            Select which track version you want to KEEP. Non-selected duplicates will be safely purged.
          </Typography>
        </Box>
        <Box display="flex" gap={2} flexWrap="wrap">
          <Button
            variant="outlined"
            color="primary"
            onClick={handleManualScan}
            disabled={scanning || loading}
            startIcon={scanning ? <CircularProgress size={18} color="inherit" /> : null}
            sx={{ borderRadius: 2.5, textTransform: 'none', fontWeight: 700, px: 3, py: 1 }}
          >
            {scanning ? 'Scanning...' : 'Manual Scan'}
          </Button>
          {groups.length > 0 && (
            <Button
              variant="contained"
              color="error"
              startIcon={deleting ? <CircularProgress size={18} color="inherit" /> : <DeleteIcon />}
              onClick={handleResolveAll}
              disabled={deleting}
              sx={{ borderRadius: 2.5, textTransform: 'none', fontWeight: 700, px: 3, py: 1 }}
            >
              Clean Selected Duplicates
            </Button>
          )}
        </Box>
      </Box>

      {loading ? (
        <Box display="flex" justifyContent="center" p={6}><CircularProgress /></Box>
      ) : error ? (
        <Alert severity="error">{error}</Alert>
      ) : groups.length === 0 ? (
        <Card sx={{ borderRadius: 4, textAlign: 'center', p: 6 }}>
          <OkIcon color="success" sx={{ fontSize: 56, mb: 2 }} />
          <Typography variant="h6" sx={{ fontWeight: 700 }}>Zero Duplicates Found</Typography>
          <Typography variant="body2" color="text.secondary">Your library is completely clean, or the cache is empty. Try running a Manual Scan.</Typography>
        </Card>
      ) : (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          {groups.map(group => {
            const currentKeeper = keptPaths[group.key] || group.tracks[0].path;

            return (
              <Card key={group.key} sx={{ borderRadius: 4, background: cardBg, border: '1px solid', borderColor: 'divider', boxShadow: 'none' }}>
                <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
                  <Stack direction="row" justifyContent="space-between" alignItems="center" mb={2} flexWrap="wrap" gap={1.5}>
                    <Box>
                      <Typography variant="h6" sx={{ fontWeight: 800 }}>{group.title}</Typography>
                      <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>{group.artist}</Typography>
                    </Box>
                    <Button
                      size="small"
                      variant="outlined"
                      color="error"
                      onClick={() => handleResolveGroup(group)}
                      disabled={deleting}
                      startIcon={<DeleteIcon />}
                      sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 700 }}
                    >
                      Purge Duplicates
                    </Button>
                  </Stack>

                  {/* Desktop Table */}
                  <Box sx={{ display: { xs: 'none', md: 'block' } }}>
                    <TableContainer component={Paper} elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 3 }}>
                      <Table size="small">
                        <TableHead sx={{ bgcolor: 'action.hover' }}>
                          <TableRow>
                            <TableCell width={60} align="center">Keep</TableCell>
                            <TableCell>Action Status</TableCell>
                            <TableCell>Format & Quality</TableCell>
                            <TableCell>File Size</TableCell>
                            <TableCell>File Path</TableCell>
                          </TableRow>
                        </TableHead>
                        <TableBody>
                          {group.tracks.map((t, idx) => {
                            const isKeeper = t.path === currentKeeper;
                            const isRecommended = idx === 0;

                            return (
                              <TableRow
                                key={t.path}
                                hover
                                onClick={() => handleSelectKeeper(group.key, t.path)}
                                sx={{
                                  cursor: 'pointer',
                                  bgcolor: isKeeper ? 'action.selected' : 'inherit'
                                }}
                              >
                                <TableCell align="center">
                                  <Radio
                                    size="small"
                                    checked={isKeeper}
                                    onChange={() => handleSelectKeeper(group.key, t.path)}
                                  />
                                </TableCell>
                                <TableCell>
                                  {isKeeper ? (
                                    <Chip
                                      icon={<KeepIcon sx={{ fontSize: '14px !important' }} />}
                                      label={isRecommended ? "KEEP (Recommended)" : "KEEP (Selected)"}
                                      size="small"
                                      color="success"
                                      sx={{ fontWeight: 700, fontSize: '0.7rem' }}
                                    />
                                  ) : (
                                    <Chip
                                      label="WILL DELETE"
                                      size="small"
                                      color="error"
                                      variant="outlined"
                                      sx={{ fontWeight: 700, fontSize: '0.65rem' }}
                                    />
                                  )}
                                </TableCell>
                                <TableCell>
                                  <Box display="flex" gap={1} alignItems="center">
                                    <Chip label={t.format.toUpperCase()} size="small" variant="outlined" sx={{ fontWeight: 700 }} />
                                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{t.bitrate} kbps</Typography>
                                  </Box>
                                </TableCell>
                                <TableCell>{formatSize(t.size)}</TableCell>
                                <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.75rem', wordBreak: 'break-all' }}>{t.path}</TableCell>
                              </TableRow>
                            );
                          })}
                        </TableBody>
                      </Table>
                    </TableContainer>
                  </Box>

                  {/* Mobile Card List */}
                  <Box sx={{ display: { xs: 'block', md: 'none' } }}>
                    <List sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, p: 0 }}>
                      {group.tracks.map((t, idx) => {
                        const isKeeper = t.path === currentKeeper;
                        const isRecommended = idx === 0;

                        return (
                          <Paper
                            key={t.path}
                            elevation={0}
                            onClick={() => handleSelectKeeper(group.key, t.path)}
                            sx={{
                              p: 1.5,
                              cursor: 'pointer',
                              borderRadius: 3,
                              border: '2px solid',
                              borderColor: isKeeper ? 'success.main' : 'divider',
                              bgcolor: isKeeper
                                ? (theme => theme.palette.mode === 'dark' ? 'rgba(76, 175, 80, 0.12)' : 'rgba(76, 175, 80, 0.06)')
                                : (theme => theme.palette.mode === 'dark' ? '#222129' : '#fafafa')
                            }}
                          >
                            <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
                              <Stack direction="row" alignItems="center" spacing={1}>
                                <Radio
                                  size="small"
                                  checked={isKeeper}
                                  onChange={() => handleSelectKeeper(group.key, t.path)}
                                />
                                {isKeeper ? (
                                  <Chip
                                    label={isRecommended ? "KEEP (Recommended)" : "KEEP THIS FILE"}
                                    size="small"
                                    color="success"
                                    sx={{ fontWeight: 800, fontSize: '0.68rem' }}
                                  />
                                ) : (
                                  <Chip
                                    label="WILL BE DELETED"
                                    size="small"
                                    color="error"
                                    variant="outlined"
                                    sx={{ fontWeight: 800, fontSize: '0.65rem' }}
                                  />
                                )}
                              </Stack>
                              <Chip label={t.format.toUpperCase()} size="small" variant="outlined" sx={{ fontWeight: 700 }} />
                            </Stack>

                            <Typography variant="caption" color="text.secondary" display="block" mb={1}>
                              Bitrate: {t.bitrate} kbps • Size: {formatSize(t.size)}
                            </Typography>

                            <Typography
                              variant="caption"
                              sx={{
                                fontFamily: 'monospace',
                                wordBreak: 'break-all',
                                display: 'block',
                                bgcolor: (theme => theme.palette.mode === 'dark' ? 'rgba(0, 0, 0, 0.25)' : 'rgba(0, 0, 0, 0.04)'),
                                p: 1,
                                borderRadius: 1.5,
                                fontSize: '0.7rem'
                              }}
                            >
                              {t.path}
                            </Typography>
                          </Paper>
                        );
                      })}
                    </List>
                  </Box>
                </CardContent>
              </Card>
            );
          })}
        </Box>
      )}
    </Box>
  );
};

export default DuplicatesManager;
