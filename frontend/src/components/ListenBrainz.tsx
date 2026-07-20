import React, { useEffect, useState } from 'react';
import { 
  Box, Typography, Card, CardContent, Grid, 
  Switch, CircularProgress, Alert, useTheme, CardActions
} from '@mui/material';
import { MusicNote as MusicIcon, Explore as ExploreIcon, ViewDay as DayIcon } from '@mui/icons-material';
import { apiService, ConfigData } from '../api';

const PLAYLISTS = [
  {
    id: 'daily-jams',
    title: 'Daily Jams',
    description: 'Fresh tracks recommended for you every day based on your recent listening history.',
    icon: <DayIcon sx={{ fontSize: 40 }} />
  },
  {
    id: 'weekly-exploration',
    title: 'Weekly Exploration',
    description: 'Discover new artists and genres based on your taste profile. Updated every Monday.',
    icon: <ExploreIcon sx={{ fontSize: 40 }} />
  },
  {
    id: 'weekly-jams',
    title: 'Weekly Jams',
    description: 'A mix of your favorite tracks and some new ones. Updated every Monday.',
    icon: <MusicIcon sx={{ fontSize: 40 }} />
  }
];

export const ListenBrainz: React.FC = () => {
  const theme = useTheme();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [activePlaylists, setActivePlaylists] = useState<string[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const fetchConfig = async () => {
    try {
      setLoading(true);
      const data = await apiService.getMyConfig();
      if (data.active_playlists) {
        setActivePlaylists(data.active_playlists);
      }
    } catch (e: any) {
      setErrorMsg("Failed to load configuration.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchConfig();
  }, []);

  const handleToggle = async (playlistId: string) => {
    const isCurrentlyActive = activePlaylists.includes(playlistId);
    const newPlaylists = isCurrentlyActive 
      ? activePlaylists.filter(id => id !== playlistId)
      : [...activePlaylists, playlistId];

    setActivePlaylists(newPlaylists);
    setSaving(true);
    setSuccessMsg(null);
    setErrorMsg(null);

    try {
      const currentConfig = await apiService.getMyConfig();
      const payload = {
        ...currentConfig,
        active_playlists: newPlaylists
      };
      await apiService.saveMyConfig(payload);
      setSuccessMsg("Preferences saved.");
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (e: any) {
      setErrorMsg(e.response?.data?.detail || "Failed to save configuration.");
      // Revert state
      setActivePlaylists(activePlaylists);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="50vh">
        <CircularProgress size={50} />
      </Box>
    );
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <Box>
        <Typography variant="h4" sx={{ fontWeight: 800 }}>ListenBrainz Playlists</Typography>
        <Typography variant="body2" color="text.secondary">
          Select which auto-generated ListenBrainz playlists you want to synchronize.
        </Typography>
      </Box>

      {errorMsg && <Alert severity="error" onClose={() => setErrorMsg(null)}>{errorMsg}</Alert>}
      {successMsg && <Alert severity="success" onClose={() => setSuccessMsg(null)}>{successMsg}</Alert>}

      <Grid container spacing={{ xs: 2, sm: 4 }}>
        {PLAYLISTS.map((playlist) => {
          const isActive = activePlaylists.includes(playlist.id);
          return (
            <Grid item xs={12} md={4} key={playlist.id}>
              <Card 
                sx={{ 
                  height: '100%', 
                  display: 'flex', 
                  flexDirection: 'column',
                  border: isActive ? `2px solid ${theme.palette.primary.main}` : '2px solid transparent',
                  transition: 'all 0.3s ease',
                  opacity: isActive ? 1 : 0.7,
                  boxShadow: isActive ? `0 8px 24px ${theme.palette.primary.main}40` : theme.shadows[1]
                }}
              >
                <CardContent sx={{ flexGrow: 1, display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                    <Box sx={{ color: isActive ? 'primary.main' : 'text.secondary' }}>
                      {playlist.icon}
                    </Box>
                    <Typography variant="h6" sx={{ fontWeight: 700 }}>
                      {playlist.title}
                    </Typography>
                  </Box>
                  <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
                    {playlist.description}
                  </Typography>
                </CardContent>
                <CardActions sx={{ justifyContent: 'flex-end', p: 2, pt: 0 }}>
                  <Box display="flex" alignItems="center" gap={1}>
                    {saving && <CircularProgress size={16} />}
                    <Typography variant="caption" sx={{ fontWeight: 600, color: isActive ? 'primary.main' : 'text.secondary' }}>
                      {isActive ? 'ACTIVE' : 'INACTIVE'}
                    </Typography>
                    <Switch
                      checked={isActive}
                      onChange={() => handleToggle(playlist.id)}
                      disabled={saving}
                      color="primary"
                    />
                  </Box>
                </CardActions>
              </Card>
            </Grid>
          );
        })}
      </Grid>
    </Box>
  );
};

export default ListenBrainz;
