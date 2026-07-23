import React, { useState, useEffect } from 'react';
import { 
  ThemeProvider, CssBaseline, Box, Drawer, AppBar, Toolbar, 
  List, ListItem, ListItemButton, ListItemIcon, ListItemText, 
  Typography, IconButton, Divider, Avatar, Tooltip, Chip, Slider
} from '@mui/material';
import { 
  Menu as MenuIcon, 
  Brightness4 as DarkModeIcon, 
  Brightness7 as LightModeIcon,
  Dashboard as DashboardIcon,
  Settings as SettingsIcon,
  History as HistoryIcon,
  Terminal as LogsIcon,
  MusicNote as MusicIcon,
  QueueMusic as PlaylistIcon,
  Search as SearchIcon,
  Favorite as FavoriteIcon,
  Person as ArtistsIcon,
  HealthAndSafety as HealthIcon,
  LibraryMusic as LibraryIcon,
  Logout as LogoutIcon,
  ManageAccounts as UserSettingsIcon,
  PendingActions as TasksIcon,
  PhotoLibrary as AlbumArtIcon,
  FileCopy as DuplicatesIcon,
  VolumeUp as VolumeIcon,
  PlayArrow as PlayIcon,
  Pause as PauseIcon,
  Close as CloseIcon,
  Fingerprint as FingerprintIcon,
  Album as AlbumIcon
} from '@mui/icons-material';
import getTheme from './theme';
import Dashboard from './components/Dashboard';
import Configuration from './components/Configuration';
import RunHistory from './components/RunHistory';
import LiveLogs from './components/LiveLogs';
import Explore from './components/Explore';
import SearchMusic from './components/SearchMusic';
import MyFeedback from './components/MyFeedback';
import ListenBrainz from './components/ListenBrainz';
import MyArtists from './components/MyArtists';
import ServerHealth from './components/ServerHealth';
import LibraryManager from './components/LibraryManager';
import LyricsManager from './components/LyricsManager';
import AlbumArtManager from './components/AlbumArtManager';
import DuplicatesManager from './components/DuplicatesManager';
import Login from './components/Login';
import UserSettings from './components/UserSettings';
import Setup from './components/Setup';
import RunningTasks from './components/RunningTasks';
import AcoustIDManager from './components/AcoustIDManager';
import NamingConvention from './components/NamingConvention';
import FeatFixer from './components/FeatFixer';
import RetagManager from './components/RetagManager';
import ArtistAliases from './components/ArtistAliases';
import MusicBrainzInspector from './components/MusicBrainzInspector';
import { AuthProvider, useAuth } from './context/AuthContext';

const DRAWER_WIDTH = 260;

type TabId = 'dashboard' | 'explore' | 'search' | 'feedback' | 'listenbrainz' | 'my-artists' | 'server-health' | 'acoustid' | 'library-manager' | 'lyrics' | 'album-art' | 'duplicates' | 'naming' | 'feat-fixer' | 'retag' | 'aliases' | 'musicbrainz-inspector' | 'tasks' | 'config' | 'history' | 'logs' | 'user-settings';

const VALID_TABS: TabId[] = ['dashboard', 'explore', 'search', 'feedback', 'listenbrainz', 'my-artists', 'server-health', 'acoustid', 'library-manager', 'lyrics', 'album-art', 'duplicates', 'naming', 'feat-fixer', 'retag', 'aliases', 'musicbrainz-inspector', 'tasks', 'config', 'history', 'logs', 'user-settings'];

const fmtTime = (secs: number) => {
  if (!secs || isNaN(secs)) return '0:00';
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s < 10 ? '0' : ''}${s}`;
};

// ── Global Audio Player ──────────────────────────────────────────────────────
const GlobalPlayer: React.FC<{
  track: { filepath: string; title: string; artist: string } | null;
  onClose: () => void;
}> = ({ track, onClose }) => {
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [volume, setVolume] = useState(0.8);
  const audioRef = React.useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    if (track && audioRef.current) {
      audioRef.current.src = `/api/library/tracks/stream?filepath=${encodeURIComponent(track.filepath)}`;
      audioRef.current.play()
        .then(() => setPlaying(true))
        .catch(() => setPlaying(false));
    }
  }, [track]);

  const handlePlayPause = () => {
    if (!audioRef.current) return;
    if (playing) {
      audioRef.current.pause();
      setPlaying(false);
    } else {
      audioRef.current.play()
        .then(() => setPlaying(true));
    }
  };

  const handleTimeUpdate = () => {
    if (audioRef.current) {
      setCurrentTime(audioRef.current.currentTime);
    }
  };

  const handleLoadedMetadata = () => {
    if (audioRef.current) {
      setDuration(audioRef.current.duration);
    }
  };

  const handleSeek = (e: any, newValue: number | number[]) => {
    const val = newValue as number;
    if (audioRef.current) {
      audioRef.current.currentTime = val;
      setCurrentTime(val);
    }
  };

  const handleVolumeChange = (e: any, newValue: number | number[]) => {
    const val = newValue as number;
    setVolume(val);
    if (audioRef.current) {
      audioRef.current.volume = val;
    }
  };

  if (!track) return null;

  return (
    <Box
      sx={{
        position: 'fixed',
        bottom: 0,
        left: 0,
        right: 0,
        height: 72,
        bgcolor: 'background.paper',
        borderTop: '1px solid',
        borderColor: 'divider',
        display: 'flex',
        alignItems: 'center',
        px: { xs: 1.5, sm: 3 },
        zIndex: 1300,
        justifyContent: 'space-between',
        boxShadow: '0 -4px 20px rgba(0,0,0,0.1)'
      }}
    >
      <audio
        ref={audioRef}
        onTimeUpdate={handleTimeUpdate}
        onLoadedMetadata={handleLoadedMetadata}
        onEnded={() => setPlaying(false)}
      />
      {/* Title & Artist */}
      <Box sx={{ minWidth: { xs: 80, sm: 200 }, maxWidth: { xs: 90, sm: 300 }, display: 'flex', flexDirection: 'column' }}>
        <Typography variant="body2" sx={{ fontWeight: 700 }} noWrap>{track.title}</Typography>
        <Typography variant="caption" color="text.secondary" noWrap>{track.artist}</Typography>
      </Box>

      {/* Controls */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: { xs: 1, sm: 2 }, flex: 1, justifyContent: 'center', maxWidth: 600 }}>
        <IconButton onClick={handlePlayPause} color="primary" size="small">
          {playing ? <PauseIcon /> : <PlayIcon />}
        </IconButton>
        <Typography variant="caption" sx={{ width: 35, textAlign: 'right', display: { xs: 'none', sm: 'block' } }}>
          {fmtTime(currentTime)}
        </Typography>
        <Slider
          size="small"
          value={currentTime}
          max={duration || 100}
          onChange={handleSeek}
          sx={{ flex: 1, mx: { xs: 1, sm: 0 } }}
        />
        <Typography variant="caption" sx={{ width: 35, display: { xs: 'none', sm: 'block' } }}>
          {fmtTime(duration)}
        </Typography>
      </Box>

      {/* Volume & Close */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: { xs: 0.5, sm: 2 }, minWidth: { xs: 'auto', sm: 150 }, justifyContent: 'flex-end' }}>
        <VolumeIcon sx={{ color: 'text.secondary', display: { xs: 'none', md: 'block' } }} />
        <Slider
          size="small"
          value={volume}
          max={1}
          step={0.05}
          onChange={handleVolumeChange}
          sx={{ width: 80, display: { xs: 'none', md: 'block' } }}
        />
        <IconButton size="small" onClick={onClose}>
          <CloseIcon />
        </IconButton>
      </Box>
    </Box>
  );
};

interface AppInnerProps {
  mode: 'light' | 'dark';
  toggleMode: () => void;
}

const AppInner: React.FC<AppInnerProps> = ({ mode, toggleMode }) => {
  const { user, loading, logout, isConfigured } = useAuth();

  const [activeTab, setActiveTab] = useState<TabId>(() => {
    const path = window.location.pathname.replace(/^\//, '') as TabId;
    if (VALID_TABS.includes(path)) return path;
    const saved = sessionStorage.getItem('vd-active-tab') as TabId;
    return (saved && VALID_TABS.includes(saved)) ? saved : 'dashboard';
  });

  const [mobileOpen, setMobileOpen] = useState(false);
  const [currentTrack, setCurrentTrack] = useState<{ filepath: string; title: string; artist: string } | null>(null);

  useEffect(() => {
    const handlePopState = () => {
      const rawPath = window.location.pathname.replace(/^\//, '');
      if (rawPath === '' || rawPath === '/') {
        setActiveTab('dashboard');
      } else {
        const path = rawPath as TabId;
        if (VALID_TABS.includes(path)) {
          setActiveTab(path);
        }
      }
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  useEffect(() => {
    const handleLogoutEvent = () => {
      logout();
    };
    window.addEventListener('auth:logout', handleLogoutEvent);
    return () => window.removeEventListener('auth:logout', handleLogoutEvent);
  }, [logout]);

  useEffect(() => {
    localStorage.setItem('verydisco-theme-mode', mode);
  }, [mode]);

  useEffect(() => {
    const handlePlay = (e: any) => {
      const { filepath, title, artist } = e.detail;
      setCurrentTrack({ filepath, title, artist });
    };
    window.addEventListener("verydisco-play", handlePlay);
    return () => window.removeEventListener("verydisco-play", handlePlay);
  }, []);

  const navigateTo = (tab: string) => {
    setActiveTab(tab as TabId);
    sessionStorage.setItem('vd-active-tab', tab);
    window.history.pushState(null, '', `/${tab}`);
  };

  // Listen for custom navigation events
  useEffect(() => {
    const handleNavigate = (e: any) => {
      if (e.detail) {
        navigateTo(e.detail);
      }
    };
    window.addEventListener("verydisco-navigate", handleNavigate);
    return () => window.removeEventListener("verydisco-navigate", handleNavigate);
  }, []);

  const activeTheme = getTheme(mode);

  if (loading) {
    return (
      <ThemeProvider theme={activeTheme}>
        <CssBaseline />
        <Box sx={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <MusicIcon sx={{ fontSize: 48, opacity: 0.3, animation: 'pulse 1.5s ease-in-out infinite' }} />
        </Box>
      </ThemeProvider>
    );
  }

  if (!isConfigured) {
    return (
      <ThemeProvider theme={activeTheme}>
        <CssBaseline />
        <Setup />
      </ThemeProvider>
    );
  }

  if (!user) {
    return (
      <ThemeProvider theme={activeTheme}>
        <CssBaseline />
        <Login />
      </ThemeProvider>
    );
  }

  const navigationItems = [
    { id: 'dashboard', text: 'Dashboard', icon: <DashboardIcon /> },
    { id: 'explore', text: 'Explore', icon: <PlaylistIcon /> },
    { id: 'search', text: 'Search Music', icon: <SearchIcon /> },
    { id: 'my-artists', text: 'My Artists', icon: <ArtistsIcon /> },
    { id: 'feedback', text: 'My Feedback', icon: <FavoriteIcon /> },
    { id: 'listenbrainz', text: 'ListenBrainz', icon: <MusicIcon /> },
    { id: 'divider-1', text: '', icon: null },
    { id: 'library-manager', text: 'Library Manager', icon: <LibraryIcon /> },
    { id: 'naming', text: 'Naming Conventions', icon: <SettingsIcon /> },
    { id: 'feat-fixer', text: 'Feature Artist Fixer', icon: <ArtistsIcon /> },
    { id: 'retag', text: 'MusicBrainz Retag', icon: <LibraryIcon /> },
    { id: 'musicbrainz-inspector', text: 'MusicBrainz Inspector', icon: <AlbumIcon /> },
    { id: 'lyrics', text: 'Lyrics Manager', icon: <MusicIcon /> },
    { id: 'album-art', text: 'Album Art Finder', icon: <AlbumArtIcon /> },
    { id: 'duplicates', text: 'Duplicate Cleaner', icon: <DuplicatesIcon /> },
    { id: 'divider-2', text: '', icon: null },
    ...(user?.isAdmin ? [
      { id: 'server-health', text: 'Server Health', icon: <HealthIcon /> },
      { id: 'acoustid', text: 'AcoustID Verification', icon: <FingerprintIcon /> },
      { id: 'aliases', text: 'Artist Aliases', icon: <ArtistsIcon /> },
      { id: 'config', text: 'Configuration', icon: <SettingsIcon /> },
      { id: 'divider-3', text: '', icon: null },
    ] : []),
    { id: 'tasks', text: 'Running Tasks', icon: <TasksIcon /> },
    { id: 'history', text: 'Sync History', icon: <HistoryIcon /> },
    { id: 'logs', text: 'Live Logs', icon: <LogsIcon /> },
    { id: 'divider-4', text: '', icon: null },
    { id: 'user-settings', text: 'My Settings', icon: <UserSettingsIcon /> },
  ];

  const drawerContent = (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Logo */}
      <Box sx={{ 
        p: 3, 
        display: 'flex', 
        alignItems: 'center', 
        gap: 1.5,
        background: mode === 'dark' 
          ? 'linear-gradient(135deg, #1c1b22 0%, #121118 100%)' 
          : 'linear-gradient(135deg, #ffffff 0%, #f1f3f9 100%)'
      }}>
        <MusicIcon color="primary" sx={{ fontSize: 32 }} />
        <Box>
          <Typography variant="h6" sx={{ fontWeight: 800, lineHeight: 1.2 }}>
            VeryDisco
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600, letterSpacing: 0.5 }}>
            MATERIAL DESIGN
          </Typography>
        </Box>
      </Box>
      <Divider />

      {/* Navigation */}
      <List sx={{ px: 2, py: 2, flex: 1, display: 'flex', flexDirection: 'column', gap: 0.5, overflowY: 'auto' }}>
        {navigationItems.map((item) => {
          if (item.id.startsWith('divider')) {
            return <Divider key={item.id} sx={{ my: 1, opacity: 0.4 }} />;
          }
          const isSelected = activeTab === item.id;
          return (
            <ListItem key={item.id} disablePadding>
              <ListItemButton
                component="a"
                href={`/${item.id}`}
                selected={isSelected}
                onClick={(e) => {
                  if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
                    e.preventDefault();
                    navigateTo(item.id);
                    setMobileOpen(false);
                  }
                }}
                sx={{
                  borderRadius: 3,
                  py: 1.5,
                  px: 2,
                  '&.Mui-selected': {
                    bgcolor: mode === 'dark' ? 'rgba(179, 136, 255, 0.12)' : 'rgba(98, 0, 234, 0.08)',
                    color: 'primary.main',
                    '& .MuiListItemIcon-root': { color: 'primary.main' },
                    '&:hover': {
                      bgcolor: mode === 'dark' ? 'rgba(179, 136, 255, 0.18)' : 'rgba(98, 0, 234, 0.12)',
                    }
                  },
                }}
              >
                <ListItemIcon sx={{ minWidth: 40, color: isSelected ? 'primary.main' : 'text.secondary' }}>
                  {item.icon}
                </ListItemIcon>
                <ListItemText 
                  primary={item.text} 
                  primaryTypographyProps={{ fontWeight: isSelected ? 700 : 500, fontSize: '0.95rem' }} 
                />
              </ListItemButton>
            </ListItem>
          );
        })}
      </List>

      {/* User footer */}
      <Divider />
      <Box sx={{ p: 2, display: 'flex', alignItems: 'center', gap: 1.5, mb: currentTrack ? '72px' : 0 }}>
        <Avatar sx={{ width: 36, height: 36, bgcolor: 'primary.main', fontSize: '0.9rem', fontWeight: 700 }}>
          {(user.displayName || user.username).charAt(0).toUpperCase()}
        </Avatar>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="body2" sx={{ fontWeight: 600, lineHeight: 1.2 }} noWrap>
            {user.displayName || user.username}
          </Typography>
          {user.isAdmin && (
            <Typography variant="caption" color="primary.main" sx={{ fontWeight: 600 }}>
              Admin
            </Typography>
          )}
        </Box>
        <Tooltip title="Sign out">
          <IconButton size="small" onClick={() => logout()} color="default">
            <LogoutIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>
    </Box>
  );

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh', maxWidth: '100vw', overflowX: 'hidden' }}>
      {/* Header App Bar */}
      <AppBar
        position="fixed"
        sx={{
          width: { md: `calc(100% - ${DRAWER_WIDTH}px)` },
          ml: { md: `${DRAWER_WIDTH}px` },
          bgcolor: 'background.paper',
          color: 'text.primary',
          boxShadow: 'none',
          borderBottom: '1px solid',
          borderColor: 'divider',
          backdropFilter: 'blur(20px)',
          backgroundImage: 'none',
        }}
      >
        <Toolbar sx={{ justifyContent: 'space-between', px: { xs: 2, md: 4 } }}>
          <Box display="flex" alignItems="center">
            <IconButton
              color="inherit"
              aria-label="open drawer"
              edge="start"
              onClick={() => setMobileOpen(!mobileOpen)}
              sx={{ mr: 2, display: { md: 'none' } }}
            >
              <MenuIcon />
            </IconButton>
            <Typography variant="h6" noWrap component="div" sx={{ fontWeight: 700 }}>
              {navigationItems.find(n => n.id === activeTab)?.text}
            </Typography>
          </Box>

          <Box display="flex" alignItems="center" gap={1}>
            <Chip
              avatar={<Avatar sx={{ bgcolor: 'primary.main', width: 24, height: 24, fontSize: '0.75rem', fontWeight: 700 }}>{(user.displayName || user.username).charAt(0).toUpperCase()}</Avatar>}
              label={user.displayName || user.username}
              size="small"
              sx={{ fontWeight: 600, display: { xs: 'none', sm: 'flex' } }}
            />
            <IconButton onClick={toggleMode} color="inherit">
              {mode === 'dark' ? <LightModeIcon /> : <DarkModeIcon />}
            </IconButton>
          </Box>
        </Toolbar>
      </AppBar>

      {/* Side Drawers */}
      <Box component="nav" sx={{ width: { md: DRAWER_WIDTH }, flexShrink: { md: 0 } }} aria-label="Main navigation">
        <Drawer
          variant="temporary"
          open={mobileOpen}
          onClose={() => setMobileOpen(false)}
          ModalProps={{ keepMounted: true }}
          sx={{
            display: { xs: 'block', md: 'none' },
            '& .MuiDrawer-paper': { boxSizing: 'border-box', width: DRAWER_WIDTH, borderRight: '1px solid', borderColor: 'divider' },
          }}
        >
          {drawerContent}
        </Drawer>

        <Drawer
          variant="permanent"
          sx={{
            display: { xs: 'none', md: 'block' },
            '& .MuiDrawer-paper': { boxSizing: 'border-box', width: DRAWER_WIDTH, borderRight: '1px solid', borderColor: 'divider' },
          }}
          open
        >
          {drawerContent}
        </Drawer>
      </Box>

      {/* Main Content */}
      <Box
        component="main"
        sx={{
          flexGrow: 1,
          p: { xs: 1.5, sm: 3, md: 4 },
          width: { md: `calc(100% - ${DRAWER_WIDTH}px)` },
          minWidth: 0,
          mt: '64px',
          mb: currentTrack ? '80px' : 0,
          bgcolor: 'background.default',
        }}
      >
        {activeTab === 'dashboard' && <Dashboard onNavigateToConfig={() => navigateTo('config')} />}
        {activeTab === 'explore' && <Explore />}
        {activeTab === 'search' && <SearchMusic />}
        {activeTab === 'my-artists' && <MyArtists />}
        {activeTab === 'feedback' && <MyFeedback />}
        {activeTab === 'listenbrainz' && <ListenBrainz />}
        {activeTab === 'server-health' && user?.isAdmin && <ServerHealth />}
        {activeTab === 'acoustid' && <AcoustIDManager />}
        {activeTab === 'library-manager' && <LibraryManager />}
        {activeTab === 'naming' && <NamingConvention />}
        {activeTab === 'feat-fixer' && <FeatFixer />}
        {activeTab === 'retag' && <RetagManager />}
        {activeTab === 'aliases' && <ArtistAliases />}
        {activeTab === 'musicbrainz-inspector' && <MusicBrainzInspector />}
        {activeTab === 'lyrics' && <LyricsManager />}
        {activeTab === 'album-art' && <AlbumArtManager />}
        {activeTab === 'duplicates' && <DuplicatesManager />}
        {activeTab === 'config' && <Configuration />}
        {activeTab === 'tasks' && <RunningTasks />}
        {activeTab === 'history' && <RunHistory />}
        {activeTab === 'logs' && <LiveLogs />}
        {activeTab === 'user-settings' && <UserSettings />}
      </Box>

      {/* Global Audio Player Bar */}
      <GlobalPlayer track={currentTrack} onClose={() => setCurrentTrack(null)} />
    </Box>
  );
};

import { NotificationProvider } from './context/NotificationContext';

export const App: React.FC = () => {
  const [mode, setMode] = useState<'light' | 'dark'>(() => {
    const saved = localStorage.getItem('verydisco-theme-mode');
    return (saved === 'light' || saved === 'dark') ? saved : 'dark';
  });

  const toggleMode = () => {
    setMode(prev => prev === 'light' ? 'dark' : 'light');
  };

  const activeTheme = getTheme(mode);

  return (
    <ThemeProvider theme={activeTheme}>
      <CssBaseline />
      <AuthProvider>
        <NotificationProvider>
          <AppInner mode={mode} toggleMode={toggleMode} />
        </NotificationProvider>
      </AuthProvider>
    </ThemeProvider>
  );
};

export default App;
