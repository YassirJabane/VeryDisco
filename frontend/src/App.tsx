import React, { useState, useEffect } from 'react';
import { 
  ThemeProvider, CssBaseline, Box, Drawer, AppBar, Toolbar, 
  List, ListItem, ListItemButton, ListItemIcon, ListItemText, 
  Typography, IconButton, Divider, Avatar, Tooltip, Chip, Slider, Collapse, Button
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
  Album as AlbumIcon,
  AutoFixHigh as RebuildIcon,
  KeyboardArrowDown as ArrowDownIcon
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
import ArtistAliases from './components/ArtistAliases';
import MusicBrainzInspector from './components/MusicBrainzInspector';
import MusicRequests from './components/MusicRequests';
import MetadataRebuild from './components/MetadataRebuild';
import { AuthProvider, useAuth } from './context/AuthContext';

const DRAWER_WIDTH = 260;
const DRAWER_COLLAPSED_WIDTH = 78;

type TabId = 'dashboard' | 'explore' | 'search' | 'requests' | 'feedback' | 'listenbrainz' | 'my-artists' | 'server-health' | 'acoustid' | 'library-manager' | 'lyrics' | 'album-art' | 'duplicates' | 'naming' | 'feat-fixer' | 'metadata-rebuild' | 'aliases' | 'musicbrainz-inspector' | 'tasks' | 'config' | 'history' | 'logs' | 'user-settings';

const VALID_TABS: TabId[] = ['dashboard', 'explore', 'search', 'requests', 'feedback', 'listenbrainz', 'my-artists', 'server-health', 'acoustid', 'library-manager', 'lyrics', 'album-art', 'duplicates', 'naming', 'feat-fixer', 'metadata-rebuild', 'aliases', 'musicbrainz-inspector', 'tasks', 'config', 'history', 'logs', 'user-settings'];

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
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.src = '';
      }
    };
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
        minHeight: 82,
        bgcolor: 'rgba(21,19,29,0.92)',
        borderTop: '1px solid',
        borderColor: 'divider',
        display: 'flex',
        alignItems: 'center',
        px: { xs: 1.5, sm: 3 },
        zIndex: 1300,
        justifyContent: 'space-between',
        boxShadow: '0 -18px 50px rgba(0,0,0,0.28)',
        backdropFilter: 'blur(24px)',
      }}
    >
      <audio
        ref={audioRef}
        onTimeUpdate={handleTimeUpdate}
        onLoadedMetadata={handleLoadedMetadata}
        onEnded={() => setPlaying(false)}
      />
      {/* Title & Artist */}
      <Box sx={{ minWidth: { xs: 90, sm: 220 }, maxWidth: { xs: 120, sm: 300 }, display: 'flex', flexDirection: 'column' }}>
        <Typography variant="caption" color="primary.main" sx={{ fontWeight: 750, letterSpacing: '.08em', textTransform: 'uppercase' }}>Now playing</Typography>
        <Typography variant="body2" sx={{ fontWeight: 700 }} noWrap>{track.title}</Typography>
        <Typography variant="caption" color="text.secondary" noWrap>{track.artist}</Typography>
      </Box>

      {/* Controls */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: { xs: 1, sm: 2 }, flex: 1, justifyContent: 'center', maxWidth: 600 }}>
        <IconButton onClick={handlePlayPause} color="primary" size="medium" aria-label={playing ? 'Pause playback' : 'Play track'} sx={{ bgcolor: 'rgba(155,108,255,.14)', '&:hover': { bgcolor: 'rgba(155,108,255,.24)' } }}>
          {playing ? <PauseIcon /> : <PlayIcon />}
        </IconButton>
        <Typography variant="caption" sx={{ width: 35, textAlign: 'right', display: { xs: 'none', sm: 'block' } }}>
          {fmtTime(currentTime)}
        </Typography>
        <Slider
          aria-label="Seek playback position"
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
          aria-label="Volume"
          size="small"
          value={volume}
          max={1}
          step={0.05}
          onChange={handleVolumeChange}
          sx={{ width: 80, display: { xs: 'none', md: 'block' } }}
        />
        <IconButton size="small" onClick={onClose} aria-label="Close player">
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
  const [drawerCollapsed, setDrawerCollapsed] = useState(false);
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({ discover: true, library: true, system: true });
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
      if (user) {
        logout();
      }
    };
    window.addEventListener('auth:logout', handleLogoutEvent);
    return () => window.removeEventListener('auth:logout', handleLogoutEvent);
  }, [logout, user]);

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

  const navigationGroups = [
    {
      id: 'discover', label: 'Discover', icon: <PlaylistIcon />,
      items: [
        { id: 'explore', text: 'Explore', icon: <PlaylistIcon /> },
        { id: 'search', text: 'Search music', icon: <SearchIcon /> },
        { id: 'requests', text: 'Music requests', icon: <TasksIcon /> },
        { id: 'my-artists', text: 'My artists', icon: <ArtistsIcon /> },
        { id: 'feedback', text: 'My feedback', icon: <FavoriteIcon /> },
        { id: 'listenbrainz', text: 'ListenBrainz', icon: <MusicIcon /> },
      ],
    },
    {
      id: 'library', label: 'Library', icon: <LibraryIcon />,
      items: [
        { id: 'library-manager', text: 'Library manager', icon: <LibraryIcon /> },
        { id: 'metadata-rebuild', text: 'Metadata rebuild', icon: <RebuildIcon /> },
        { id: 'album-art', text: 'Artwork', icon: <AlbumArtIcon /> },
        { id: 'lyrics', text: 'Lyrics', icon: <MusicIcon /> },
        { id: 'duplicates', text: 'Duplicates', icon: <DuplicatesIcon /> },
        { id: 'naming', text: 'Naming conventions', icon: <SettingsIcon /> },
        { id: 'feat-fixer', text: 'Feature artist fixer', icon: <ArtistsIcon /> },
        { id: 'musicbrainz-inspector', text: 'MusicBrainz inspector', icon: <AlbumIcon /> },
      ],
    },
    {
      id: 'system', label: 'System', icon: <TasksIcon />,
      items: [
        { id: 'tasks', text: 'Running tasks', icon: <TasksIcon /> },
        { id: 'history', text: 'Sync history', icon: <HistoryIcon /> },
        { id: 'logs', text: 'Live logs', icon: <LogsIcon /> },
        ...(user?.isAdmin ? [
          { id: 'server-health', text: 'Server health', icon: <HealthIcon /> },
          { id: 'acoustid', text: 'AcoustID verification', icon: <FingerprintIcon /> },
          { id: 'aliases', text: 'Artist aliases', icon: <ArtistsIcon /> },
          { id: 'config', text: 'Configuration', icon: <SettingsIcon /> },
        ] : []),
      ],
    },
  ];
  const navigationItems = [
    { id: 'dashboard', text: 'Home', icon: <DashboardIcon /> },
    ...navigationGroups.flatMap(group => group.items),
    { id: 'user-settings', text: 'My settings', icon: <UserSettingsIcon /> },
  ];

  const drawerContent = (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <Box sx={{ p: drawerCollapsed ? 2 : 2.5, display: 'flex', alignItems: 'center', gap: 1.25, minHeight: 82 }}>
        <Box sx={{ width: 42, height: 42, borderRadius: 2.5, display: 'grid', placeItems: 'center', flexShrink: 0, background: 'linear-gradient(135deg, #6d3df5, #b084ff)', boxShadow: '0 10px 26px rgba(109,61,245,0.32)' }}>
          <MusicIcon sx={{ color: '#fff', fontSize: 24 }} />
        </Box>
        {!drawerCollapsed && <Box sx={{ minWidth: 0 }}>
          <Typography variant="h6" sx={{ fontWeight: 850, lineHeight: 1 }}>VeryDisco</Typography>
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 700, letterSpacing: '.12em' }}>MUSIC OPS</Typography>
        </Box>}
      </Box>
      <Divider />

      <List sx={{ px: drawerCollapsed ? 1 : 1.5, py: 1.5, flex: 1, overflowY: 'auto' }}>
        <ListItem disablePadding sx={{ mb: 1 }}>
          <ListItemButton component="a" href="/dashboard" selected={activeTab === 'dashboard'} onClick={(e) => { if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey) { e.preventDefault(); navigateTo('dashboard'); setMobileOpen(false); } }} sx={{ borderRadius: 2.5, minHeight: 46, justifyContent: drawerCollapsed ? 'center' : 'initial', px: drawerCollapsed ? 1 : 1.5, '&.Mui-selected': { color: 'primary.main', bgcolor: 'rgba(155,108,255,0.13)', '& .MuiListItemIcon-root': { color: 'primary.main' } } }}>
            <ListItemIcon sx={{ minWidth: drawerCollapsed ? 0 : 38, color: activeTab === 'dashboard' ? 'primary.main' : 'text.secondary' }}><DashboardIcon /></ListItemIcon>
            {!drawerCollapsed && <ListItemText primary="Home" primaryTypographyProps={{ fontWeight: activeTab === 'dashboard' ? 750 : 550 }} />}
          </ListItemButton>
        </ListItem>

        {navigationGroups.map((group) => {
          const groupActive = group.items.some(item => item.id === activeTab);
          return <Box key={group.id} sx={{ mb: 1 }}>
            {!drawerCollapsed && <ListItem disablePadding>
              <ListItemButton onClick={() => setOpenGroups(current => ({ ...current, [group.id]: !current[group.id] }))} sx={{ borderRadius: 2, minHeight: 34, px: 1.5, color: groupActive ? 'primary.main' : 'text.secondary' }}>
                <ListItemIcon sx={{ minWidth: 32, color: 'inherit' }}>{group.icon}</ListItemIcon>
                <ListItemText primary={group.label} primaryTypographyProps={{ variant: 'overline', fontWeight: 800 }} />
                <ArrowDownIcon sx={{ fontSize: 18, transform: openGroups[group.id] ? 'rotate(0deg)' : 'rotate(-90deg)', transition: 'transform 160ms ease' }} />
              </ListItemButton>
            </ListItem>}
            <Collapse in={drawerCollapsed || openGroups[group.id]} timeout="auto" unmountOnExit={!drawerCollapsed}>
              <List disablePadding>
                {group.items.map((item) => {
                  const isSelected = activeTab === item.id;
                  return <ListItem key={item.id} disablePadding>
                    <Tooltip title={drawerCollapsed ? item.text : ''} placement="right">
                      <ListItemButton component="a" href={`/${item.id}`} selected={isSelected} onClick={(e) => { if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey) { e.preventDefault(); navigateTo(item.id); setMobileOpen(false); } }} sx={{ borderRadius: 2.5, minHeight: 42, justifyContent: drawerCollapsed ? 'center' : 'initial', px: drawerCollapsed ? 1 : 1.5, ml: drawerCollapsed ? 0 : 1, '&.Mui-selected': { color: 'primary.main', bgcolor: 'rgba(155,108,255,0.13)', '& .MuiListItemIcon-root': { color: 'primary.main' } } }}>
                        <ListItemIcon sx={{ minWidth: drawerCollapsed ? 0 : 38, color: isSelected ? 'primary.main' : 'text.secondary' }}>{item.icon}</ListItemIcon>
                        {!drawerCollapsed && <ListItemText primary={item.text} primaryTypographyProps={{ fontWeight: isSelected ? 750 : 550, fontSize: '.9rem' }} />}
                      </ListItemButton>
                    </Tooltip>
                  </ListItem>;
                })}
              </List>
            </Collapse>
          </Box>;
        })}
      </List>

      <Divider />
      <Box sx={{ p: drawerCollapsed ? 1 : 1.5, display: 'flex', alignItems: 'center', gap: 1.25, mb: currentTrack ? '76px' : 0 }}>
        <Avatar sx={{ width: 36, height: 36, bgcolor: 'primary.main', fontSize: '.9rem', fontWeight: 800, flexShrink: 0 }}>{(user.displayName || user.username).charAt(0).toUpperCase()}</Avatar>
        {!drawerCollapsed && <Box sx={{ flex: 1, minWidth: 0 }}><Typography variant="body2" sx={{ fontWeight: 700 }} noWrap>{user.displayName || user.username}</Typography><Typography variant="caption" color="text.secondary">{user.isAdmin ? 'Administrator' : 'Listener'}</Typography></Box>}
        {!drawerCollapsed && <Tooltip title="Sign out"><IconButton size="small" onClick={() => logout()}><LogoutIcon fontSize="small" /></IconButton></Tooltip>}
      </Box>
    </Box>
  );

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh', maxWidth: '100vw', overflowX: 'hidden' }}>
      {/* Header App Bar */}
      <AppBar
        position="fixed"
        sx={{
          width: { md: `calc(100% - ${drawerCollapsed ? DRAWER_COLLAPSED_WIDTH : DRAWER_WIDTH}px)` },
          ml: { md: `${drawerCollapsed ? DRAWER_COLLAPSED_WIDTH : DRAWER_WIDTH}px` },
          bgcolor: 'rgba(21,19,29,0.78)',
          color: 'text.primary',
          boxShadow: 'none',
          borderBottom: '1px solid',
          borderColor: 'divider',
          backdropFilter: 'blur(24px)',
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
            <Tooltip title={drawerCollapsed ? 'Expand navigation' : 'Collapse navigation'}>
              <IconButton color="inherit" onClick={() => setDrawerCollapsed(current => !current)} sx={{ display: { xs: 'none', md: 'inline-flex' }, mr: 1 }} aria-label={drawerCollapsed ? 'Expand navigation' : 'Collapse navigation'}>
                <MenuIcon sx={{ transform: drawerCollapsed ? 'rotate(180deg)' : 'none', transition: 'transform 180ms ease' }} />
              </IconButton>
            </Tooltip>
            <Typography variant="h6" noWrap component="div" sx={{ fontWeight: 700 }}>
              {navigationItems.find(n => n.id === activeTab)?.text}
            </Typography>
          </Box>

          <Box display="flex" alignItems="center" gap={1}>
            <Chip icon={<Box component="span" sx={{ width: 7, height: 7, borderRadius: '50%', bgcolor: 'success.main' }} />} label="Navidrome online" size="small" sx={{ display: { xs: 'none', md: 'flex' }, bgcolor: 'rgba(98,217,154,0.10)', color: 'success.main', border: '1px solid rgba(98,217,154,0.18)' }} />
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
      <Box component="nav" sx={{ width: { md: drawerCollapsed ? DRAWER_COLLAPSED_WIDTH : DRAWER_WIDTH }, flexShrink: { md: 0 }, transition: 'width 180ms ease' }} aria-label="Main navigation">
        <Drawer
          variant="temporary"
          open={mobileOpen}
          onClose={() => setMobileOpen(false)}
          ModalProps={{ keepMounted: true }}
          sx={{
            display: { xs: 'block', md: 'none' },
            '& .MuiDrawer-paper': { boxSizing: 'border-box', width: DRAWER_WIDTH, borderRight: '1px solid', borderColor: 'divider', background: 'background.paper' },
          }}
        >
          {drawerContent}
        </Drawer>

        <Drawer
          variant="permanent"
          sx={{
            display: { xs: 'none', md: 'block' },
            '& .MuiDrawer-paper': { boxSizing: 'border-box', width: drawerCollapsed ? DRAWER_COLLAPSED_WIDTH : DRAWER_WIDTH, borderRight: '1px solid', borderColor: 'divider', background: 'background.paper', transition: 'width 180ms ease' },
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
          p: { xs: 1.5, sm: 3, md: 4.5 },
          width: { md: `calc(100% - ${drawerCollapsed ? DRAWER_COLLAPSED_WIDTH : DRAWER_WIDTH}px)` },
          minWidth: 0,
          mt: '72px',
          mb: { xs: currentTrack ? '178px' : '76px', md: currentTrack ? '96px' : 0 },
          bgcolor: 'background.default',
        }}
      >
        {activeTab === 'dashboard' && <Dashboard onNavigateToConfig={() => navigateTo('config')} />}
        {activeTab === 'explore' && <Explore />}
        {activeTab === 'search' && <SearchMusic />}
        {activeTab === 'requests' && <MusicRequests />}
        {activeTab === 'my-artists' && <MyArtists />}
        {activeTab === 'feedback' && <MyFeedback />}
        {activeTab === 'listenbrainz' && <ListenBrainz />}
        {activeTab === 'server-health' && user?.isAdmin && <ServerHealth />}
        {activeTab === 'acoustid' && <AcoustIDManager />}
        {activeTab === 'library-manager' && <LibraryManager />}
        {activeTab === 'naming' && <NamingConvention />}
        {activeTab === 'feat-fixer' && <FeatFixer />}
        {activeTab === 'metadata-rebuild' && <MetadataRebuild />}
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

      <Box sx={{ display: { xs: 'flex', md: 'none' }, position: 'fixed', bottom: currentTrack ? '82px' : 0, left: 0, right: 0, zIndex: 1200, p: 1, gap: .5, bgcolor: 'rgba(21,19,29,.92)', borderTop: '1px solid', borderColor: 'divider', backdropFilter: 'blur(24px)' }}>
        {[['dashboard', 'Home', <DashboardIcon />], ['explore', 'Explore', <PlaylistIcon />], ['library-manager', 'Library', <LibraryIcon />], ['tasks', 'Activity', <TasksIcon />]].map(([id, label, icon]) => <Button key={String(id)} onClick={() => navigateTo(String(id))} sx={{ flex: 1, minWidth: 0, minHeight: 48, px: .5, flexDirection: 'column', gap: .25, color: activeTab === id ? 'primary.main' : 'text.secondary', bgcolor: activeTab === id ? 'rgba(155,108,255,.12)' : 'transparent', '&:hover': { bgcolor: 'rgba(155,108,255,.10)' } }}>
          {icon}<Typography variant="caption" sx={{ fontWeight: activeTab === id ? 800 : 600, fontSize: '.68rem' }}>{label}</Typography>
        </Button>)}
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
