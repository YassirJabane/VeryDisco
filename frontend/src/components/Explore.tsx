import React, { useEffect, useState } from 'react';
import { 
  Box, Card, CardContent, Typography, CircularProgress, 
  List, ListItem, ListItemAvatar, ListItemText, Avatar, 
  Chip, Tooltip, IconButton, useTheme, Tabs, Tab, Button,
  LinearProgress, Grid, Alert, Dialog, DialogTitle, DialogContent, 
  DialogActions, Table, TableBody, TableCell, TableContainer, 
  TableHead, TableRow, Paper
} from '@mui/material';
import { 
  LibraryMusic as MusicIcon,
  Favorite as FavoriteIcon,
  FavoriteBorder as FavoriteBorderIcon,
  HeartBroken as HeartBrokenIcon,
  Sync as SyncIcon,
  Stop as StopIcon,
  CheckCircle as SuccessIcon,
  Error as ErrorIcon,
  CloudDownload as DownloadIcon,
  SkipNext as SkipIcon,
  Info as InfoIcon
} from '@mui/icons-material';
import { apiService, GetStatusResponse } from '../api';

export const Explore: React.FC = () => {
  const theme = useTheme();
  const [playlist, setPlaylist] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [source, setSource] = useState<string>('weekly-exploration');
  const [activePlaylists, setActivePlaylists] = useState<string[]>([]);
  const [status, setStatus] = useState<GetStatusResponse | null>(null);
  const [syncingActive, setSyncingActive] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [triggering, setTriggering] = useState(false);
  
  const [likedTracks, setLikedTracks] = useState<Set<string>>(() => {
    const saved = localStorage.getItem('likedTracks');
    return saved ? new Set(JSON.parse(saved)) : new Set();
  });

  const [hatedTracks, setHatedTracks] = useState<Set<string>>(() => {
    const saved = localStorage.getItem('hatedTracks');
    return saved ? new Set(JSON.parse(saved)) : new Set();
  });

  const [manualSearchOpen, setManualSearchOpen] = useState(false);
  const [searchTrack, setSearchTrack] = useState<any | null>(null);
  const [searchingTrackCandidates, setSearchingTrackCandidates] = useState(false);
  const [trackCandidates, setTrackCandidates] = useState<any[]>([]);
  const [grabbingTrackKey, setGrabbingTrackKey] = useState<string | null>(null);

  const handleOpenManualSearch = async (track: any) => {
    setSearchTrack(track);
    setManualSearchOpen(true);
    setSearchingTrackCandidates(true);
    setTrackCandidates([]);
    try {
      const results = await apiService.searchTrackCandidates(track.artist, track.title, track.album);
      setTrackCandidates(results);
    } catch (err: any) {
      alert("Failed to search track candidates on Slskd.");
    } finally {
      setSearchingTrackCandidates(false);
    }
  };

  const handleGrabTrack = async (cand: any) => {
    if (!searchTrack) return;
    const key = `${cand.username}-${cand.filename}`;
    setGrabbingTrackKey(key);
    try {
      await apiService.grabTrack(
        searchTrack.artist,
        searchTrack.title,
        searchTrack.album || '',
        cand.username,
        cand.filename,
        cand.size
      );
      alert(`Manual grab initiated from "${cand.username}" for "${searchTrack.title}".`);
      setManualSearchOpen(false);
    } catch (err: any) {
      alert(err.response?.data?.detail || "Failed to initiate grab.");
    } finally {
      setGrabbingTrackKey(null);
    }
  };

  const getFilenameOnly = (filepath: string) => {
    const parts = filepath.split(/[\\/]/);
    return parts[parts.length - 1];
  };

  const formatSize = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const fetchPlaylist = async (targetSource: string, silent: boolean = false) => {
    if (!silent) setLoading(true);
    try {
      const data = await apiService.getCurrentPlaylist(targetSource);
      setPlaylist(data.tracks);
    } catch (e: any) {
      console.error("Failed to load playlist", e);
    } finally {
      if (!silent) setLoading(false);
    }
  };

  const fetchStatus = async () => {
    try {
      const data = await apiService.getStatus();
      setStatus(data);
      if (data.active_playlists) {
        setActivePlaylists(data.active_playlists);
        if (data.active_playlists.length > 0) {
          setSource(prev => data.active_playlists.includes(prev) ? prev : data.active_playlists[0]);
        }
      }
    } catch (e) {
      console.error("Failed to fetch status", e);
    }
  };

  useEffect(() => {
    fetchStatus();
  }, []);

  useEffect(() => {
    if (source) fetchPlaylist(source);
  }, [source]);

  useEffect(() => {
    const interval = setInterval(() => {
      fetchStatus();
      if (source) {
        fetchPlaylist(source, true);
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [source]);

  const handleSync = async () => {
    setTriggering(true);
    try {
      await apiService.triggerSyncForSource(source);
      await fetchStatus();
    } catch (e) {
      console.error("Failed to trigger sync", e);
    } finally {
      setTriggering(false);
    }
  };

  const handleStop = async () => {
    setStopping(true);
    try {
      await apiService.stopSync();
      await fetchStatus();
    } catch (e) {
      console.error("Failed to stop sync", e);
    } finally {
      setStopping(false);
    }
  };

  const handleSubmitFeedback = async (track: any, score: number) => {
    const key = `${track.artist}-${track.title}`;
    const isLiked = likedTracks.has(key);
    const isHated = hatedTracks.has(key);

    let finalScore = score;
    if (score === 1 && isLiked) finalScore = 0;
    if (score === -1 && isHated) finalScore = 0;

    setLikedTracks(prev => {
      const next = new Set(prev);
      if (finalScore === 1) {
        next.add(key);
      } else {
        next.delete(key);
      }
      localStorage.setItem('likedTracks', JSON.stringify(Array.from(next)));
      return next;
    });

    setHatedTracks(prev => {
      const next = new Set(prev);
      if (finalScore === -1) {
        next.add(key);
      } else {
        next.delete(key);
      }
      localStorage.setItem('hatedTracks', JSON.stringify(Array.from(next)));
      return next;
    });

    try {
      await apiService.likeTrack(track.artist, track.title, track.album, finalScore);
    } catch (e: any) {
      console.error("Failed to submit feedback", e);
      alert(e.response?.data?.detail || "Failed to submit feedback to ListenBrainz. Check your token.");
      setLikedTracks(prev => {
        const next = new Set(prev);
        if (isLiked) next.add(key); else next.delete(key);
        localStorage.setItem('likedTracks', JSON.stringify(Array.from(next)));
        return next;
      });
      setHatedTracks(prev => {
        const next = new Set(prev);
        if (isHated) next.add(key); else next.delete(key);
        localStorage.setItem('hatedTracks', JSON.stringify(Array.from(next)));
        return next;
      });
    }
  };

  // Determine if active sync is for the current selected source
  // Currently, we store the active source in state. If the sync progress shows active, we can check.
  const isSyncingThisSource = status?.is_syncing && status?.progress?.status === 'running';
  const latestRunForSource = status?.latest_runs?.[source];

  const found = isSyncingThisSource ? status?.progress?.tracks_found : latestRunForSource?.tracks_found || 0;
  const downloaded = isSyncingThisSource ? status?.progress?.tracks_downloaded : latestRunForSource?.tracks_downloaded || 0;
  const skipped = isSyncingThisSource ? status?.progress?.tracks_skipped : latestRunForSource?.tracks_skipped || 0;
  const failed = isSyncingThisSource ? status?.progress?.tracks_failed : latestRunForSource?.tracks_failed || 0;

  const totalProcessed = downloaded + skipped + failed;
  const progressPercent = found > 0 ? (totalProcessed / found) * 100 : 0;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <Box display="flex" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={2}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 800 }}>Explore Playlists</Typography>
          <Typography variant="body2" color="text.secondary">
            Sync weekly discovery and jams directly into your library
          </Typography>
        </Box>
        <Box display="flex" gap={2}>
          {isSyncingThisSource ? (
            <Button
              variant="contained"
              size="large"
              color="error"
              startIcon={stopping ? <CircularProgress size={20} color="inherit" /> : <StopIcon />}
              disabled={stopping}
              onClick={handleStop}
              sx={{ px: 4, py: 1.5, borderRadius: 3, boxShadow: '0 4px 20px rgba(244, 67, 54, 0.3)' }}
            >
              Stop Sync
            </Button>
          ) : (
            <Button
              variant="contained"
              size="large"
              color="primary"
              startIcon={<SyncIcon />}
              disabled={triggering || !status?.is_configured}
              onClick={handleSync}
              sx={{
                px: 4,
                py: 1.5,
                borderRadius: 3,
                boxShadow: theme.palette.mode === 'dark' 
                  ? '0 4px 20px rgba(179, 136, 255, 0.4)' 
                  : '0 4px 20px rgba(98, 0, 234, 0.2)',
              }}
            >
              Sync {source === 'weekly-exploration' ? 'Exploration' : 'Jams'}
            </Button>
          )}
        </Box>
      </Box>

      {/* Real-time / Last run statistics area */}
      <Card sx={{ borderRadius: 4, border: `1px solid ${theme.palette.divider}` }}>
        <CardContent sx={{ p: 3 }}>
          <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>
            {isSyncingThisSource ? 'Active Synchronization' : 'Last Synchronization Run'}
          </Typography>
          <Grid container spacing={{ xs: 2, sm: 3 }} alignItems="center">
            <Grid item xs={12} md={7}>
              {isSyncingThisSource ? (
                <Box>
                  <Box display="flex" justifyContent="space-between" mb={1}>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      Progress ({totalProcessed}/{found} tracks)
                    </Typography>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {Math.round(progressPercent)}%
                    </Typography>
                  </Box>
                  <LinearProgress 
                    variant="determinate" 
                    value={progressPercent} 
                    sx={{ height: 10, borderRadius: 5, mb: 1 }} 
                  />
                  <Typography variant="caption" color="text.secondary">
                    Retrieving metadata, lyrics and cover art from Deezer...
                  </Typography>
                </Box>
              ) : latestRunForSource ? (
                <Box display="flex" alignItems="center" gap={1.5}>
                  {latestRunForSource.status === 'completed' ? (
                    <SuccessIcon color="success" sx={{ fontSize: 24 }} />
                  ) : (
                    <ErrorIcon color="error" sx={{ fontSize: 24 }} />
                  )}
                  <Box>
                    <Typography variant="body1" sx={{ fontWeight: 700 }}>
                      Status: {latestRunForSource.status.toUpperCase()}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Completed at {new Date(latestRunForSource.timestamp).toLocaleString()}
                    </Typography>
                  </Box>
                </Box>
              ) : (
                <Box display="flex" alignItems="center" gap={1}>
                  <InfoIcon color="action" />
                  <Typography variant="body2" color="text.secondary">
                    No run logs for this playlist yet.
                  </Typography>
                </Box>
              )}
            </Grid>

            <Grid item xs={12} md={5}>
              <Box display="flex" gap={2} justifyContent={{ xs: 'flex-start', md: 'flex-end' }}>
                <Box sx={{ textAlign: 'center', minWidth: 70 }}>
                  <Typography variant="caption" color="text.secondary">Found</Typography>
                  <Typography variant="h6" sx={{ fontWeight: 700 }}>{found}</Typography>
                </Box>
                <Box sx={{ textAlign: 'center', minWidth: 70 }}>
                  <Typography variant="caption" color="text.secondary">Downloaded</Typography>
                  <Typography variant="h6" color="success.main" sx={{ fontWeight: 700 }}>{downloaded}</Typography>
                </Box>
                <Box sx={{ textAlign: 'center', minWidth: 70 }}>
                  <Typography variant="caption" color="text.secondary">Skipped</Typography>
                  <Typography variant="h6" color="secondary.main" sx={{ fontWeight: 700 }}>{skipped}</Typography>
                </Box>
                <Box sx={{ textAlign: 'center', minWidth: 70 }}>
                  <Typography variant="caption" color="text.secondary">Failed</Typography>
                  <Typography variant="h6" color="error.main" sx={{ fontWeight: 700 }}>{failed}</Typography>
                </Box>
              </Box>
            </Grid>
          </Grid>
        </CardContent>
      </Card>

      <Card sx={{ 
        background: theme.palette.mode === 'dark' ? '#1c1b22' : '#ffffff',
        borderRadius: 4,
        boxShadow: '0 4px 20px rgba(0,0,0,0.05)'
      }}>
        <CardContent sx={{ p: { xs: 2, sm: 4 } }}>
          <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 3, display: 'flex', flexDirection: 'column', gap: 1.5 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 2 }}>
              <Tabs 
                value={activePlaylists.indexOf(source) >= 0 ? activePlaylists.indexOf(source) : 0} 
                onChange={(_, val) => setSource(activePlaylists[val])}
                variant="scrollable"
                scrollButtons="auto"
                allowScrollButtonsMobile
                sx={{
                  maxWidth: '100%',
                  minHeight: 48,
                  '& .MuiTab-root': {
                    fontWeight: 800,
                    fontSize: { xs: '0.8rem', sm: '0.9rem' },
                    minWidth: 'auto',
                    px: { xs: 2, sm: 3 },
                    py: 1.5,
                    whiteSpace: 'nowrap'
                  }
                }}
              >
                {activePlaylists.map(pl => (
                  <Tab key={pl} label={pl.replace('-', ' ').toUpperCase()} />
                ))}
              </Tabs>
              {status?.next_run && (
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, pb: 1, px: { xs: 0, sm: 1 } }}>
                  <Typography variant="caption" color="text.secondary" fontWeight={700}>
                    NEXT SCHEDULED RUN:
                  </Typography>
                  <Chip 
                    label={new Date(status.next_run).toLocaleString()} 
                    size="small" 
                    variant="outlined" 
                    color="primary"
                    sx={{ fontWeight: 700, height: 22, fontSize: '0.7rem', borderRadius: 2 }}
                  />
                </Box>
              )}
            </Box>
          </Box>

          {loading ? (
            <Box display="flex" justifyContent="center" p={4}><CircularProgress /></Box>
          ) : playlist.length === 0 ? (
            <Typography color="text.secondary">No tracks found. Check your ListenBrainz connection or config.</Typography>
          ) : (
            <List sx={{ width: '100%', bgcolor: 'transparent' }}>
              {playlist.map((track, i) => {
                const key = `${track.artist}-${track.title}`;
                const isLiked = likedTracks.has(key);
                return (
                  <ListItem 
                    key={i} 
                    divider={i < playlist.length - 1}
                    sx={{ 
                      py: 2.5, 
                      px: { xs: 1, sm: 2 },
                      flexDirection: 'column',
                      alignItems: 'stretch',
                      gap: 1.5,
                      transition: 'background-color 0.2s',
                      '&:hover': { bgcolor: theme.palette.mode === 'dark' ? 'rgba(255,255,255,0.02)' : 'rgba(0,0,0,0.02)' }
                    }}
                  >
                    <Box display="flex" alignItems="center" sx={{ width: '100%' }}>
                      <ListItemAvatar>
                        <Avatar 
                          variant="rounded" 
                          src={track.artwork} 
                          sx={{ width: 64, height: 64, mr: 2, boxShadow: '0 4px 10px rgba(0,0,0,0.15)' }}
                        >
                          <MusicIcon />
                        </Avatar>
                      </ListItemAvatar>
                      <ListItemText
                        primary={<Typography sx={{ fontWeight: 700, fontSize: '1.15rem' }}>{track.title}</Typography>}
                        secondary={
                          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                            {track.artist} {track.album ? `• ${track.album}` : ''}
                          </Typography>
                        }
                      />
                    </Box>

                    <Box display="flex" justifyContent="space-between" alignItems="center" sx={{ pl: { xs: 0, sm: 10 }, width: '100%' }}>
                      <Box>
                        {track.status !== 'pending' && (
                          <Tooltip title={track.status === 'failed' && track.error_reason ? track.error_reason : ""}>
                            <Chip 
                              label={track.status.toUpperCase()} 
                              size="small" 
                              color={track.status === 'downloaded' ? 'success' : track.status === 'failed' ? 'error' : 'default'}
                              sx={{ fontWeight: 800, cursor: track.status === 'failed' ? 'help' : 'default' }}
                            />
                          </Tooltip>
                        )}
                      </Box>
                      <Box display="flex" gap={1} flexWrap="wrap" justifyContent="flex-end">
                        <Tooltip title={isLiked ? "Loved on LB (Album Sync Active)" : "Love track & Sync full Album"}>
                          <Chip
                            icon={isLiked ? <FavoriteIcon fontSize="small" /> : <FavoriteBorderIcon fontSize="small" />}
                            label="Auto Grab"
                            clickable
                            onClick={() => handleSubmitFeedback(track, 1)}
                            color={isLiked ? "error" : "default"}
                            variant={isLiked ? "filled" : "outlined"}
                            sx={{ fontWeight: 700 }}
                          />
                        </Tooltip>
                        <Tooltip title={hatedTracks.has(key) ? "Hated on LB" : "Hate/Dislike track on LB"}>
                          <Chip
                            icon={hatedTracks.has(key) ? <HeartBrokenIcon fontSize="small" /> : <HeartBrokenIcon fontSize="small" sx={{ opacity: 0.5 }} />}
                            label="Hate"
                            clickable
                            onClick={() => handleSubmitFeedback(track, -1)}
                            color={hatedTracks.has(key) ? "warning" : "default"}
                            variant={hatedTracks.has(key) ? "filled" : "outlined"}
                            sx={{ fontWeight: 700 }}
                          />
                        </Tooltip>
                        <Tooltip title="Manual Search & Grab from Soulseek">
                          <Chip
                            icon={<DownloadIcon fontSize="small" />}
                            label="Manual Grab"
                            clickable
                            onClick={() => handleOpenManualSearch(track)}
                            color="primary"
                            variant="outlined"
                            sx={{ fontWeight: 700 }}
                          />
                        </Tooltip>
                      </Box>
                    </Box>
                  </ListItem>
                );
              })}
            </List>
          )}
        </CardContent>
      </Card>

      {/* Manual Search Slskd Dialog */}
      <Dialog 
        open={manualSearchOpen} 
        onClose={() => setManualSearchOpen(false)} 
        maxWidth="xl" 
        fullWidth
        PaperProps={{
          sx: {
            borderRadius: 4,
            background: theme.palette.mode === 'dark' ? '#1c1b22' : '#ffffff',
            boxShadow: '0 8px 32px rgba(0,0,0,0.2)'
          }
        }}
      >
        <DialogTitle sx={{ fontWeight: 800, pb: 1 }}>
          Manual Search: {searchTrack ? `${searchTrack.artist} - ${searchTrack.title}` : ''}
        </DialogTitle>
        <DialogContent dividers>
          {searchingTrackCandidates ? (
            <Box display="flex" flexDirection="column" alignItems="center" justifyContent="center" py={8} gap={2}>
              <CircularProgress size={50} />
              <Typography variant="body2" color="text.secondary">Searching Soulseek for track candidates...</Typography>
            </Box>
          ) : trackCandidates.length === 0 ? (
            <Typography variant="body1" color="text.secondary" align="center" sx={{ py: 6 }}>
              No candidates found on Soulseek. Make sure Slskd is connected and the track name is correct.
            </Typography>
          ) : (
            <TableContainer component={Paper} sx={{ borderRadius: 3, boxShadow: 'none', border: `1px solid ${theme.palette.divider}` }}>
              <Table size="medium">
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ fontWeight: 700 }}>Peer</TableCell>
                    <TableCell sx={{ fontWeight: 700 }}>Filename</TableCell>
                    <TableCell sx={{ fontWeight: 700 }} align="right">Length</TableCell>
                    <TableCell sx={{ fontWeight: 700 }} align="right">Size</TableCell>
                    <TableCell sx={{ fontWeight: 700 }} align="right">Bitrate</TableCell>
                    <TableCell sx={{ fontWeight: 700 }} align="center">Action</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {trackCandidates.map((cand, idx) => {
                    const isGrabbing = grabbingTrackKey === `${cand.username}-${cand.filename}`;
                    return (
                      <TableRow key={idx} hover>
                        <TableCell sx={{ fontWeight: 600 }}>{cand.username}</TableCell>
                        <TableCell sx={{ maxWidth: 400, wordBreak: 'break-all' }}>
                          <Tooltip title={cand.filename}>
                            <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                              {getFilenameOnly(cand.filename)}
                            </Typography>
                          </Tooltip>
                        </TableCell>
                        <TableCell align="right">
                          {cand.length ? `${Math.floor(cand.length / 60)}:${Math.floor(cand.length % 60).toString().padStart(2, '0')}` : '--:--'}
                        </TableCell>
                        <TableCell align="right">{formatSize(cand.size)}</TableCell>
                        <TableCell align="right">
                          {cand.bitrate ? (
                            <Chip 
                              label={`${cand.bitrate} kbps`} 
                              size="small" 
                              color={cand.bitrate >= 320 ? 'success' : 'default'}
                              variant="outlined"
                              sx={{ fontWeight: 600 }}
                            />
                          ) : (
                            <Typography variant="caption" color="text.secondary">VBR/Unknown</Typography>
                          )}
                        </TableCell>
                        <TableCell align="center">
                          <Button
                            variant="contained"
                            size="small"
                            onClick={() => handleGrabTrack(cand)}
                            disabled={grabbingTrackKey !== null}
                            startIcon={isGrabbing ? <CircularProgress size={14} color="inherit" /> : <DownloadIcon />}
                            sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600 }}
                          >
                            {isGrabbing ? 'Grabbing...' : 'Grab'}
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </DialogContent>
        <DialogActions sx={{ p: 2.5 }}>
          <Button onClick={() => setManualSearchOpen(false)} variant="outlined" sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600 }}>
            Close
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};
export default Explore;
