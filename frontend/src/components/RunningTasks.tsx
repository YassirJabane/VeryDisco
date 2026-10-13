import React, { useEffect, useState, useRef } from 'react';
import { 
  Box, Card, CardContent, Typography, Table, TableBody, TableCell, 
  TableContainer, TableRow, TableHead, Paper, IconButton, Chip, 
  CircularProgress, Alert, Tooltip, Button, Grid, Divider, useTheme, List,
  Dialog, DialogTitle, DialogContent, DialogActions
} from '@mui/material';
import { 
  StopCircle as StopIcon,
  Delete as DeleteIcon,
  Refresh as RefreshIcon,
  Sync as SyncIcon,
  Album as AlbumIcon,
  MusicNote as TrackIcon,
  HourglassEmpty as PendingIcon,
  CheckCircle as SuccessIcon,
  Error as ErrorIcon,
  Replay as RetryIcon,
  InfoOutlined as DetailsIcon
} from '@mui/icons-material';
import { apiService, ActiveTask, AlbumDownloadQueueItem, TrackDownloadAttempt, TrackDownloadJobItem } from '../api';
import { useNotification } from '../context/NotificationContext';

const RunningTasks: React.FC = () => {
  const { notify, confirm } = useNotification();
  const theme = useTheme();
  const [tasks, setTasks] = useState<ActiveTask[]>([]);
  const [downloads, setDownloads] = useState<AlbumDownloadQueueItem[]>([]);
  const [trackDownloads, setTrackDownloads] = useState<TrackDownloadJobItem[]>([]);
  const [selectedTrackJob, setSelectedTrackJob] = useState<TrackDownloadJobItem | null>(null);
  const [trackAttempts, setTrackAttempts] = useState<TrackDownloadAttempt[]>([]);
  const [attemptsLoading, setAttemptsLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const pollingRef = useRef<any>(null);

  const fetchData = async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      const [tasksData, downloadsData, trackDownloadsData] = await Promise.all([
        apiService.getActiveTasks(),
        apiService.getAlbumDownloads(),
        apiService.getTrackDownloads(),
      ]);
      setTasks(tasksData.tasks);
      setDownloads(downloadsData.downloads);
      setTrackDownloads(trackDownloadsData.downloads);
      setError(null);
    } catch (e) {
      setError("Failed to fetch running processes and downloads queue.");
    } finally {
      if (showLoading) setLoading(false);
    }
  };

  const handleRetryTrackDownload = async (jobId: number) => {
    setActionLoading(`track-${jobId}`);
    try {
      await apiService.retryTrackDownload(jobId);
      notify('Track download queued for a clean retry.', 'info');
      await fetchData(false);
    } catch (e: any) {
      notify(e.response?.data?.detail || 'Failed to retry track download.', 'error');
    } finally {
      setActionLoading(null);
    }
  };

  const showTrackAttempts = async (job: TrackDownloadJobItem) => {
    setSelectedTrackJob(job);
    setAttemptsLoading(true);
    try {
      const response = await apiService.getTrackDownloadAttempts(job.id);
      setTrackAttempts(response.attempts);
    } catch (e) {
      setTrackAttempts([]);
      notify('Failed to load candidate history.', 'error');
    } finally {
      setAttemptsLoading(false);
    }
  };

  useEffect(() => {
    fetchData(true);

    // Setup polling every 3 seconds to update real-time progress
    pollingRef.current = setInterval(() => {
      fetchData(false);
    }, 3000);

    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  const handleStopTask = async (taskId: string) => {
    confirm({
      title: 'Stop Task',
      message: 'Are you sure you want to stop this background task?',
      confirmText: 'Stop Task',
      onConfirm: async () => {
        setActionLoading(taskId);
        try {
          await apiService.stopActiveTask(taskId);
          notify('Task stop requested.', 'info');
          await fetchData(false);
        } catch (e) {
          notify('Failed to request stop for task.', 'error');
        } finally {
          setActionLoading(null);
        }
      }
    });
  };

  const handleDeleteDownload = async (downloadId: number) => {
    confirm({
      title: 'Delete Download Entry',
      message: 'Are you sure you want to delete this album download from the queue? If it is currently running, it will be stopped.',
      confirmText: 'Delete Entry',
      onConfirm: async () => {
        setActionLoading(`dl-${downloadId}`);
        try {
          await apiService.deleteAlbumDownload(downloadId);
          notify('Album download entry deleted.', 'info');
          await fetchData(false);
        } catch (e) {
          notify('Failed to delete album download entry.', 'error');
        } finally {
          setActionLoading(null);
        }
      }
    });
  };

  const getTaskIcon = (type: string) => {
    switch (type) {
      case 'sync':
        return <SyncIcon color="primary" />;
      case 'album':
        return <AlbumIcon color="secondary" />;
      case 'track':
        return <TrackIcon color="action" />;
      case 'request':
        return <PendingIcon color="primary" />;
      default:
        return <PendingIcon />;
    }
  };

  const getStatusChip = (status: string) => {
    switch ((status || '').toLowerCase()) {
      case 'completed':
        return (
          <Chip 
            icon={<SuccessIcon style={{ color: theme.palette.success.main }} />}
            label="Completed" 
            variant="outlined" 
            color="success" 
            size="small" 
            sx={{ fontWeight: 600 }} 
          />
        );
      case 'failed':
        return (
          <Chip 
            icon={<ErrorIcon style={{ color: theme.palette.error.main }} />}
            label="Failed" 
            variant="outlined" 
            color="error" 
            size="small" 
            sx={{ fontWeight: 600 }} 
          />
        );
      case 'partial':
        return <Chip icon={<ErrorIcon />} label="Partial — tracks missing" variant="outlined" color="warning" size="small" />;
      case 'pending':
        return (
          <Chip 
            icon={<PendingIcon style={{ color: theme.palette.warning.main }} />}
            label="Pending" 
            variant="outlined" 
            color="warning" 
            size="small" 
            sx={{ fontWeight: 600 }} 
          />
        );
      case 'running':
      case 'downloading':
        return (
          <Chip 
            icon={<CircularProgress size={14} />}
            label="Downloading" 
            variant="outlined" 
            color="primary" 
            size="small" 
            sx={{ fontWeight: 600 }} 
          />
        );
      default:
        return <Chip label={status} size="small" />;
    }
  };

  const getTaskName = (task: ActiveTask) => {
    if (task.type === 'sync') {
      return `Sync Playlist: ${task.metadata?.source || 'Unknown source'}`;
    }
    if (task.type === 'album') {
      return `Album Download: ${task.metadata?.artist || 'Unknown Artist'} - ${task.metadata?.album || 'Unknown Album'}`;
    }
    if (task.type === 'track') {
      return `Track Download: ${task.metadata?.artist || 'Unknown Artist'} - ${task.metadata?.title || 'Unknown Title'}`;
    }
    if (task.type === 'request') {
      return `Music Request: ${task.metadata?.artist || 'Unknown Artist'} - ${task.metadata?.title || task.metadata?.album || 'Unknown release'}`;
    }
    return `Task: ${task.id}`;
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {/* Header section */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 2 }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 800 }}>
            Running Processes & Downloads
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Monitor and cancel active synchronizations, album downloads, and queue status.
          </Typography>
        </Box>
        <Button 
          variant="outlined" 
          startIcon={<RefreshIcon />} 
          onClick={() => fetchData(true)}
          disabled={loading}
        >
          Refresh
        </Button>
      </Box>

      {error && <Alert severity="error">{error}</Alert>}

      {loading ? (
        <Box display="flex" justifyContent="center" py={8}>
          <CircularProgress />
        </Box>
      ) : (
        <Grid container spacing={{ xs: 2, sm: 4 }}>
          {/* Active Tasks Panel */}
          <Grid item xs={12}>
            <Card sx={{ borderRadius: 4, boxShadow: theme.shadows[2] }}>
              <CardContent sx={{ p: 3 }}>
                <Typography variant="h6" sx={{ fontWeight: 700, mb: 2, display: 'flex', alignItems: 'center', gap: 1 }}>
                  <CircularProgress size={18} thickness={5} sx={{ display: tasks.length > 0 ? 'inline-block' : 'none' }} />
                  Active Background Tasks ({tasks.length})
                </Typography>
                <Divider sx={{ mb: 2 }} />

                {tasks.length === 0 ? (
                  <Typography variant="body2" color="text.secondary" align="center" sx={{ py: 4 }}>
                    No background tasks are currently running.
                  </Typography>
                ) : (
                  <>
                    {/* Desktop Tasks Table */}
                    <Box sx={{ display: { xs: 'none', md: 'block' } }}>
                      <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 2 }}>
                        <Table size="medium">
                          <TableHead sx={{ bgcolor: theme.palette.action.hover }}>
                            <TableRow>
                              <TableCell style={{ width: 60 }}></TableCell>
                              <TableCell sx={{ fontWeight: 600 }}>Task Name</TableCell>
                              <TableCell sx={{ fontWeight: 600 }}>Type</TableCell>
                              <TableCell sx={{ fontWeight: 600 }}>Started At</TableCell>
                              <TableCell align="right" sx={{ fontWeight: 600 }}>Actions</TableCell>
                            </TableRow>
                          </TableHead>
                          <TableBody>
                            {tasks.map((task) => (
                              <TableRow key={task.id} hover>
                                <TableCell align="center">{getTaskIcon(task.type)}</TableCell>
                                <TableCell sx={{ fontWeight: 600 }}>
                                  {getTaskName(task)}
                                </TableCell>
                                <TableCell>
                                  <Chip label={task.type.toUpperCase()} size="small" variant="outlined" sx={{ fontWeight: 700 }} />
                                </TableCell>
                                <TableCell>{new Date(task.started_at).toLocaleTimeString()}</TableCell>
                                <TableCell align="right">
                                  <Tooltip title="Stop task immediately">
                                    <IconButton 
                                      color="error" 
                                      onClick={() => handleStopTask(task.id)}
                                      disabled={actionLoading === task.id}
                                    >
                                      {actionLoading === task.id ? (
                                        <CircularProgress size={20} color="error" />
                                      ) : (
                                        <StopIcon />
                                      )}
                                    </IconButton>
                                  </Tooltip>
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </TableContainer>
                    </Box>

                    {/* Mobile Tasks List */}
                    <Box sx={{ display: { xs: 'block', md: 'none' } }}>
                      <List sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
                        {tasks.map((task) => (
                          <Card key={task.id} variant="outlined" sx={{ borderRadius: 3, border: '1px solid', borderColor: 'divider' }}>
                            <CardContent sx={{ p: 2, display: 'flex', flexDirection: 'column', gap: 1.5 }}>
                              <Box display="flex" justifyContent="space-between" alignItems="center">
                                <Box display="flex" alignItems="center" gap={1}>
                                  {getTaskIcon(task.type)}
                                  <Typography variant="subtitle2" fontWeight={700}>{getTaskName(task)}</Typography>
                                </Box>
                                <IconButton 
                                  color="error" 
                                  size="small"
                                  onClick={() => handleStopTask(task.id)}
                                  disabled={actionLoading === task.id}
                                >
                                  {actionLoading === task.id ? (
                                    <CircularProgress size={16} color="error" />
                                  ) : (
                                    <StopIcon />
                                  )}
                                </IconButton>
                              </Box>
                              <Divider />
                              <Box display="flex" justifyContent="space-between" alignItems="center">
                                <Chip label={task.type.toUpperCase()} size="small" variant="outlined" sx={{ fontWeight: 700 }} />
                                <Typography variant="caption" color="text.secondary">
                                  Started: {new Date(task.started_at).toLocaleTimeString()}
                                </Typography>
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
          </Grid>

          {/* Album Downloads Queue Panel */}
          <Grid item xs={12}>
            <Card sx={{ borderRadius: 4, boxShadow: theme.shadows[2] }}>
              <CardContent sx={{ p: 3 }}>
                <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>
                  Album Downloads Queue & History ({downloads.length})
                </Typography>
                <Divider sx={{ mb: 2 }} />

                {downloads.length === 0 ? (
                  <Typography variant="body2" color="text.secondary" align="center" sx={{ py: 4 }}>
                    The album download queue is empty.
                  </Typography>
                ) : (
                  <>
                    {/* Desktop Queue Table */}
                    <Box sx={{ display: { xs: 'none', md: 'block' } }}>
                      <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 2 }}>
                        <Table size="medium">
                          <TableHead sx={{ bgcolor: theme.palette.action.hover }}>
                            <TableRow>
                              <TableCell sx={{ fontWeight: 600 }}>Artist</TableCell>
                              <TableCell sx={{ fontWeight: 600 }}>Album</TableCell>
                              <TableCell sx={{ fontWeight: 600 }}>Status</TableCell>
                              <TableCell sx={{ fontWeight: 600 }}>Queued Time</TableCell>
                              <TableCell align="right" sx={{ fontWeight: 600 }}>Actions</TableCell>
                            </TableRow>
                          </TableHead>
                          <TableBody>
                            {downloads.map((dl) => {
                              const isRunning = tasks.some(t => t.id === `album:${dl.id}`);
                              return (
                                <TableRow key={dl.id} hover>
                                  <TableCell sx={{ fontWeight: 600 }}>{dl.artist}</TableCell>
                                  <TableCell>{dl.album}</TableCell>
                                  <TableCell>
                                    {getStatusChip(isRunning ? 'running' : dl.status)}
                                  </TableCell>
                                  <TableCell>{new Date(dl.added_at).toLocaleString()}</TableCell>
                                  <TableCell align="right">
                                    <Tooltip title="Delete from queue and cancel if running">
                                      <IconButton 
                                        color="error" 
                                        onClick={() => handleDeleteDownload(dl.id)}
                                        disabled={actionLoading === `dl-${dl.id}`}
                                      >
                                        {actionLoading === `dl-${dl.id}` ? (
                                          <CircularProgress size={20} color="error" />
                                        ) : (
                                          <DeleteIcon />
                                        )}
                                      </IconButton>
                                    </Tooltip>
                                  </TableCell>
                                </TableRow>
                              );
                            })}
                          </TableBody>
                        </Table>
                      </TableContainer>
                    </Box>

                    {/* Mobile Queue List */}
                    <Box sx={{ display: { xs: 'block', md: 'none' } }}>
                      <List sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
                        {downloads.map((dl) => {
                          const isRunning = tasks.some(t => t.id === `album:${dl.id}`);
                          return (
                            <Card key={dl.id} variant="outlined" sx={{ borderRadius: 3, border: '1px solid', borderColor: 'divider' }}>
                              <CardContent sx={{ p: 2, display: 'flex', flexDirection: 'column', gap: 1.5 }}>
                                <Box display="flex" justifyContent="space-between" alignItems="center">
                                  <Box>
                                    <Typography variant="subtitle2" fontWeight={700}>{dl.album}</Typography>
                                    <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>{dl.artist}</Typography>
                                  </Box>
                                  <IconButton 
                                    color="error" 
                                    size="small"
                                    onClick={() => handleDeleteDownload(dl.id)}
                                    disabled={actionLoading === `dl-${dl.id}`}
                                  >
                                    {actionLoading === `dl-${dl.id}` ? (
                                      <CircularProgress size={16} color="error" />
                                    ) : (
                                      <DeleteIcon />
                                    )}
                                  </IconButton>
                                </Box>
                                <Divider />
                                <Box display="flex" justifyContent="space-between" alignItems="center">
                                  {getStatusChip(isRunning ? 'running' : dl.status)}
                                  <Typography variant="caption" color="text.secondary">
                                    Queued: {new Date(dl.added_at).toLocaleString()}
                                  </Typography>
                                </Box>
                              </CardContent>
                            </Card>
                          );
                        })}
                      </List>
                    </Box>
                  </>
                )}
              </CardContent>
            </Card>
          </Grid>

          <Grid item xs={12}>
            <Card sx={{ borderRadius: 4, boxShadow: theme.shadows[2] }}>
              <CardContent sx={{ p: 3 }}>
                <Typography variant="h6" sx={{ fontWeight: 700, mb: 0.5 }}>
                  Track Download Decisions ({trackDownloads.length})
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                  Persistent search history: selected query, final outcome, and a safe retry for failures.
                </Typography>
                <Divider sx={{ mb: 2 }} />
                {trackDownloads.length === 0 ? (
                  <Typography variant="body2" color="text.secondary" align="center" sx={{ py: 3 }}>
                    No individual track downloads have been recorded yet.
                  </Typography>
                ) : (
                  <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 2 }}>
                    <Table size="small">
                      <TableHead sx={{ bgcolor: theme.palette.action.hover }}>
                        <TableRow>
                          <TableCell sx={{ fontWeight: 600 }}>Track</TableCell>
                          <TableCell sx={{ fontWeight: 600 }}>Search</TableCell>
                          <TableCell sx={{ fontWeight: 600 }}>Outcome</TableCell>
                          <TableCell align="right" sx={{ fontWeight: 600 }}>Action</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {trackDownloads.map((job) => {
                          const canRetry = ['failed', 'interrupted'].includes(job.status.toLowerCase());
                          return (
                            <TableRow key={job.id} hover>
                              <TableCell>
                                <Typography variant="body2" fontWeight={700}>{job.artist} — {job.title}</Typography>
                                {job.album && <Typography variant="caption" color="text.secondary">{job.album}</Typography>}
                              </TableCell>
                              <TableCell>
                                <Typography variant="body2">{job.query_text || 'Preparing search…'}</Typography>
                                {job.query_strategy > 0 && <Typography variant="caption" color="text.secondary">Strategy {job.query_strategy}</Typography>}
                              </TableCell>
                              <TableCell>
                                {getStatusChip(job.status)}
                                {job.error_reason && <Typography variant="caption" display="block" color="error.main" sx={{ mt: 0.5 }}>{job.error_reason}</Typography>}
                              </TableCell>
                              <TableCell align="right">
                                <Tooltip title="View candidate decisions">
                                  <IconButton color="default" onClick={() => showTrackAttempts(job)}>
                                    <DetailsIcon />
                                  </IconButton>
                                </Tooltip>
                                {canRetry && <Tooltip title="Start a clean retry; this history is kept">
                                  <IconButton color="primary" onClick={() => handleRetryTrackDownload(job.id)} disabled={actionLoading === `track-${job.id}`}>
                                    {actionLoading === `track-${job.id}` ? <CircularProgress size={20} /> : <RetryIcon />}
                                  </IconButton>
                                </Tooltip>}
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </TableContainer>
                )}
              </CardContent>
            </Card>
          </Grid>
        </Grid>
      )}
      <Dialog open={Boolean(selectedTrackJob)} onClose={() => setSelectedTrackJob(null)} fullWidth maxWidth="md">
        <DialogTitle>Candidate decisions — {selectedTrackJob?.artist} — {selectedTrackJob?.title}</DialogTitle>
        <DialogContent dividers>
          {attemptsLoading ? <Box display="flex" justifyContent="center" py={3}><CircularProgress /></Box> : trackAttempts.length === 0 ? (
            <Typography color="text.secondary">No peer was selected. The job may have stopped before candidates were returned.</Typography>
          ) : (
            <Table size="small">
              <TableHead><TableRow>
                <TableCell>#</TableCell><TableCell>Peer / file</TableCell><TableCell>Decision</TableCell><TableCell>Reason</TableCell>
              </TableRow></TableHead>
              <TableBody>{trackAttempts.map((attempt) => <TableRow key={attempt.id}>
                <TableCell>{attempt.attempt_number}</TableCell>
                <TableCell><Typography variant="body2">{attempt.username}</Typography><Typography variant="caption" color="text.secondary">{attempt.remote_filename}</Typography></TableCell>
                <TableCell>{getStatusChip(attempt.status)}</TableCell>
                <TableCell><Typography variant="body2">{attempt.reason || 'No additional reason recorded.'}</Typography></TableCell>
              </TableRow>)}</TableBody>
            </Table>
          )}
        </DialogContent>
        <DialogActions><Button onClick={() => setSelectedTrackJob(null)}>Close</Button></DialogActions>
      </Dialog>
    </Box>
  );
};

export default RunningTasks;
