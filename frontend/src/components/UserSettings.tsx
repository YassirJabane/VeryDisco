import React, { useEffect, useState } from 'react';
import {
  Box, Card, CardContent, Typography, TextField, Button, Chip,
  CircularProgress, Alert, Snackbar, Divider, useTheme,
  Switch, FormControlLabel,
} from '@mui/material';
import {
  Save as SaveIcon,
  Person as PersonIcon,
  MusicNote as MusicIcon,
  CheckCircle as CheckCircleIcon,
  Error as ErrorIcon,
  AdminPanelSettings as AdminIcon,
  WifiTethering as TestIcon,
} from '@mui/icons-material';
import { apiService } from '../api';
import { useAuth } from '../context/AuthContext';


const UserSettings: React.FC = () => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const { user } = useAuth();

  // LB config state
  const [lbUsername, setLbUsername] = useState('');
  const [lbToken, setLbToken] = useState('');
  const [activePlaylists, setActivePlaylists] = useState<string[]>([]);
  const [musicDir, setMusicDir] = useState('');
  const [playlistDir, setPlaylistDir] = useState('');
  const [renamingPattern, setRenamingPattern] = useState('');
  const [enabledFeatures, setEnabledFeatures] = useState<{ [key: string]: boolean }>({
    starred_sync: true,
    listenbrainz_sync: true,
    discovery: true,
    album_downloads: true,
  });
  const [configLoading, setConfigLoading] = useState(true);
  const [configSaving, setConfigSaving] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);

  // Connection testing state
  const [lbTest, setLbTest] = useState<{ status: 'idle' | 'testing' | 'ok' | 'error'; message: string }>({
    status: 'idle', message: '',
  });

  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: 'success' | 'error' }>({
    open: false, message: '', severity: 'success',
  });

  const showSnack = (message: string, severity: 'success' | 'error' = 'success') =>
    setSnackbar({ open: true, message, severity });

  const handleTestLB = async () => {
    setLbTest({ status: 'testing', message: '' });
    try {
      const res = await apiService.testListenBrainz();
      setLbTest({ status: 'ok', message: res.message });
    } catch (e: any) {
      setLbTest({ status: 'error', message: e.response?.data?.detail || 'Connection failed' });
    }
  };

  // Load my config
  useEffect(() => {
    apiService.getMyConfig()
      .then(cfg => {
        setLbUsername(cfg.lb_username || '');
        setLbToken(cfg.lb_token || '');
        setActivePlaylists(cfg.active_playlists || []);
        setMusicDir(cfg.music_dir || '');
        setPlaylistDir(cfg.playlist_dir || '');
        setRenamingPattern(cfg.renaming_pattern || '{Artist}/{Year} - {Album}/{Track:2} - {Title}');
        if (cfg.enabled_features) {
          setEnabledFeatures(cfg.enabled_features);
        }
      })
      .catch(() => setConfigError('Failed to load your configuration.'))
      .finally(() => setConfigLoading(false));
  }, []);

  const handleSaveConfig = async () => {
    setConfigSaving(true);
    try {
      await apiService.saveMyConfig({
        lb_username: lbUsername,
        lb_token: lbToken,
        active_playlists: activePlaylists,
        music_dir: musicDir,
        playlist_dir: playlistDir,
        renaming_pattern: renamingPattern,
        enabled_features: enabledFeatures,
      });
      if (user) {
        user.musicDir = musicDir;
      }
      showSnack('Your configuration saved successfully!');
    } catch (err: any) {
      showSnack(err?.response?.data?.detail || 'Failed to save config.', 'error');
    } finally {
      setConfigSaving(false);
    }
  };

  const togglePlaylist = (id: string) => {
    setActivePlaylists(prev =>
      prev.includes(id) ? prev.filter(p => p !== id) : [...prev, id]
    );
  };

  const cardBg = isDark ? '#1c1b22' : '#ffffff';

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Header */}
      <Box>
        <Typography variant="h5" sx={{ fontWeight: 800 }}>My Settings</Typography>
        <Typography variant="body2" color="text.secondary">
          Manage your ListenBrainz integration and personal preferences
        </Typography>
      </Box>

      {/* Profile card */}
      <Card sx={{ borderRadius: 4, background: cardBg, boxShadow: '0 4px 20px rgba(0,0,0,0.05)' }}>
        <CardContent sx={{ p: 4 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 0 }}>
            <Box
              sx={{
                width: 56, height: 56, borderRadius: 3,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: 'linear-gradient(135deg, #6e46ff, #b388ff)',
              }}
            >
              <PersonIcon sx={{ color: '#fff', fontSize: 28 }} />
            </Box>
            <Box>
              <Typography variant="h6" sx={{ fontWeight: 700 }}>
                {user?.displayName || user?.username}
              </Typography>
              <Box sx={{ display: 'flex', gap: 1, mt: 0.5 }}>
                <Chip label={`@${user?.username}`} size="small" sx={{ fontWeight: 600 }} />
                {user?.isAdmin && (
                  <Chip label="Admin" size="small" color="primary" icon={<AdminIcon />} sx={{ fontWeight: 600 }} />
                )}
              </Box>
            </Box>
          </Box>
        </CardContent>
      </Card>

      {/* Configuration settings card */}
      <Card sx={{ borderRadius: 4, background: cardBg, boxShadow: '0 4px 20px rgba(0,0,0,0.05)' }}>
        <CardContent sx={{ p: 4 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 0.5 }}>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>
              My Configuration
            </Typography>
            <Button
              size="small"
              variant="outlined"
              startIcon={lbTest.status === 'testing' ? <CircularProgress size={14} /> : <TestIcon />}
              onClick={handleTestLB}
              disabled={lbTest.status === 'testing' || !lbUsername}
              sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600 }}
            >
              Test Connection
            </Button>
          </Box>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            Configure your personal ListenBrainz integration and music library directory
          </Typography>

          {configLoading ? (
            <Box display="flex" justifyContent="center" p={4}><CircularProgress /></Box>
          ) : (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.5 }}>
              {configError && <Alert severity="error">{configError}</Alert>}
              
              {lbTest.status === 'ok' && (
                <Alert severity="success" onClose={() => setLbTest({ status: 'idle', message: '' })}>
                  {lbTest.message}
                </Alert>
              )}
              {lbTest.status === 'error' && (
                <Alert severity="error" onClose={() => setLbTest({ status: 'idle', message: '' })}>
                  {lbTest.message}
                </Alert>
              )}

              <TextField
                label="ListenBrainz Username"
                value={lbUsername}
                onChange={e => setLbUsername(e.target.value)}
                fullWidth
                size="medium"
                sx={{ '& .MuiOutlinedInput-root': { borderRadius: 2 } }}
              />

              <TextField
                label="User Token"
                type="password"
                value={lbToken}
                onChange={e => setLbToken(e.target.value)}
                fullWidth
                size="medium"
                helperText="Get your token from listenbrainz.org → Profile → API Token"
                sx={{ '& .MuiOutlinedInput-root': { borderRadius: 2 } }}
              />

              <TextField
                label="Personal Music Library Directory"
                value={musicDir}
                onChange={e => setMusicDir(e.target.value)}
                fullWidth
                size="medium"
                helperText="Where your personal album downloads are saved (e.g. /music)"
                sx={{ '& .MuiOutlinedInput-root': { borderRadius: 2 } }}
              />

              <TextField
                label="Personal Navidrome Playlists Directory"
                value={playlistDir}
                onChange={e => setPlaylistDir(e.target.value)}
                fullWidth
                size="medium"
                helperText="Where your Navidrome playlist files are written (e.g. /music/Playlists)"
                sx={{ '& .MuiOutlinedInput-root': { borderRadius: 2 } }}
              />

              <TextField
                label="Custom Renaming Pattern"
                value={renamingPattern}
                onChange={e => setRenamingPattern(e.target.value)}
                fullWidth
                size="medium"
                helperText="Define file structure rules using tags like {Artist}, {Year}, {Album}, {Track:2}, {Track}, {Title}, {Genre}"
                sx={{ '& .MuiOutlinedInput-root': { borderRadius: 2 } }}
              />
              <Box sx={{ mt: -1.5, mb: 1, pl: 1 }}>
                <Typography variant="caption" color="text.secondary">
                  Example Preview: <Chip size="small" label={renamingPattern
                    .replace("{Artist}", "Gorillaz")
                    .replace("{Year}", "2005")
                    .replace("{Album}", "Demon Days")
                    .replace("{Track:2}", "02")
                    .replace("{Track}", "2")
                    .replace("{Title}", "Feel Good Inc")
                    .replace("{Genre}", "Alternative") + ".mp3"
                  } sx={{ fontFamily: 'monospace', fontWeight: 600, fontSize: '0.75rem' }} />
                </Typography>
              </Box>

              <Divider sx={{ my: 1.5 }} />

              <Box>
                <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 0.5 }}>
                  Enabled Features
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                  Customize which background services and automated features run for your account
                </Typography>
                <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2.5 }}>
                  <FormControlLabel
                    control={
                      <Switch
                        checked={enabledFeatures.starred_sync ?? true}
                        onChange={e => setEnabledFeatures(prev => ({ ...prev, starred_sync: e.target.checked }))}
                      />
                    }
                    label={
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>⭐ Starred Tracks Sync</Typography>
                        <Typography variant="caption" color="text.secondary">Automatically sync starred tracks from Navidrome</Typography>
                      </Box>
                    }
                  />
                  <FormControlLabel
                    control={
                      <Switch
                        checked={enabledFeatures.listenbrainz_sync ?? true}
                        onChange={e => setEnabledFeatures(prev => ({ ...prev, listenbrainz_sync: e.target.checked }))}
                      />
                    }
                    label={
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>🎵 ListenBrainz Sync</Typography>
                        <Typography variant="caption" color="text.secondary">Pull Exploration / Jams playlists from ListenBrainz</Typography>
                      </Box>
                    }
                  />
                  <FormControlLabel
                    control={
                      <Switch
                        checked={enabledFeatures.discovery ?? true}
                        onChange={e => setEnabledFeatures(prev => ({ ...prev, discovery: e.target.checked }))}
                      />
                    }
                    label={
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>🔍 Discovery Flow</Typography>
                        <Typography variant="caption" color="text.secondary">Save new tracks into the Explore/discovery directory</Typography>
                      </Box>
                    }
                  />
                  <FormControlLabel
                    control={
                      <Switch
                        checked={enabledFeatures.album_downloads ?? true}
                        onChange={e => setEnabledFeatures(prev => ({ ...prev, album_downloads: e.target.checked }))}
                      />
                    }
                    label={
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>📥 Automatic Album Downloads</Typography>
                        <Typography variant="caption" color="text.secondary">Automatically download complete albums for starred tracks</Typography>
                      </Box>
                    }
                  />
                </Box>
              </Box>

              <Divider sx={{ my: 1.5 }} />

              <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
                <Button
                  variant="contained"
                  startIcon={configSaving ? <CircularProgress size={18} color="inherit" /> : <SaveIcon />}
                  onClick={handleSaveConfig}
                  disabled={configSaving}
                  sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600, px: 3 }}
                >
                  {configSaving ? 'Saving…' : 'Save Changes'}
                </Button>
              </Box>
            </Box>
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

export default UserSettings;
