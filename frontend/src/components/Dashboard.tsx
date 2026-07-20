import React, { useEffect, useState } from 'react';
import { 
  Box, Card, CardContent, Typography, Grid, 
  CircularProgress, Alert, AlertTitle, Chip,
  useTheme, Avatar, Tabs, Tab, Button, Divider, Stack
} from '@mui/material';
import { 
  Sync as SyncIcon, 
  CheckCircle as SuccessIcon, 
  Error as ErrorIcon,
  LibraryMusic as MusicIcon,
  CloudDownload as DownloadIcon,
  SkipNext as SkipIcon,
  Storage as StorageIcon,
  Album as AlbumIcon,
  People as ArtistIcon,
  Info as InfoIcon
} from '@mui/icons-material';
import { apiService, GetStatusResponse, RunRecord } from '../api';

interface DashboardProps {
  onNavigateToConfig: () => void;
}

export const Dashboard: React.FC<DashboardProps> = ({ onNavigateToConfig }) => {
  const theme = useTheme();
  const [status, setStatus] = useState<GetStatusResponse | null>(null);
  const [navidromeStats, setNavidromeStats] = useState<{ songs: number; albums: number; artists: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [statsLoading, setStatsLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [activeTab, setActiveTab] = useState<number>(0);
  const [activePlaylists, setActivePlaylists] = useState<string[]>([]);

  const showToast = (msg: string, type: 'success' | 'error' = 'success') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  };

  const fetchStatus = async () => {
    try {
      const data = await apiService.getStatus();
      setStatus(data);
      if (data.latest_runs) {
        setActivePlaylists(Object.keys(data.latest_runs));
      }
      setErrorMessage(null);
    } catch (e: any) {
      console.error(e);
      setErrorMessage("Could not fetch status from backend server.");
    } finally {
      setLoading(false);
    }
  };

  const fetchNavidromeStats = async () => {
    try {
      const stats = await apiService.getNavidromeStats();
      setNavidromeStats(stats);
    } catch (e) {
      console.error("Failed to fetch Navidrome stats", e);
    } finally {
      setStatsLoading(false);
    }
  };

  useEffect(() => {
    fetchStatus();
    fetchNavidromeStats();
    const statusInterval = setInterval(() => fetchStatus(), 5000);
    const statsInterval = setInterval(() => fetchNavidromeStats(), 60000);
    return () => {
      clearInterval(statusInterval);
      clearInterval(statsInterval);
    };
  }, []);

  if (loading && !status) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="50vh">
        <CircularProgress size={50} />
      </Box>
    );
  }

  const isConfigured = status?.is_configured ?? false;
  const isSyncing = status?.is_syncing ?? false;
  
  // Last runs
  const currentTabPlaylist = activePlaylists[activeTab] || '';
  
  const currentTabRun: RunRecord | null | undefined = status?.latest_runs?.[currentTabPlaylist];

  const currentTabName = currentTabPlaylist.replace('-', ' ').toUpperCase() || 'PLAYLIST';

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {errorMessage && (
        <Alert severity="error" onClose={() => setErrorMessage(null)} sx={{ borderRadius: 3 }}>
          {errorMessage}
        </Alert>
      )}

      {!isConfigured && (
        <Alert 
          severity="warning" 
          action={
            <Button color="inherit" size="small" onClick={onNavigateToConfig}>
              Configure Now
            </Button>
          }
          sx={{ borderRadius: 3 }}
        >
          <AlertTitle sx={{ fontWeight: 700 }}>Configuration Incomplete</AlertTitle>
          {status?.validation_errors || "Please complete the setup to start syncing music."}
        </Alert>
      )}

      {/* Welcome Banner */}
      {toast && (
        <Alert severity={toast.type} onClose={() => setToast(null)} sx={{ borderRadius: 2, mb: 2 }}>
          {toast.msg}
        </Alert>
      )}

      {/* Header */}
      <Box display="flex" justifyContent="space-between" alignItems="flex-start" flexWrap="wrap" gap={2}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 800, background: 'linear-gradient(90deg, #9c27b0, #673ab7)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
            Dashboard
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Syncing ListenBrainz music discovery to your local Navidrome library
          </Typography>
          {status?.next_run && (
            <Typography variant="caption" sx={{ display: 'block', mt: 0.5, fontWeight: 700, color: 'text.secondary' }}>
              Next Scheduled Sync: {new Date(status.next_run).toLocaleString()}
            </Typography>
          )}
        </Box>
        {isSyncing && (
          <Chip
            icon={<SyncIcon className="spin-icon" sx={{ animation: 'spin 2s linear infinite' }} />}
            label="SYNC ACTIVE"
            color="primary"
            sx={{ fontWeight: 700, px: 1, borderRadius: 2 }}
          />
        )}
      </Box>

      {/* Side-by-side playlist status cards */}
      <Grid container spacing={{ xs: 2, sm: 3 }}>
        {/* Dynamic Panels based on activePlaylists (up to 2 for grid layout, or all?) */}
        {/* The user originally had two grid items. Let's map through all active playlists. */}
        {activePlaylists.map((playlistKey) => {
          const runInfo = status?.latest_runs?.[playlistKey];
          return (
            <Grid item xs={12} md={activePlaylists.length === 1 ? 12 : 6} key={playlistKey}>
              <Card sx={{ 
                height: '100%',
                background: theme.palette.mode === 'dark' 
                  ? 'linear-gradient(135deg, #1e1b26 0%, #110f17 100%)'
                  : 'linear-gradient(135deg, #ffffff 0%, #f7f6fa 100%)',
                border: `1px solid ${theme.palette.divider}`,
                borderRadius: 4,
                transition: 'transform 0.2s, box-shadow 0.2s',
                '&:hover': {
                  transform: 'translateY(-2px)',
                  boxShadow: '0 8px 30px rgba(0,0,0,0.12)'
                }
              }}>
                <CardContent sx={{ p: 3 }}>
                  <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
                    <Typography variant="h6" sx={{ fontWeight: 700 }}>{playlistKey.replace('-', ' ').toUpperCase()}</Typography>
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Button
                        variant="outlined"
                        size="small"
                        startIcon={actionLoading === playlistKey ? <CircularProgress size={12} color="inherit" /> : <SyncIcon fontSize="small" />}
                        disabled={isSyncing || actionLoading === playlistKey}
                        onClick={async () => {
                          setActionLoading(playlistKey);
                          try {
                            await apiService.triggerSyncForSource(playlistKey);
                            showToast(`Sync triggered for ${playlistKey.replace('-', ' ')}`);
                            setTimeout(fetchStatus, 1500); // refresh status
                          } catch (e) {
                            showToast(`Failed to trigger sync for ${playlistKey}`, 'error');
                          } finally {
                            setActionLoading(null);
                          }
                        }}
                        sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 700, px: 1, py: 0 }}
                      >
                        Manual Sync
                      </Button>
                      <Chip 
                        label={runInfo?.status.toUpperCase() || 'NO RUNS'} 
                        color={runInfo?.status === 'completed' ? 'success' : runInfo?.status === 'failed' ? 'error' : 'default'}
                        size="small"
                        sx={{ fontWeight: 700 }}
                      />
                    </Stack>
                  </Box>
                  <Typography variant="body2" color="text.secondary" mb={3}>
                    Sync statistics for {playlistKey.replace('-', ' ')}.
                  </Typography>
                  <Grid container spacing={2}>
                    <Grid item xs={4}>
                      <Typography variant="caption" color="text.secondary">Downloaded</Typography>
                      <Typography variant="h5" color="success.main" sx={{ fontWeight: 700 }}>
                        {runInfo?.tracks_downloaded ?? 0}
                      </Typography>
                    </Grid>
                    <Grid item xs={4}>
                      <Typography variant="caption" color="text.secondary">Skipped</Typography>
                      <Typography variant="h5" color="secondary.main" sx={{ fontWeight: 700 }}>
                        {runInfo?.tracks_skipped ?? 0}
                      </Typography>
                    </Grid>
                    <Grid item xs={4}>
                      <Typography variant="caption" color="text.secondary">Failed</Typography>
                      <Typography variant="h5" color="error.main" sx={{ fontWeight: 700 }}>
                        {runInfo?.tracks_failed ?? 0}
                      </Typography>
                    </Grid>
                  </Grid>
                  {runInfo?.timestamp && (
                    <Typography variant="caption" color="text.secondary" display="block" mt={3}>
                      Last run: {new Date(runInfo.timestamp).toLocaleString()}
                    </Typography>
                  )}
                </CardContent>
              </Card>
            </Grid>
          );
        })}
      </Grid>

      {/* Tabs with Last Run info */}
      <Card sx={{ borderRadius: 4, border: `1px solid ${theme.palette.divider}` }}>
        {activePlaylists.length > 0 && (
          <Box sx={{ borderBottom: 1, borderColor: 'divider', px: 2, pt: 1 }}>
            <Tabs 
              value={activeTab} 
              onChange={(_, newValue) => setActiveTab(newValue)}
            >
              {activePlaylists.map(pl => (
                <Tab key={pl} label={pl.replace('-', ' ').toUpperCase()} />
              ))}
            </Tabs>
          </Box>
        )}
        <CardContent sx={{ p: { xs: 2, sm: 4 } }}>
          {currentTabRun ? (
            <Grid container spacing={{ xs: 2, sm: 3 }} alignItems="center">
              <Grid item xs={12} md={8}>
                <Box display="flex" alignItems="center" gap={1.5} mb={1}>
                  {currentTabRun.status === 'completed' ? (
                    <SuccessIcon color="success" sx={{ fontSize: 28 }} />
                  ) : (
                    <ErrorIcon color="error" sx={{ fontSize: 28 }} />
                  )}
                  <Typography variant="h5" sx={{ fontWeight: 700 }}>
                    {currentTabName} Run #{currentTabRun.id}
                  </Typography>
                </Box>
                <Typography variant="body2" color="text.secondary" mb={3}>
                  Completed at {new Date(currentTabRun.timestamp).toLocaleString()}
                </Typography>

                {currentTabRun.error_message && (
                  <Alert severity="error" sx={{ mt: 1, borderRadius: 2 }}>
                    {currentTabRun.error_message}
                  </Alert>
                )}
              </Grid>

              <Grid item xs={12} md={4}>
                <Box sx={{ bgcolor: 'action.hover', p: 3, borderRadius: 3, display: 'flex', flexDirection: 'column', gap: 1 }}>
                  <Box display="flex" justifyContent="space-between">
                    <Typography variant="body2" color="text.secondary">Total Tracks:</Typography>
                    <Typography variant="body2" sx={{ fontWeight: 700 }}>{currentTabRun.tracks_found}</Typography>
                  </Box>
                  <Divider />
                  <Box display="flex" justifyContent="space-between">
                    <Typography variant="body2" color="text.secondary">Downloaded:</Typography>
                    <Typography variant="body2" color="success.main" sx={{ fontWeight: 700 }}>{currentTabRun.tracks_downloaded}</Typography>
                  </Box>
                  <Box display="flex" justifyContent="space-between">
                    <Typography variant="body2" color="text.secondary">Skipped:</Typography>
                    <Typography variant="body2" color="secondary.main" sx={{ fontWeight: 700 }}>{currentTabRun.tracks_skipped}</Typography>
                  </Box>
                  <Box display="flex" justifyContent="space-between">
                    <Typography variant="body2" color="text.secondary">Failed:</Typography>
                    <Typography variant="body2" color="error.main" sx={{ fontWeight: 700 }}>{currentTabRun.tracks_failed}</Typography>
                  </Box>
                </Box>
              </Grid>
            </Grid>
          ) : (
            <Box textAlign="center" py={4} display="flex" flexDirection="column" alignItems="center" gap={1}>
              <InfoIcon color="action" sx={{ fontSize: 40 }} />
              <Typography color="text.secondary">No sync runs recorded yet for this playlist.</Typography>
            </Box>
          )}
        </CardContent>
      </Card>

      {/* Navidrome Server Stats Card */}
      <Card sx={{ 
        borderRadius: 4, 
        border: `1px solid ${theme.palette.divider}`,
        background: theme.palette.mode === 'dark' 
          ? 'linear-gradient(180deg, #121118 0%, #1a1921 100%)' 
          : 'linear-gradient(180deg, #fbfbfe 0%, #f4f4f9 100%)'
      }}>
        <CardContent sx={{ p: { xs: 2, sm: 4 } }}>
          <Box display="flex" alignItems="center" gap={1.5} mb={3}>
            <StorageIcon color="primary" />
            <Typography variant="h6" sx={{ fontWeight: 800 }}>Navidrome Server Stats</Typography>
          </Box>
          {statsLoading ? (
            <Box display="flex" justifyContent="center" py={3}>
              <CircularProgress size={30} />
            </Box>
          ) : navidromeStats ? (
            <Grid container spacing={{ xs: 2, sm: 3 }}>
              <Grid item xs={12} sm={4}>
                <Box display="flex" alignItems="center" gap={2} sx={{ p: 2, bgcolor: 'action.hover', borderRadius: 3 }}>
                  <Avatar sx={{ bgcolor: 'rgba(98, 0, 234, 0.1)', color: 'primary.main', width: 56, height: 56 }}>
                    <MusicIcon sx={{ fontSize: 28 }} />
                  </Avatar>
                  <Box>
                    <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>Total Songs</Typography>
                    <Typography variant="h5" sx={{ fontWeight: 800 }}>{navidromeStats.songs.toLocaleString()}</Typography>
                  </Box>
                </Box>
              </Grid>
              
              <Grid item xs={12} sm={4}>
                <Box display="flex" alignItems="center" gap={2} sx={{ p: 2, bgcolor: 'action.hover', borderRadius: 3 }}>
                  <Avatar sx={{ bgcolor: 'rgba(3, 218, 198, 0.1)', color: 'teal', width: 56, height: 56 }}>
                    <AlbumIcon sx={{ fontSize: 28 }} />
                  </Avatar>
                  <Box>
                    <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>Total Albums</Typography>
                    <Typography variant="h5" sx={{ fontWeight: 800 }}>{navidromeStats.albums.toLocaleString()}</Typography>
                  </Box>
                </Box>
              </Grid>

              <Grid item xs={12} sm={4}>
                <Box display="flex" alignItems="center" gap={2} sx={{ p: 2, bgcolor: 'action.hover', borderRadius: 3 }}>
                  <Avatar sx={{ bgcolor: 'rgba(244, 67, 54, 0.1)', color: 'error.main', width: 56, height: 56 }}>
                    <ArtistIcon sx={{ fontSize: 28 }} />
                  </Avatar>
                  <Box>
                    <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>Total Artists</Typography>
                    <Typography variant="h5" sx={{ fontWeight: 800 }}>{navidromeStats.artists.toLocaleString()}</Typography>
                  </Box>
                </Box>
              </Grid>
            </Grid>
          ) : (
            <Typography color="text.secondary">Navidrome credentials are incomplete or server is offline.</Typography>
          )}
        </CardContent>
      </Card>

      {/* Embedded CSS for animations */}
      <style>{`
        @keyframes spin {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }
      `}</style>
    </Box>
  );
};

export default Dashboard;
