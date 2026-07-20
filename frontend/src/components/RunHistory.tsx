import React, { useEffect, useState } from 'react';
import { 
  Box, Card, CardContent, Typography, Table, TableBody, TableCell, 
  TableContainer, TableHead, TableRow, Paper, Collapse, IconButton,
  TablePagination, Chip, CircularProgress, Alert, Tooltip, useTheme, Button, Stack, List
} from '@mui/material';
import { 
  KeyboardArrowDown as ExpandIcon, 
  KeyboardArrowUp as CollapseIcon,
  CheckCircle as SuccessIcon,
  Error as ErrorIcon,
  Help as HelpIcon
} from '@mui/icons-material';
import { apiService, RunRecord, TrackRecord } from '../api';

const formatSize = (bytes: number | null) => {
  if (!bytes) return '-';
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(1)} MB`;
};

const getLyricsChip = (status: string | null) => {
  switch (status) {
    case 'synced':
      return <Chip label="LRC Synced" color="success" variant="outlined" size="small" sx={{ fontWeight: 600 }} />;
    case 'plain':
      return <Chip label="LRC Plain" color="primary" variant="outlined" size="small" sx={{ fontWeight: 600 }} />;
    case 'missing':
      return <Chip label="Missing" color="warning" variant="outlined" size="small" sx={{ fontWeight: 600 }} />;
    case 'none':
    default:
      return <Chip label="None" variant="outlined" size="small" />;
  }
};

const getTrackStatusChip = (status: string) => {
  switch (status) {
    case 'downloaded':
      return <Chip label="Downloaded" color="success" size="small" sx={{ fontWeight: 700 }} />;
    case 'skipped':
      return <Chip label="Skipped (Exists)" color="info" size="small" sx={{ fontWeight: 700 }} />;
    case 'failed':
      return <Chip label="Failed" color="error" size="small" sx={{ fontWeight: 700 }} />;
    case 'pending':
      return <Chip label="Pending" color="warning" size="small" sx={{ fontWeight: 700 }} />;
    default:
      return <Chip label={status} size="small" />;
  }
};

const Row: React.FC<{ run: RunRecord }> = ({ run }) => {
  const [open, setOpen] = useState(false);
  const [tracks, setTracks] = useState<TrackRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetched, setFetched] = useState(false);

  const fetchTracks = async () => {
    if (fetched || loading) return;
    setLoading(true);
    try {
      const data = await apiService.getTracks(run.id);
      setTracks(data.tracks);
      setFetched(true);
      setError(null);
    } catch (e) {
      setError("Failed to fetch tracks for this run.");
    } finally {
      setLoading(false);
    }
  };

  const handleToggle = () => {
    setOpen(!open);
    if (!open) {
      fetchTracks();
    }
  };



  return (
    <>
      <TableRow sx={{ '& > *': { borderBottom: 'unset' } }}>
        <TableCell>
          <IconButton aria-label="expand row" size="small" onClick={handleToggle}>
            {open ? <CollapseIcon /> : <ExpandIcon />}
          </IconButton>
        </TableCell>
        <TableCell component="th" scope="row" sx={{ fontWeight: 600 }}>
          #{run.id}
        </TableCell>
        <TableCell>{new Date(run.timestamp).toLocaleString()}</TableCell>
        <TableCell>
          <Chip 
            label={run.status.toUpperCase()} 
            color={run.status === 'completed' ? 'success' : run.status === 'failed' ? 'error' : 'warning'} 
            size="small"
            sx={{ fontWeight: 700 }}
          />
        </TableCell>
        <TableCell align="center">{run.tracks_found}</TableCell>
        <TableCell align="center" sx={{ color: 'success.main', fontWeight: 600 }}>{run.tracks_downloaded}</TableCell>
        <TableCell align="center" sx={{ color: 'info.main', fontWeight: 600 }}>{run.tracks_skipped}</TableCell>
        <TableCell align="center" sx={{ color: 'error.main', fontWeight: 600 }}>{run.tracks_failed}</TableCell>
      </TableRow>
      <TableRow>
        <TableCell style={{ paddingBottom: 0, paddingTop: 0 }} colSpan={8}>
          <Collapse in={open} timeout="auto" unmountOnExit>
            <Box sx={{ margin: 2, padding: 2, borderRadius: 2, bgcolor: 'background.default' }}>
              <Typography variant="h6" gutterBottom component="div" sx={{ fontWeight: 700 }}>
                Track Synchronization Details
              </Typography>
              
              {loading && (
                <Box display="flex" justifyContent="center" p={2}>
                  <CircularProgress size={30} />
                </Box>
              )}
              
              {error && <Alert severity="error">{error}</Alert>}
              
              {run.error_message && (
                <Alert severity="error" sx={{ mb: 2, borderRadius: 2 }}>
                  <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Global Run Error:</Typography>
                  {run.error_message}
                </Alert>
              )}

              {!loading && !error && tracks.length === 0 && (
                <Typography variant="body2" color="text.secondary">
                  No tracks were processed in this run.
                </Typography>
              )}

              {!loading && !error && tracks.length > 0 && (
                <TableContainer component={Paper} sx={{ boxShadow: 'none', border: '1px solid', borderColor: 'divider', borderRadius: 2, overflowX: 'auto' }}>
                  <Table size="small" aria-label="tracks" sx={{ minWidth: 480 }}>
                    <TableHead>
                      <TableRow>
                        <TableCell sx={{ fontWeight: 700 }}>Artist</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>Title</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>Status</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>Bitrate</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>File Size</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>Lyrics</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>Failure Details</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {tracks.map((track) => (
                        <TableRow key={track.id}>
                          <TableCell sx={{ fontWeight: 500 }}>{track.artist}</TableCell>
                          <TableCell>{track.title}</TableCell>
                          <TableCell>{getTrackStatusChip(track.status)}</TableCell>
                          <TableCell>{track.bitrate ? `${track.bitrate} kbps` : '-'}</TableCell>
                          <TableCell>{formatSize(track.size)}</TableCell>
                          <TableCell>{getLyricsChip(track.lyrics_status)}</TableCell>
                          <TableCell sx={{ color: 'error.main' }}>
                            {track.error_reason || '-'}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              )}
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
};

const MobileRunRow: React.FC<{ run: RunRecord }> = ({ run }) => {
  const [open, setOpen] = useState(false);
  const [tracks, setTracks] = useState<TrackRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetched, setFetched] = useState(false);

  const fetchTracks = async () => {
    if (fetched || loading) return;
    setLoading(true);
    try {
      const data = await apiService.getTracks(run.id);
      setTracks(data.tracks);
      setFetched(true);
      setError(null);
    } catch (e) {
      setError("Failed to fetch tracks for this run.");
    } finally {
      setLoading(false);
    }
  };

  const handleToggle = () => {
    setOpen(!open);
    if (!open) {
      fetchTracks();
    }
  };

  return (
    <Card sx={{ borderRadius: 3, border: '1px solid', borderColor: 'divider', mb: 2 }}>
      <CardContent sx={{ p: 2 }}>
        <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
          <Typography variant="subtitle1" fontWeight={700}>Run #{run.id}</Typography>
          <Chip 
            label={run.status.toUpperCase()} 
            color={run.status === 'completed' ? 'success' : run.status === 'failed' ? 'error' : 'warning'} 
            size="small"
            sx={{ fontWeight: 700 }}
          />
        </Box>
        <Typography variant="caption" color="text.secondary" display="block" mb={2}>
          {new Date(run.timestamp).toLocaleString()}
        </Typography>
        
        <Box display="flex" flexWrap="wrap" gap={1} mb={2}>
          <Chip label={`Found: ${run.tracks_found}`} size="small" variant="outlined" />
          <Chip label={`DL'd: ${run.tracks_downloaded}`} size="small" color="success" variant="outlined" sx={{ fontWeight: 600 }} />
          <Chip label={`Skip: ${run.tracks_skipped}`} size="small" color="info" variant="outlined" sx={{ fontWeight: 600 }} />
          <Chip label={`Fail: ${run.tracks_failed}`} size="small" color="error" variant="outlined" sx={{ fontWeight: 600 }} />
        </Box>

        <Button 
          fullWidth 
          variant="outlined" 
          size="small" 
          onClick={handleToggle}
          sx={{ textTransform: 'none', fontWeight: 600 }}
        >
          {open ? 'Hide details' : 'Show details'}
        </Button>

        <Collapse in={open} timeout="auto" unmountOnExit sx={{ mt: 2 }}>
          {loading && (
            <Box display="flex" justifyContent="center" py={2}><CircularProgress size={24} /></Box>
          )}
          {error && <Alert severity="error">{error}</Alert>}
          {run.error_message && (
            <Alert severity="error" sx={{ mb: 1.5, borderRadius: 2 }}>{run.error_message}</Alert>
          )}
          {!loading && !error && tracks.length === 0 && (
            <Typography variant="caption" color="text.secondary">No tracks were processed.</Typography>
          )}
          {!loading && !error && tracks.length > 0 && (
            <Stack spacing={1.5}>
              {tracks.map((track) => (
                <Box key={track.id} sx={{ p: 1.5, borderRadius: 2, bgcolor: 'action.hover', border: '1px solid', borderColor: 'divider' }}>
                  <Box display="flex" justifyContent="space-between" alignItems="flex-start" mb={0.5}>
                    <Typography variant="body2" fontWeight={700}>{track.title}</Typography>
                    {getTrackStatusChip(track.status)}
                  </Box>
                  <Typography variant="caption" color="text.secondary" display="block">{track.artist}</Typography>
                  <Box display="flex" gap={1} mt={1} flexWrap="wrap">
                    {track.bitrate && <Chip label={`${track.bitrate} kbps`} size="small" variant="outlined" sx={{ fontSize: '0.65rem', height: 18 }} />}
                    {track.lyrics_status && getLyricsChip(track.lyrics_status)}
                  </Box>
                  {track.error_reason && (
                    <Typography variant="caption" color="error.main" display="block" mt={1}>
                      Error: {track.error_reason}
                    </Typography>
                  )}
                </Box>
              ))}
            </Stack>
          )}
        </Collapse>
      </CardContent>
    </Card>
  );
};

export const RunHistory: React.FC = () => {
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(10);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchRuns = async () => {
    try {
      setLoading(true);
      const limit = rowsPerPage;
      const offset = page * rowsPerPage;
      const data = await apiService.getRuns(limit, offset);
      setRuns(data.runs);
      setTotal(data.total);
      setError(null);
    } catch (e) {
      setError("Failed to retrieve sync history runs from SQLite database.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRuns();
  }, [page, rowsPerPage]);

  const handleChangePage = (_event: unknown, newPage: number) => {
    setPage(newPage);
  };

  const handleChangeRowsPerPage = (event: React.ChangeEvent<HTMLInputElement>) => {
    setRowsPerPage(parseInt(event.target.value, 10));
    setPage(0);
  };

  if (loading && runs.length === 0) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="50vh">
        <CircularProgress size={50} />
      </Box>
    );
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <Box>
        <Typography variant="h5" sx={{ fontWeight: 800 }}>Sync History</Typography>
        <Typography variant="body2" color="text.secondary">
          Track download audit history logs fetched from SQLite.
        </Typography>
      </Box>

      {error && <Alert severity="error">{error}</Alert>}

      <Card>
        <CardContent sx={{ p: 0, '&:last-child': { pb: 0 } }}>
          {/* Desktop View Table */}
          <Box sx={{ display: { xs: 'none', md: 'block' } }}>
            <TableContainer sx={{ overflowX: 'auto' }}>
              <Table aria-label="collapsible table" sx={{ minWidth: 580 }}>
                <TableHead>
                  <TableRow>
                    <TableCell style={{ width: 50 }} />
                    <TableCell sx={{ fontWeight: 700 }}>Run #</TableCell>
                    <TableCell sx={{ fontWeight: 700 }}>Timestamp</TableCell>
                    <TableCell sx={{ fontWeight: 700 }}>Status</TableCell>
                    <TableCell align="center" sx={{ fontWeight: 700 }}>Found</TableCell>
                    <TableCell align="center" sx={{ fontWeight: 700 }}>DL'd</TableCell>
                    <TableCell align="center" sx={{ fontWeight: 700 }}>Skip</TableCell>
                    <TableCell align="center" sx={{ fontWeight: 700 }}>Fail</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {runs.map((run) => (
                    <Row key={run.id} run={run} />
                  ))}
                  {runs.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={8} align="center" style={{ padding: '40px 0' }}>
                        <Typography color="text.secondary">
                          No synchronization history records found in SQLite.
                        </Typography>
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          </Box>

          {/* Mobile View Card List */}
          <Box sx={{ display: { xs: 'block', md: 'none' }, p: 1.5 }}>
            {runs.map((run) => (
              <MobileRunRow key={run.id} run={run} />
            ))}
            {runs.length === 0 && (
              <Typography color="text.secondary" align="center" sx={{ py: 4 }}>
                No synchronization history records found.
              </Typography>
            )}
          </Box>

          <TablePagination
            rowsPerPageOptions={[10, 25, 50]}
            component="div"
            count={total}
            rowsPerPage={rowsPerPage}
            page={page}
            onPageChange={handleChangePage}
            onRowsPerPageChange={handleChangeRowsPerPage}
            labelRowsPerPage="Rows:"
          />
        </CardContent>
      </Card>
    </Box>
  );
};
export default RunHistory;
