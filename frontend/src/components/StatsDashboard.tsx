import React, { useEffect, useState } from 'react';
import {
  Box, Card, CardContent, Typography, Grid, CircularProgress,
  Alert, Divider, useTheme, List, ListItem, ListItemText, Avatar, Chip, Stack
} from '@mui/material';
import {
  BarChart as BarIcon,
  MusicNote as MusicIcon,
  Person as ArtistIcon,
  FolderOpen as FolderIcon,
  HourglassEmpty as PlayIcon,
  Album as AlbumIcon
} from '@mui/icons-material';
import { apiService } from '../api';

interface TopArtist {
  artist: string;
  count: number;
}

interface TopTrack {
  track: string;
  count: number;
}

interface TopAlbum {
  name: string;
  artist: string;
  playCount: number;
  id: string;
}

interface LBArtist {
  artist_name: string;
  listen_count: number;
}

interface LBRelease {
  release_name: string;
  artist_name: string;
  listen_count: number;
}

interface StatsData {
  navidrome_history: {
    top_artists: TopArtist[];
    top_tracks: TopTrack[];
    top_albums: TopAlbum[];
    heatmap: Record<string, number>;
    weekday_heatmap: Record<string, number>;
  };
  listenbrainz: {
    artists?: LBArtist[];
    releases?: LBRelease[];
  };
  library_stats: {
    total_tracks: number;
    listened_tracks: number;
    discovery_rate: number;
  };
}

export const StatsDashboard: React.FC = () => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<StatsData | null>(null);

  // Tab mode state toggles for decluttered UI
  const [lbMode, setLbMode] = useState<'artists' | 'releases'>('artists');
  const [topMode, setTopMode] = useState<'artists' | 'tracks' | 'heatmap'>('artists');

  useEffect(() => {
    apiService.getStatsSummary()
      .then(res => setData(res))
      .catch(() => setError("Failed to load listening statistics."))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="50vh">
        <CircularProgress />
      </Box>
    );
  }

  if (error || !data) {
    return (
      <Box p={3}>
        <Alert severity="error">{error || "No data available."}</Alert>
      </Box>
    );
  }

  const { navidrome_history, listenbrainz, library_stats } = data;
  const cardBg = isDark ? '#1c1b22' : '#ffffff';

  const hours = Array.from({ length: 24 }, (_, i) => i);
  const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  const maxHourValue = Math.max(...hours.map(h => navidrome_history.heatmap[String(h)] || 0), 1);
  const maxDayValue = Math.max(...weekdays.map((_, i) => navidrome_history.weekday_heatmap[String(i)] || 0), 1);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Header */}
      <Box>
        <Typography variant="h5" sx={{ fontWeight: 800 }}>Listening Stats & Insights</Typography>
        <Typography variant="body2" color="text.secondary">
          Detailed breakdown of your music library coverage and streaming habits
        </Typography>
      </Box>

      {/* Overview Metric Cards */}
      <Grid container spacing={{ xs: 2, sm: 3 }}>
        <Grid item xs={12} sm={4}>
          <Card sx={{ borderRadius: 4, background: cardBg, border: '1px solid', borderColor: 'divider', boxShadow: 'none' }}>
            <CardContent sx={{ display: 'flex', alignItems: 'center', gap: 2, p: 2.5 }}>
              <Avatar sx={{ bgcolor: 'primary.main', width: 48, height: 48, borderRadius: 2.5 }}>
                <FolderIcon sx={{ color: '#fff' }} />
              </Avatar>
              <Box>
                <Typography variant="h5" sx={{ fontWeight: 800 }}>{library_stats.total_tracks}</Typography>
                <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 700 }}>Total Tracks</Typography>
              </Box>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={4}>
          <Card sx={{ borderRadius: 4, background: cardBg, border: '1px solid', borderColor: 'divider', boxShadow: 'none' }}>
            <CardContent sx={{ display: 'flex', alignItems: 'center', gap: 2, p: 2.5 }}>
              <Avatar sx={{ bgcolor: 'secondary.main', width: 48, height: 48, borderRadius: 2.5 }}>
                <PlayIcon sx={{ color: '#fff' }} />
              </Avatar>
              <Box>
                <Typography variant="h5" sx={{ fontWeight: 800 }}>{library_stats.listened_tracks}</Typography>
                <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 700 }}>Listened Tracks</Typography>
              </Box>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={4}>
          <Card sx={{ borderRadius: 4, background: cardBg, border: '1px solid', borderColor: 'divider', boxShadow: 'none' }}>
            <CardContent sx={{ display: 'flex', alignItems: 'center', gap: 2, p: 2.5 }}>
              <Avatar sx={{ bgcolor: 'success.main', width: 48, height: 48, borderRadius: 2.5 }}>
                <BarIcon sx={{ color: '#fff' }} />
              </Avatar>
              <Box>
                <Typography variant="h5" sx={{ fontWeight: 800 }}>{library_stats.discovery_rate}%</Typography>
                <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 700 }}>Discovery Rate</Typography>
              </Box>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Navidrome Top Stats with Mode Selector Chips */}
      <Card sx={{ borderRadius: 4, background: cardBg, border: '1px solid', borderColor: 'divider', boxShadow: 'none' }}>
        <CardContent sx={{ p: { xs: 2.5, sm: 3 } }}>
          <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ xs: 'flex-start', sm: 'center' }} mb={2.5} gap={1.5}>
            <Typography variant="h6" sx={{ fontWeight: 800 }}>Navidrome Activity</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              <Chip
                icon={<ArtistIcon sx={{ fontSize: 16 }} />}
                label="Top Artists"
                clickable
                color={topMode === 'artists' ? 'primary' : 'default'}
                variant={topMode === 'artists' ? 'filled' : 'outlined'}
                onClick={() => setTopMode('artists')}
                sx={{ fontWeight: 700 }}
              />
              <Chip
                icon={<MusicIcon sx={{ fontSize: 16 }} />}
                label="Top Tracks"
                clickable
                color={topMode === 'tracks' ? 'primary' : 'default'}
                variant={topMode === 'tracks' ? 'filled' : 'outlined'}
                onClick={() => setTopMode('tracks')}
                sx={{ fontWeight: 700 }}
              />
              <Chip
                icon={<BarIcon sx={{ fontSize: 16 }} />}
                label="Activity Patterns"
                clickable
                color={topMode === 'heatmap' ? 'primary' : 'default'}
                variant={topMode === 'heatmap' ? 'filled' : 'outlined'}
                onClick={() => setTopMode('heatmap')}
                sx={{ fontWeight: 700 }}
              />
            </Stack>
          </Stack>

          {topMode === 'artists' && (
            <List disablePadding>
              {navidrome_history.top_artists.length === 0 ? (
                <Typography variant="body2" color="text.secondary">No listening history found yet.</Typography>
              ) : (
                navidrome_history.top_artists.map((item, idx) => (
                  <React.Fragment key={item.artist}>
                    <ListItem sx={{ py: 1, px: 0 }}>
                      <ListItemText
                        primary={`${idx + 1}. ${item.artist}`}
                        primaryTypographyProps={{ fontWeight: 700, fontSize: '0.9rem' }}
                      />
                      <Typography variant="subtitle2" color="primary.main" sx={{ fontWeight: 800 }}>
                        {item.count} plays
                      </Typography>
                    </ListItem>
                    {idx < navidrome_history.top_artists.length - 1 && <Divider />}
                  </React.Fragment>
                ))
              )}
            </List>
          )}

          {topMode === 'tracks' && (
            <List disablePadding>
              {navidrome_history.top_tracks.length === 0 ? (
                <Typography variant="body2" color="text.secondary">No listening history found yet.</Typography>
              ) : (
                navidrome_history.top_tracks.map((item, idx) => (
                  <React.Fragment key={item.track}>
                    <ListItem sx={{ py: 1, px: 0 }}>
                      <ListItemText
                        primary={`${idx + 1}. ${item.track}`}
                        primaryTypographyProps={{ fontWeight: 700, fontSize: '0.9rem' }}
                      />
                      <Typography variant="subtitle2" color="secondary.main" sx={{ fontWeight: 800 }}>
                        {item.count} plays
                      </Typography>
                    </ListItem>
                    {idx < navidrome_history.top_tracks.length - 1 && <Divider />}
                  </React.Fragment>
                ))
              )}
            </List>
          )}

          {topMode === 'heatmap' && (
            <Grid container spacing={3}>
              <Grid item xs={12} md={8}>
                <Typography variant="subtitle2" fontWeight={700} mb={1.5}>Hourly Activity</Typography>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', gap: 0.8, overflowX: 'auto', py: 1 }}>
                  {hours.map(h => {
                    const val = navidrome_history.heatmap[String(h)] || 0;
                    const intensity = val / maxHourValue;
                    const opacity = 0.15 + intensity * 0.85;
                    return (
                      <Box key={h} sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 0.8, flex: 1, minWidth: 16 }}>
                        <Box
                          sx={{
                            width: '100%',
                            height: 90,
                            borderRadius: 1.5,
                            bgcolor: 'primary.main',
                            opacity: opacity,
                          }}
                          title={`${val} plays at ${h}:00`}
                        />
                        <Typography variant="caption" sx={{ fontWeight: 700, fontSize: '0.7rem' }}>
                          {String(h).padStart(2, '0')}
                        </Typography>
                      </Box>
                    );
                  })}
                </Box>
              </Grid>

              <Grid item xs={12} md={4}>
                <Typography variant="subtitle2" fontWeight={700} mb={1.5}>Weekly Breakdown</Typography>
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                  {weekdays.map((day, idx) => {
                    const val = navidrome_history.weekday_heatmap[String(idx)] || 0;
                    const percent = (val / maxDayValue) * 100;
                    return (
                      <Box key={day} sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                        <Typography variant="caption" sx={{ fontWeight: 700, width: 32 }}>{day}</Typography>
                        <Box sx={{ flex: 1, height: 8, bgcolor: isDark ? 'grey.800' : 'grey.200', borderRadius: 1, overflow: 'hidden' }}>
                          <Box sx={{ width: `${percent}%`, height: '100%', bgcolor: 'secondary.main', borderRadius: 1 }} />
                        </Box>
                        <Typography variant="caption" sx={{ fontWeight: 800, width: 24, textAlign: 'right' }}>{val}</Typography>
                      </Box>
                    );
                  })}
                </Box>
              </Grid>
            </Grid>
          )}
        </CardContent>
      </Card>

      {/* ListenBrainz Cloud Stats Card with Chip Mode Selector */}
      {listenbrainz && (listenbrainz.artists || listenbrainz.releases) && (
        <Card sx={{ borderRadius: 4, background: cardBg, border: '1px solid', borderColor: 'divider', boxShadow: 'none' }}>
          <CardContent sx={{ p: { xs: 2.5, sm: 3 } }}>
            <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ xs: 'flex-start', sm: 'center' }} mb={2.5} gap={1.5}>
              <Typography variant="h6" sx={{ fontWeight: 800 }}>ListenBrainz Cloud Stats</Typography>
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                <Chip
                  icon={<ArtistIcon sx={{ fontSize: 16 }} />}
                  label="Artist Mode"
                  clickable
                  color={lbMode === 'artists' ? 'primary' : 'default'}
                  variant={lbMode === 'artists' ? 'filled' : 'outlined'}
                  onClick={() => setLbMode('artists')}
                  sx={{ fontWeight: 700 }}
                />
                <Chip
                  icon={<AlbumIcon sx={{ fontSize: 16 }} />}
                  label="Release Mode"
                  clickable
                  color={lbMode === 'releases' ? 'primary' : 'default'}
                  variant={lbMode === 'releases' ? 'filled' : 'outlined'}
                  onClick={() => setLbMode('releases')}
                  sx={{ fontWeight: 700 }}
                />
              </Stack>
            </Stack>

            {lbMode === 'artists' ? (
              <List disablePadding>
                {listenbrainz.artists?.map((item, idx) => (
                  <React.Fragment key={item.artist_name}>
                    <ListItem sx={{ py: 1, px: 0 }}>
                      <ListItemText primary={`${idx + 1}. ${item.artist_name}`} primaryTypographyProps={{ fontWeight: 700, fontSize: '0.9rem' }} />
                      <Chip label={`${item.listen_count} listens`} size="small" color="primary" variant="outlined" sx={{ fontWeight: 700 }} />
                    </ListItem>
                    {idx < (listenbrainz.artists?.length || 0) - 1 && <Divider />}
                  </React.Fragment>
                ))}
              </List>
            ) : (
              <List disablePadding>
                {listenbrainz.releases?.map((item, idx) => (
                  <React.Fragment key={item.release_name}>
                    <ListItem sx={{ py: 1, px: 0 }}>
                      <ListItemText
                        primary={`${idx + 1}. ${item.release_name}`}
                        secondary={item.artist_name}
                        primaryTypographyProps={{ fontWeight: 700, fontSize: '0.9rem' }}
                      />
                      <Chip label={`${item.listen_count} listens`} size="small" color="secondary" variant="outlined" sx={{ fontWeight: 700 }} />
                    </ListItem>
                    {idx < (listenbrainz.releases?.length || 0) - 1 && <Divider />}
                  </React.Fragment>
                ))}
              </List>
            )}
          </CardContent>
        </Card>
      )}
    </Box>
  );
};

export default StatsDashboard;
