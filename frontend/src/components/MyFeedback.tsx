import React, { useEffect, useState } from 'react';
import { 
  Box, Card, CardContent, Typography, CircularProgress, 
  List, ListItem, ListItemAvatar, ListItemText, Avatar, 
  Chip, Tooltip, IconButton, useTheme, Alert, Snackbar, Button, Tabs, Tab
} from '@mui/material';
import { 
  LibraryMusic as MusicIcon,
  Favorite as FavoriteIcon,
  HeartBroken as HateIcon,
  Sync as SyncIcon
} from '@mui/icons-material';
import { apiService } from '../api';

export const MyFeedback: React.FC = () => {
  const theme = useTheme();
  const [feedbackTracks, setFeedbackTracks] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncingStarred, setSyncingStarred] = useState(false);
  const [activeTab, setActiveTab] = useState<number>(0); // 0 = Loved, 1 = Hated
  const [error, setError] = useState<string | null>(null);
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: 'success' | 'error' }>({
    open: false,
    message: '',
    severity: 'success'
  });
  const [, setLikedTracks] = useState<Set<string>>(() => {
    const saved = localStorage.getItem('likedTracks');
    return saved ? new Set(JSON.parse(saved)) : new Set();
  });

  const fetchFeedback = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiService.getFeedback();
      const initialTracks = data.feedback || [];
      
      const itemsToCheck = initialTracks.map((t: any) => ({ artist: t.artist, title: t.title }));
      let libraryStatusMap = new Map();
      if (itemsToCheck.length > 0) {
        try {
          const checkResults = await apiService.checkAlbumsBatch(itemsToCheck);
          checkResults.forEach((res: any) => {
            libraryStatusMap.set(`${res.artist}-${res.title}`, res);
          });
        } catch (e) {
          console.error("Library check failed", e);
        }
      }

      const missingCovers = initialTracks.filter((t: any) => !t.artwork && !t.cover_medium && !t.cover_small && !t.cover && !t.image_url);
      let coverMap = new Map();
      if (missingCovers.length > 0) {
        const batchCovers = missingCovers.slice(0, 10);
        await Promise.allSettled(batchCovers.map(async (t: any) => {
          try {
            const deezerRes = await apiService.searchDeezer(t.artist + ' ' + t.title, 'track');
            if (deezerRes && deezerRes.data && deezerRes.data.length > 0) {
              const coverUrl = deezerRes.data[0].album?.cover_medium;
              if (coverUrl) coverMap.set(`${t.artist}-${t.title}`, coverUrl);
            }
          } catch (e) {
            // ignore
          }
        }));
      }

      const enrichedTracks = initialTracks.map((t: any) => ({
        ...t,
        cover_url: t.artwork || t.cover_medium || t.cover_small || t.cover || t.image_url || coverMap.get(`${t.artist}-${t.title}`) || '',
        libraryStatus: libraryStatusMap.get(`${t.artist}-${t.title}`)
      }));

      setFeedbackTracks(enrichedTracks);
    } catch (e: any) {
      console.error("Failed to load feedback", e);
      setError(e.response?.data?.detail || "Failed to load feedback from ListenBrainz.");
    } finally {
      setLoading(false);
    }
  };

  const handleSyncStarred = async () => {
    setSyncingStarred(true);
    try {
      const res = await apiService.syncNavidromeStarred();
      setSnackbar({
        open: true,
        message: res.message || "Navidrome loved tracks sync started successfully.",
        severity: 'success'
      });
      setTimeout(() => fetchFeedback(), 3000);
    } catch (err: any) {
      setSnackbar({
        open: true,
        message: err.response?.data?.detail || "Failed to trigger Navidrome loved tracks sync.",
        severity: 'error'
      });
    } finally {
      setSyncingStarred(false);
    }
  };

  useEffect(() => {
    fetchFeedback();
  }, []);

  const lovedTracks = feedbackTracks.filter(t => t.score === 1);
  const hatedTracks = feedbackTracks.filter(t => t.score === -1);
  const currentList = activeTab === 0 ? lovedTracks : hatedTracks;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: { xs: 'flex-start', sm: 'center' }, flexDirection: { xs: 'column', sm: 'row' }, gap: 2 }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 800 }}>My Feedback</Typography>
          <Typography variant="body2" color="text.secondary">
            Loved and Hated tracks recorded on your ListenBrainz account
          </Typography>
        </Box>
        <Button
          variant="contained"
          color="primary"
          startIcon={syncingStarred ? <CircularProgress size={20} color="inherit" /> : <SyncIcon />}
          onClick={handleSyncStarred}
          disabled={syncingStarred}
          sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600, width: { xs: '100%', sm: 'auto' } }}
        >
          {syncingStarred ? 'Syncing...' : 'Sync Navidrome Loved'}
        </Button>
      </Box>

      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

      <Card sx={{ 
        background: theme.palette.mode === 'dark' ? '#1c1b22' : '#ffffff',
        borderRadius: 4,
        boxShadow: '0 4px 20px rgba(0,0,0,0.05)'
      }}>
        <CardContent sx={{ p: { xs: 2, sm: 4 } }}>
          <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 3 }}>
            <Tabs 
              value={activeTab} 
              onChange={(_, val) => setActiveTab(val)}
              sx={{
                '& .MuiTab-root': {
                  fontWeight: 800,
                  fontSize: '0.95rem',
                  textTransform: 'none'
                }
              }}
            >
              <Tab 
                icon={<FavoriteIcon color="error" sx={{ mr: 1, fontSize: 18 }} />} 
                iconPosition="start" 
                label={`Loved Tracks (${lovedTracks.length})`} 
              />
              <Tab 
                icon={<HateIcon sx={{ mr: 1, fontSize: 18, color: theme.palette.warning.main }} />} 
                iconPosition="start" 
                label={`Hated Tracks (${hatedTracks.length})`} 
              />
            </Tabs>
          </Box>

          {loading ? (
            <Box display="flex" justifyContent="center" p={4}><CircularProgress /></Box>
          ) : currentList.length === 0 ? (
            <Typography color="text.secondary" align="center" sx={{ py: 6 }}>
              {activeTab === 0 ? 'No loved tracks found.' : 'No hated tracks found.'}
            </Typography>
          ) : (
            <List sx={{ width: '100%', bgcolor: 'transparent' }}>
              {currentList.map((track, i) => {
                const isLoved = track.score === 1;
                return (
                  <ListItem 
                    key={i} 
                    divider={i < currentList.length - 1}
                    sx={{ 
                      py: 2, 
                      px: { xs: 0, sm: 2 },
                      transition: 'background-color 0.2s',
                      '&:hover': { bgcolor: theme.palette.mode === 'dark' ? 'rgba(255,255,255,0.02)' : 'rgba(0,0,0,0.02)' }
                    }}
                  >
                    <ListItemAvatar>
                      <Avatar 
                        variant="rounded" 
                        src={track.cover_url || track.artwork || undefined}
                        sx={{ width: 52, height: 52, mr: 2, borderRadius: 2, boxShadow: '0 2px 8px rgba(0,0,0,0.15)', bgcolor: isLoved ? 'rgba(211, 47, 47, 0.1)' : 'rgba(0, 0, 0, 0.05)' }}
                      >
                        <MusicIcon color={isLoved ? "error" : "action"} />
                      </Avatar>
                    </ListItemAvatar>
                    <ListItemText
                      primary={
                        <Box display="flex" alignItems="center" gap={1}>
                          <Typography sx={{ fontWeight: 700, fontSize: '1.05rem' }}>{track.title}</Typography>
                          {(() => {
                            const lib = track.libraryStatus;
                            if (lib && lib.exists) {
                              if (lib.quality_status === 'worse') {
                                return <Chip label="Upgrade" size="small" sx={{ bgcolor: 'orange', color: 'white', fontWeight: 700, height: 20, fontSize: '0.7rem' }} />;
                              }
                              return <Chip label="In Library" size="small" color="success" sx={{ fontWeight: 700, height: 20, fontSize: '0.7rem' }} />;
                            }
                            return <Chip label="Missing" size="small" color="error" sx={{ fontWeight: 700, height: 20, fontSize: '0.7rem' }} />;
                          })()}
                        </Box>
                      }
                      secondary={
                        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                          {track.artist} {track.album ? `• ${track.album}` : ''}
                        </Typography>
                      }
                    />
                    <Box display="flex" alignItems="center" gap={2}>
                      <Chip 
                        label={isLoved ? 'LOVED' : 'HATED'} 
                        size="small" 
                        color={isLoved ? 'error' : 'warning'}
                        sx={{ fontWeight: 700 }}
                      />
                      
                      <Tooltip title={isLoved ? "Remove Love (Unlike)" : "Remove Hate"}>
                        <IconButton 
                          onClick={async () => {
                            try {
                              await apiService.likeTrack(track.artist, track.title, "", 0);
                              setFeedbackTracks(prev => prev.filter(t => t.mbid !== track.mbid || t.title !== track.title));
                              if (isLoved) {
                                const key = `${track.artist}-${track.title}`;
                                setLikedTracks(prev => {
                                  const next = new Set(prev);
                                  next.delete(key);
                                  localStorage.setItem('likedTracks', JSON.stringify(Array.from(next)));
                                  return next;
                                });
                              }
                              setSnackbar({
                                open: true,
                                message: `Successfully removed ${isLoved ? 'love' : 'hate'} rating.`,
                                severity: 'success'
                              });
                            } catch (err: any) {
                              setSnackbar({
                                open: true,
                                message: err.response?.data?.detail || "Failed to remove feedback.",
                                severity: 'error'
                              });
                            }
                          }}
                          color="default"
                        >
                          {isLoved ? <FavoriteIcon color="error" /> : <HateIcon color="warning" />}
                        </IconButton>
                      </Tooltip>
                    </Box>
                  </ListItem>
                );
              })}
            </List>
          )}
        </CardContent>
      </Card>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar(s => ({ ...s, open: false }))}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity={snackbar.severity} variant="filled" onClose={() => setSnackbar(s => ({ ...s, open: false }))}>
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
};
export default MyFeedback;
