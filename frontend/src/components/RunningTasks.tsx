import React, { useEffect, useState, useRef } from 'react';
import { 
  Box, Card, CardContent, Typography, Table, TableBody, TableCell, 
  TableContainer, TableRow, TableHead, Paper, IconButton, Chip, 
  CircularProgress, Alert, Tooltip, Button, Grid, Divider, useTheme, List
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
  Error as ErrorIcon
} from '@mui/icons-material';
import { apiService, ActiveTask, AlbumDownloadQueueItem } from '../api';

const RunningTasks: React.FC = () => {
  const theme = useTheme();
  const [tasks, setTasks] = useState<ActiveTask[]>([]);
  const [downloads, setDownloads] = useState<AlbumDownloadQueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const pollingRef = useRef<any>(null);

  const fetchData = async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      const [tasksData, downloadsData] = await Promise.all([
        apiService.getActiveTasks(),
        apiService.getAlbumDownloads()
      ]);
      setTasks(tasksData.tasks);
      setDownloads(downloadsData.downloads);
      setError(null);
    } catch (e) {
      setError("Failed to fetch running processes and downloads queue.");
    } finally {
      if (showLoading) setLoading(false);
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
    if (!window.confirm("Are you sure you want to stop this background task?")) return;
    setActionLoading(taskId);
    try {
      await apiService.stopActiveTask(taskId);
      await fetchData(false);
    } catch (e) {
      alert("Failed to request stop for task.");
    } finally {
      setActionLoading(null);
    }
  };

  const handleDeleteDownload = async (downloadId: number) => {
    if (!window.confirm("Are you sure you want to delete this album download from the queue? If it is currently running, it will be stopped.")) return;
    setActionLoading(`dl-${downloadId}`);
    try {
      await apiService.deleteAlbumDownload(downloadId);
      await fetchData(false);
    } catch (e) {
      alert("Failed to delete album download entry.");
    } finally {
      setActionLoading(null);
    }
  };

  const getTaskIcon = (type: string) => {
    switch (type) {
      case 'sync':
        return <SyncIcon color="primary" />;
      case 'album':
        return <AlbumIcon color="secondary" />;
      case 'track':
        return <TrackIcon color="action" />;
      default:
        return <PendingIcon />;
    }
  };

  const getStatusChip = (status: string) => {
    switch (status.toLowerCase()) {
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
        </Grid>
      )}
    </Box>
  );
};

export default RunningTasks;
