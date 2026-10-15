import React, { useState, useEffect } from 'react';
import { 
  ThemeProvider, CssBaseline, Box, Drawer, AppBar, Toolbar, 
  List, ListItem, ListItemButton, ListItemIcon, ListItemText, 
  Typography, IconButton, Divider, Avatar, Tooltip, Chip, Collapse, Button, LinearProgress, useMediaQuery
} from '@mui/material';
import MenuIcon from '@mui/icons-material/Menu';
import DarkModeIcon from '@mui/icons-material/Brightness4';
import LightModeIcon from '@mui/icons-material/Brightness7';
import DashboardIcon from '@mui/icons-material/Dashboard';
import SettingsIcon from '@mui/icons-material/Settings';
import HistoryIcon from '@mui/icons-material/History';
import LogsIcon from '@mui/icons-material/Terminal';
import MusicIcon from '@mui/icons-material/MusicNote';
import PlaylistIcon from '@mui/icons-material/QueueMusic';
import SearchIcon from '@mui/icons-material/Search';
import FavoriteIcon from '@mui/icons-material/Favorite';
import ArtistsIcon from '@mui/icons-material/Person';
import HealthIcon from '@mui/icons-material/HealthAndSafety';
import LibraryIcon from '@mui/icons-material/LibraryMusic';
import LogoutIcon from '@mui/icons-material/Logout';
import UserSettingsIcon from '@mui/icons-material/ManageAccounts';
import TasksIcon from '@mui/icons-material/PendingActions';
import AlbumArtIcon from '@mui/icons-material/PhotoLibrary';
import DuplicatesIcon from '@mui/icons-material/FileCopy';
import FingerprintIcon from '@mui/icons-material/Fingerprint';
import AlbumIcon from '@mui/icons-material/Album';
import RebuildIcon from '@mui/icons-material/AutoFixHigh';
import ArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import getTheme from './theme';
import { alpha } from '@mui/material/styles';
import { resolveTab, TabId } from './navigation';
import CommandPalette from './components/CommandPalette';
import GlobalPlayer from './components/GlobalPlayer';
import PageErrorBoundary from './components/PageErrorBoundary';
import Login from './components/Login';
import Setup from './components/Setup';
import { AuthProvider, useAuth } from './context/AuthContext';

const Dashboard = React.lazy(() => import('./components/Dashboard'));
const Configuration = React.lazy(() => import('./components/Configuration'));
const RunHistory = React.lazy(() => import('./components/RunHistory'));
const LiveLogs = React.lazy(() => import('./components/LiveLogs'));
const Explore = React.lazy(() => import('./components/Explore'));
const SearchMusic = React.lazy(() => import('./components/SearchMusic'));
const MyFeedback = React.lazy(() => import('./components/MyFeedback'));
const ListenBrainz = React.lazy(() => import('./components/ListenBrainz'));
const MyArtists = React.lazy(() => import('./components/MyArtists'));
const ServerHealth = React.lazy(() => import('./components/ServerHealth'));
const LibraryManager = React.lazy(() => import('./components/LibraryManager'));
const LyricsManager = React.lazy(() => import('./components/LyricsManager'));
const AlbumArtManager = React.lazy(() => import('./components/AlbumArtManager'));
const DuplicatesManager = React.lazy(() => import('./components/DuplicatesManager'));
const UserSettings = React.lazy(() => import('./components/UserSettings'));
const RunningTasks = React.lazy(() => import('./components/RunningTasks'));
const AcoustIDManager = React.lazy(() => import('./components/AcoustIDManager'));
const NamingConvention = React.lazy(() => import('./components/NamingConvention'));
const FeatFixer = React.lazy(() => import('./components/FeatFixer'));
const MusicBrainzInspector = React.lazy(() => import('./components/MusicBrainzInspector'));
const MusicRequests = React.lazy(() => import('./components/MusicRequests'));
const MetadataRebuild = React.lazy(() => import('./components/MetadataRebuild'));
const PlaylistImport = React.lazy(() => import('./components/PlaylistImport'));

const DRAWER_WIDTH = 260;
const DRAWER_COLLAPSED_WIDTH = 78;


interface AppInnerProps {
  mode: 'light' | 'dark';
  toggleMode: () => void;
}

const AppInner: React.FC<AppInnerProps> = ({ mode, toggleMode }) => {
  const { user, loading, logout, isConfigured } = useAuth();

  const [activeTab, setActiveTab] = useState<TabId>(() => {
    const path = window.location.pathname.replace(/^\//, '');
    return resolveTab(path || sessionStorage.getItem('vd-active-tab'));
  });

  const [mobileOpen, setMobileOpen] = useState(false);
  const [drawerCollapsed, setDrawerCollapsed] = useState(() => localStorage.getItem('verydisco-nav-collapsed') === 'true');
  const [commandOpen, setCommandOpen] = useState(false);
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({ discover: true, library: true, system: true });
  const [currentTrack, setCurrentTrack] = useState<{ filepath: string; title: string; artist: string } | null>(null);

  useEffect(() => {
    const handlePopState = () => {
      setActiveTab(resolveTab(window.location.pathname, Boolean(user?.isAdmin)));
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [user?.isAdmin]);

  useEffect(() => {
    if (user) {
      const permitted = resolveTab(activeTab, user.isAdmin);
      if (permitted !== activeTab) { setActiveTab(permitted); window.history.replaceState(null, '', `/${permitted}`); }
    }
  }, [activeTab, user]);
  useEffect(() => { localStorage.setItem('verydisco-nav-collapsed', String(drawerCollapsed)); }, [drawerCollapsed]);
  useEffect(() => {
    sessionStorage.setItem('vd-active-tab', activeTab);
    setMobileOpen(false);
    document.getElementById('main-content')?.focus({ preventScroll: true });
  }, [activeTab]);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setCommandOpen(current => !current); }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, []);

  useEffect(() => {
    const handleLogoutEvent = () => {
      setCurrentTrack(null);
      setCommandOpen(false);
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
      const { filepath, title, artist } = e.detail || {};
      if (typeof filepath !== 'string' || !filepath) return;
      setCurrentTrack({ filepath, title, artist });
    };
    window.addEventListener("verydisco-play", handlePlay);
    return () => window.removeEventListener("verydisco-play", handlePlay);
  }, []);

  const navigateTo = React.useCallback((tab: string) => {
    const destination = resolveTab(tab, Boolean(user?.isAdmin));
    setActiveTab(destination);
    if (window.location.pathname !== `/${destination}`) window.history.pushState(null, '', `/${destination}`);
  }, [user?.isAdmin]);

  // Listen for custom navigation events
  useEffect(() => {
    const handleNavigate = (e: any) => {
      if (e.detail) {
        navigateTo(e.detail);
      }
    };
    window.addEventListener("verydisco-navigate", handleNavigate);
    return () => window.removeEventListener("verydisco-navigate", handleNavigate);
  }, [navigateTo]);

  const activeTheme = React.useMemo(() => getTheme(mode), [mode]);
  const mobileView = useMediaQuery(activeTheme.breakpoints.down('md'));
  const navigationCollapsed = drawerCollapsed && !mobileView;
  useEffect(() => { setCurrentTrack(null); setCommandOpen(false); }, [user?.id]);

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
        { id: 'playlist-import', text: 'Playlist importing', icon: <PlaylistIcon /> },
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
        ...(user?.isAdmin ? [
          { id: 'logs', text: 'Live logs', icon: <LogsIcon /> },
          { id: 'server-health', text: 'Server health', icon: <HealthIcon /> },
          { id: 'acoustid', text: 'AcoustID verification', icon: <FingerprintIcon /> },
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
  const destinations = [
    { id: 'dashboard', text: 'Home', icon: <DashboardIcon />, group: 'Overview' },
    ...navigationGroups.flatMap(group => group.items.map(item => ({ ...item, group: group.label }))),
    { id: 'user-settings', text: 'My settings', icon: <UserSettingsIcon />, group: 'Account' },
  ];
  const activeGroup = destinations.find(item => item.id === activeTab)?.group || 'Workspace';

  const drawerContent = (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <Box sx={{ p: navigationCollapsed ? 2 : 2.5, display: 'flex', alignItems: 'center', gap: 1.25, minHeight: 82 }}>
        <Box sx={{ width: 42, height: 42, borderRadius: 2.5, display: 'grid', placeItems: 'center', flexShrink: 0, background: 'linear-gradient(135deg, #6d3df5, #b084ff)', boxShadow: '0 10px 26px rgba(109,61,245,0.32)' }}>
          <MusicIcon sx={{ color: '#fff', fontSize: 24 }} />
        </Box>
        {!navigationCollapsed && <Box sx={{ minWidth: 0 }}>
          <Typography variant="h6" sx={{ fontWeight: 850, lineHeight: 1 }}>VeryDisco</Typography>
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 700, letterSpacing: '.12em' }}>MUSIC OPS</Typography>
        </Box>}
      </Box>
      <Divider />

      <List sx={{ px: navigationCollapsed ? 1 : 1.5, py: 1.5, flex: 1, overflowY: 'auto' }}>
        <ListItem disablePadding sx={{ mb: 1 }}>
          <ListItemButton component="a" href="/dashboard" aria-label="Home" aria-current={activeTab === 'dashboard' ? 'page' : undefined} selected={activeTab === 'dashboard'} onClick={(e) => { if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) { e.preventDefault(); navigateTo('dashboard'); setMobileOpen(false); } }} sx={{ borderRadius: 2.5, minHeight: 46, justifyContent: navigationCollapsed ? 'center' : 'initial', px: navigationCollapsed ? 1 : 1.5, '&.Mui-selected': { color: 'primary.main', bgcolor: 'rgba(155,108,255,0.13)', '& .MuiListItemIcon-root': { color: 'primary.main' } } }}>
            <ListItemIcon sx={{ minWidth: navigationCollapsed ? 0 : 38, color: activeTab === 'dashboard' ? 'primary.main' : 'text.secondary' }}><DashboardIcon /></ListItemIcon>
            {!navigationCollapsed && <ListItemText primary="Home" primaryTypographyProps={{ fontWeight: activeTab === 'dashboard' ? 750 : 550 }} />}
          </ListItemButton>
        </ListItem>

        {navigationGroups.map((group) => {
          const groupActive = group.items.some(item => item.id === activeTab);
          return <Box key={group.id} sx={{ mb: 1 }}>
            {!navigationCollapsed && <ListItem disablePadding>
              <ListItemButton aria-expanded={!!openGroups[group.id]} onClick={() => setOpenGroups(current => ({ ...current, [group.id]: !current[group.id] }))} sx={{ borderRadius: 2, minHeight: 34, px: 1.5, color: groupActive ? 'primary.main' : 'text.secondary' }}>
                <ListItemIcon sx={{ minWidth: 32, color: 'inherit' }}>{group.icon}</ListItemIcon>
                <ListItemText primary={group.label} primaryTypographyProps={{ variant: 'overline', fontWeight: 800 }} />
                <ArrowDownIcon sx={{ fontSize: 18, transform: openGroups[group.id] ? 'rotate(0deg)' : 'rotate(-90deg)', transition: 'transform 160ms ease' }} />
              </ListItemButton>
            </ListItem>}
            <Collapse in={navigationCollapsed || openGroups[group.id]} timeout="auto" unmountOnExit={!navigationCollapsed}>
              <List disablePadding>
                {group.items.map((item) => {
                  const isSelected = activeTab === item.id;
                  return <ListItem key={item.id} disablePadding>
                    <Tooltip title={navigationCollapsed ? item.text : ''} placement="right">
                      <ListItemButton component="a" href={`/${item.id}`} aria-label={item.text} aria-current={isSelected ? 'page' : undefined} selected={isSelected} onClick={(e) => { if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) { e.preventDefault(); navigateTo(item.id); setMobileOpen(false); } }} sx={{ borderRadius: 2.5, minHeight: 42, justifyContent: navigationCollapsed ? 'center' : 'initial', px: navigationCollapsed ? 1 : 1.5, ml: navigationCollapsed ? 0 : 1, '&.Mui-selected': { color: 'primary.main', bgcolor: 'rgba(155,108,255,0.13)', '& .MuiListItemIcon-root': { color: 'primary.main' } } }}>
                        <ListItemIcon sx={{ minWidth: navigationCollapsed ? 0 : 38, color: isSelected ? 'primary.main' : 'text.secondary' }}>{item.icon}</ListItemIcon>
                        {!navigationCollapsed && <ListItemText primary={item.text} primaryTypographyProps={{ fontWeight: isSelected ? 750 : 550, fontSize: '.9rem' }} />}
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
      <Box sx={{ p: navigationCollapsed ? 1 : 1.5, display: 'flex', alignItems: 'center', gap: 1.25, mb: currentTrack ? '76px' : 0 }}>
        <Avatar component="button" aria-label="Open my settings" onClick={() => navigateTo('user-settings')} sx={{ cursor: 'pointer', border: 0, width: 36, height: 36, bgcolor: 'primary.main', fontSize: '.9rem', fontWeight: 800, flexShrink: 0 }}>{(user.displayName || user.username).charAt(0).toUpperCase()}</Avatar>
        {!navigationCollapsed && <Box sx={{ flex: 1, minWidth: 0 }}><Typography variant="body2" sx={{ fontWeight: 700 }} noWrap>{user.displayName || user.username}</Typography><Typography variant="caption" color="text.secondary">{user.isAdmin ? 'Administrator' : 'Listener'}</Typography></Box>}
        {!navigationCollapsed && <Tooltip title="Sign out"><IconButton size="small" onClick={() => { setCurrentTrack(null); void logout(); }}><LogoutIcon fontSize="small" /></IconButton></Tooltip>}
      </Box>
    </Box>
  );

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh', maxWidth: '100vw', overflowX: 'hidden' }}>
      <Box component="a" href="#main-content" sx={{ position: 'fixed', left: 16, top: -80, zIndex: 1600, px: 2, py: 1.5, bgcolor: 'primary.main', color: 'primary.contrastText', borderRadius: 2, '&:focus': { top: 12 } }}>Skip to content</Box>
      {/* Header App Bar */}
      <AppBar
        position="fixed"
        sx={{
          width: { md: `calc(100% - ${drawerCollapsed ? DRAWER_COLLAPSED_WIDTH : DRAWER_WIDTH}px)` },
          ml: { md: `${drawerCollapsed ? DRAWER_COLLAPSED_WIDTH : DRAWER_WIDTH}px` },
          bgcolor: theme => alpha(theme.palette.background.default, .88),
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
            <Box sx={{ minWidth: 0 }}><Typography variant="overline" color="text.secondary" sx={{ display: { xs: 'none', sm: 'block' }, fontSize: '.6rem', lineHeight: 1.4 }}>{activeGroup}</Typography><Typography variant="body1" noWrap component="div" sx={{ fontWeight: 700 }}>
              {navigationItems.find(n => n.id === activeTab)?.text}
            </Typography></Box>
          </Box>

          <Box display="flex" alignItems="center" gap={1}>
            <Button aria-label="Search pages" onClick={() => setCommandOpen(true)} startIcon={<SearchIcon fontSize="small" />} sx={{ mr: { xs: 0, sm: 1 }, minWidth: { xs: 40, sm: 200 }, justifyContent: 'flex-start', color: 'text.secondary', border: '1px solid', borderColor: 'divider', bgcolor: 'action.hover', px: { xs: 1, sm: 1.5 }, '& .MuiButton-startIcon': { mr: { xs: 0, sm: 1 } } }}>
              <Box component="span" sx={{ display: { xs: 'none', sm: 'block' } }}>Go to a page…</Box><Box component="kbd" sx={{ display: { xs: 'none', sm: 'block' }, ml: 'auto', pl: 2, fontSize: '.68rem', opacity: .65 }}>Ctrl K</Box>
            </Button>
            <Chip
              avatar={<Avatar sx={{ bgcolor: 'primary.main', width: 24, height: 24, fontSize: '0.75rem', fontWeight: 700 }}>{(user.displayName || user.username).charAt(0).toUpperCase()}</Avatar>}
              label={user.displayName || user.username}
              size="small"
              sx={{ fontWeight: 600, display: { xs: 'none', sm: 'flex' } }}
            />
            <Tooltip title={mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}><IconButton onClick={toggleMode} color="inherit" aria-label={mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
              {mode === 'dark' ? <LightModeIcon /> : <DarkModeIcon />}
            </IconButton></Tooltip>
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
        id="main-content"
        tabIndex={-1}
        sx={{
          flexGrow: 1,
          p: { xs: 1.5, sm: 3, md: 4.5 },
          width: { md: `calc(100% - ${drawerCollapsed ? DRAWER_COLLAPSED_WIDTH : DRAWER_WIDTH}px)` },
          minWidth: 0,
          mt: '72px',
          mb: { xs: currentTrack ? 'calc(180px + env(safe-area-inset-bottom))' : 'calc(80px + env(safe-area-inset-bottom))', md: currentTrack ? '108px' : 0 },
          outline: 'none',
        }}
      >
        <PageErrorBoundary key={activeTab}><React.Suspense fallback={<Box role="status" aria-label="Loading page" sx={{ p: 3 }}><LinearProgress sx={{ borderRadius: 4 }} /><Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>Opening your workspace…</Typography></Box>}>
          {activeTab === 'dashboard' && <Dashboard onNavigateToConfig={() => navigateTo('config')} />}
          {activeTab === 'explore' && <Explore />}
          {activeTab === 'search' && <SearchMusic />}
          {activeTab === 'requests' && <MusicRequests />}
          {activeTab === 'my-artists' && <MyArtists />}
          {activeTab === 'feedback' && <MyFeedback />}
          {activeTab === 'listenbrainz' && <ListenBrainz />}
          {activeTab === 'playlist-import' && <PlaylistImport />}
          {activeTab === 'server-health' && user?.isAdmin && <ServerHealth />}
          {activeTab === 'acoustid' && user.isAdmin && <AcoustIDManager />}
          {activeTab === 'library-manager' && <LibraryManager />}
          {activeTab === 'naming' && <NamingConvention />}
          {activeTab === 'feat-fixer' && <FeatFixer />}
          {activeTab === 'metadata-rebuild' && <MetadataRebuild />}
          {activeTab === 'musicbrainz-inspector' && <MusicBrainzInspector />}
          {activeTab === 'lyrics' && <LyricsManager />}
          {activeTab === 'album-art' && <AlbumArtManager />}
          {activeTab === 'duplicates' && <DuplicatesManager />}
          {activeTab === 'config' && user.isAdmin && <Configuration />}
          {activeTab === 'tasks' && <RunningTasks />}
          {activeTab === 'history' && <RunHistory />}
          {activeTab === 'logs' && <LiveLogs />}
          {activeTab === 'user-settings' && <UserSettings />}
        </React.Suspense></PageErrorBoundary>
      </Box>

      <Box component="nav" aria-label="Quick navigation" sx={theme => ({ display: { xs: 'flex', md: 'none' }, position: 'fixed', bottom: currentTrack ? 'calc(88px + env(safe-area-inset-bottom))' : 0, left: 0, right: 0, zIndex: 1200, p: 1, pb: currentTrack ? 1 : 'max(8px, env(safe-area-inset-bottom))', gap: .5, bgcolor: alpha(theme.palette.background.paper, .96), borderTop: '1px solid', borderColor: 'divider', backdropFilter: 'blur(24px)' })}>
        {[['dashboard', 'Home', <DashboardIcon />], ['explore', 'Explore', <PlaylistIcon />], ['library-manager', 'Library', <LibraryIcon />], ['tasks', 'Activity', <TasksIcon />]].map(([id, label, icon]) => <Button key={String(id)} onClick={() => navigateTo(String(id))} sx={{ flex: 1, minWidth: 0, minHeight: 48, px: .5, flexDirection: 'column', gap: .25, color: activeTab === id ? 'primary.main' : 'text.secondary', bgcolor: activeTab === id ? 'rgba(155,108,255,.12)' : 'transparent', '&:hover': { bgcolor: 'rgba(155,108,255,.10)' } }}>
          {icon}<Typography variant="caption" sx={{ fontWeight: activeTab === id ? 800 : 600, fontSize: '.68rem' }}>{label}</Typography>
        </Button>)}
      </Box>

      {/* Global Audio Player Bar */}
      <GlobalPlayer track={currentTrack} onClose={() => setCurrentTrack(null)} />
      <CommandPalette open={commandOpen} onClose={() => setCommandOpen(false)} destinations={destinations} onNavigate={navigateTo} />
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

  const activeTheme = React.useMemo(() => getTheme(mode), [mode]);

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
