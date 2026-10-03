import React, { useEffect, useMemo, useState } from 'react';
import { Alert, AlertTitle, Avatar, Box, Button, Card, CardContent, Chip, CircularProgress, Divider, Grid, LinearProgress, Stack, Typography, useTheme } from '@mui/material';
import { Album as AlbumIcon, ArrowForward as ArrowForwardIcon, CheckCircle as SuccessIcon, LibraryMusic as MusicIcon, People as ArtistIcon, PlayArrow as PlayIcon, Storage as StorageIcon, Sync as SyncIcon, WarningAmber as WarningIcon } from '@mui/icons-material';
import { apiService, GetStatusResponse, RunRecord } from '../api';

interface DashboardProps { onNavigateToConfig: () => void; }
const playlistLabel = (value: string) => value.replace(/[-_]/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase());

const StatCard: React.FC<{ label: string; value: number | string; icon: React.ReactNode; tone: string }> = ({ label, value, icon, tone }) => (
  <Card sx={{ height: '100%', background: 'linear-gradient(145deg, rgba(255,255,255,0.045), rgba(255,255,255,0.012))' }}>
    <CardContent sx={{ p: { xs: 2, sm: 2.5 } }}>
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start" gap={2}><Box><Typography variant="caption" color="text.secondary" sx={{ fontWeight: 700 }}>{label}</Typography><Typography variant="h4" sx={{ mt: .5, fontWeight: 850 }}>{typeof value === 'number' ? value.toLocaleString() : value}</Typography></Box><Avatar sx={{ bgcolor: `${tone}1a`, color: tone, width: 42, height: 42 }}>{icon}</Avatar></Stack>
    </CardContent>
  </Card>
);

const Dashboard: React.FC<DashboardProps> = ({ onNavigateToConfig }) => {
  const theme = useTheme();
  const [status, setStatus] = useState<GetStatusResponse | null>(null);
  const [navidromeStats, setNavidromeStats] = useState<{ songs: number; albums: number; artists: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [statsLoading, setStatsLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const abortControllerRef = React.useRef<AbortController | null>(null);

  const fetchStatus = async () => {
    const controller = new AbortController(); abortControllerRef.current = controller;
    try { setStatus(await apiService.getStatus({ signal: controller.signal })); setErrorMessage(null); }
    catch (error: any) { if (error?.name !== 'CanceledError' && error?.code !== 'ERR_CANCELED') setErrorMessage('Could not reach the VeryDisco backend.'); }
    finally { setLoading(false); }
  };
  const fetchStats = async () => { try { setNavidromeStats(await apiService.getNavidromeStats()); } catch { setNavidromeStats(null); } finally { setStatsLoading(false); } };
  useEffect(() => { void fetchStatus(); void fetchStats(); const statusTimer = window.setInterval(fetchStatus, 5000); const statsTimer = window.setInterval(fetchStats, 60000); return () => { window.clearInterval(statusTimer); window.clearInterval(statsTimer); abortControllerRef.current?.abort(); }; }, []);

  const playlists = useMemo(() => Object.entries(status?.latest_runs || {}) as [string, RunRecord][], [status]);
  const totals = useMemo(() => playlists.reduce((acc, [, run]) => ({ found: acc.found + (run?.tracks_found || 0), downloaded: acc.downloaded + (run?.tracks_downloaded || 0), failed: acc.failed + (run?.tracks_failed || 0) }), { found: 0, downloaded: 0, failed: 0 }), [playlists]);
  const isSyncing = Boolean(status?.is_syncing);
  const triggerSync = async (playlist: string) => { setActionLoading(playlist); try { await apiService.triggerSyncForSource(playlist); setToast({ msg: `${playlistLabel(playlist)} sync started`, type: 'success' }); window.setTimeout(() => void fetchStatus(), 1200); } catch { setToast({ msg: `Unable to start ${playlistLabel(playlist)} sync`, type: 'error' }); } finally { setActionLoading(null); } };
  if (loading && !status) return <Box sx={{ minHeight: '55vh', display: 'grid', placeItems: 'center' }}><CircularProgress /></Box>;

  return <Box sx={{ maxWidth: 1480, mx: 'auto' }}>
    {errorMessage && <Alert severity="error" onClose={() => setErrorMessage(null)} sx={{ mb: 2.5 }}><AlertTitle>Connection issue</AlertTitle>{errorMessage}</Alert>}
    {!status?.is_configured && <Alert severity="warning" sx={{ mb: 2.5 }} action={<Button color="inherit" onClick={onNavigateToConfig}>Configure</Button>}><AlertTitle>Finish your setup</AlertTitle>VeryDisco needs its provider settings before it can sync your library.</Alert>}
    {toast && <Alert severity={toast.type} onClose={() => setToast(null)} sx={{ mb: 2.5 }}>{toast.msg}</Alert>}

    <Card sx={{ mb: 3, overflow: 'hidden', position: 'relative', background: theme.palette.mode === 'dark' ? 'linear-gradient(120deg, #1b1530 0%, #161323 48%, #0d0c12 100%)' : 'linear-gradient(120deg, #f0eaff 0%, #fff 55%, #e7f7f8 100%)' }}>
      <Box sx={{ position: 'absolute', width: 360, height: 360, right: -130, top: -160, borderRadius: '50%', background: 'radial-gradient(circle, rgba(155,108,255,.35), transparent 66%)', pointerEvents: 'none' }} />
      <CardContent sx={{ p: { xs: 2.5, sm: 4 } }}><Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" gap={3}><Box><Stack direction="row" alignItems="center" gap={1} mb={1}><Chip size="small" icon={isSyncing ? <SyncIcon className="spin-icon" /> : <SuccessIcon />} label={isSyncing ? 'Sync in progress' : 'Library ready'} color={isSyncing ? 'primary' : 'success'} variant="outlined" /></Stack><Typography variant="h3" sx={{ fontSize: { xs: '2rem', sm: '3rem' } }}>Your music, in motion.</Typography><Typography color="text.secondary" sx={{ mt: 1, maxWidth: 560 }}>Discover fresh picks, keep metadata clean and make your Navidrome library feel alive.</Typography>{status?.next_run && <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 2, fontWeight: 650 }}>Next scheduled sync · {new Date(status.next_run).toLocaleString()}</Typography>}</Box><Stack direction={{ xs: 'row', sm: 'column' }} alignItems={{ sm: 'flex-end' }} gap={1}><Button variant="contained" startIcon={<SyncIcon />} disabled={isSyncing || actionLoading !== null} onClick={() => void triggerSync(playlists[0]?.[0] || 'weekly-exploration')}>Sync now</Button><Button variant="text" endIcon={<ArrowForwardIcon />} onClick={onNavigateToConfig}>Manage setup</Button></Stack></Stack></CardContent>
    </Card>

    <Grid container spacing={2} sx={{ mb: 3 }}><Grid item xs={12} sm={4}><StatCard label="Tracks discovered" value={totals.found} icon={<MusicIcon />} tone="#9b6cff" /></Grid><Grid item xs={12} sm={4}><StatCard label="Tracks added" value={totals.downloaded} icon={<SuccessIcon />} tone="#62d99a" /></Grid><Grid item xs={12} sm={4}><StatCard label="Needs attention" value={totals.failed} icon={<WarningIcon />} tone="#f4c95d" /></Grid></Grid>

    <Grid container spacing={3}><Grid item xs={12} lg={8}><Card sx={{ height: '100%' }}><CardContent sx={{ p: { xs: 2, sm: 3 } }}><Stack direction="row" justifyContent="space-between" alignItems="center" mb={2.5}><Box><Typography variant="h5">Sync sources</Typography><Typography variant="body2" color="text.secondary">Your latest discovery pipelines</Typography></Box><Chip label={`${playlists.length} active`} size="small" variant="outlined" /></Stack><Stack spacing={1.5}>{playlists.length ? playlists.map(([playlist, run]) => { const progress = run?.tracks_found ? Math.min(100, Math.round(((run.tracks_downloaded || 0) / run.tracks_found) * 100)) : 0; return <Box key={playlist} sx={{ p: 2, borderRadius: 3, bgcolor: 'action.hover', border: '1px solid', borderColor: 'divider' }}><Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" gap={1.5}><Box><Typography fontWeight={800}>{playlistLabel(playlist)}</Typography><Typography variant="caption" color="text.secondary">{run?.timestamp ? `Last run ${new Date(run.timestamp).toLocaleString()}` : 'No run recorded yet'}</Typography></Box><Stack direction="row" alignItems="center" gap={1}><Chip size="small" label={run?.status || 'idle'} color={run?.status === 'completed' ? 'success' : run?.status === 'failed' ? 'error' : 'default'} /><Button size="small" variant="outlined" disabled={isSyncing || actionLoading === playlist} onClick={() => void triggerSync(playlist)} startIcon={actionLoading === playlist ? <CircularProgress size={14} /> : <SyncIcon />}>Manual Sync</Button></Stack></Stack><LinearProgress variant="determinate" value={progress} sx={{ mt: 1.5, height: 6, borderRadius: 5 }} /><Stack direction="row" justifyContent="space-between" mt={.75}><Typography variant="caption" color="text.secondary">{run?.tracks_downloaded || 0} added · {run?.tracks_skipped || 0} skipped</Typography><Typography variant="caption" color="text.secondary">{progress}%</Typography></Stack></Box>; }) : <Box sx={{ p: 4, textAlign: 'center' }}><MusicIcon sx={{ fontSize: 44, color: 'text.disabled' }} /><Typography color="text.secondary" mt={1}>No sync sources have reported a run yet.</Typography></Box>}</Stack></CardContent></Card></Grid>
      <Grid item xs={12} lg={4}><Card sx={{ height: '100%' }}><CardContent sx={{ p: { xs: 2, sm: 3 } }}><Stack direction="row" alignItems="center" gap={1} mb={2.5}><StorageIcon color="primary" /><Box><Typography variant="h5">Library pulse</Typography><Typography variant="body2" color="text.secondary">Navidrome at a glance</Typography></Box></Stack>{statsLoading ? <Box sx={{ py: 5, display: 'grid', placeItems: 'center' }}><CircularProgress size={28} /></Box> : navidromeStats ? <Stack spacing={1.5}>{[['Tracks', navidromeStats.songs, <MusicIcon />], ['Albums', navidromeStats.albums, <AlbumIcon />], ['Artists', navidromeStats.artists, <ArtistIcon />]].map(([label, value, icon]) => <Stack key={String(label)} direction="row" alignItems="center" justifyContent="space-between" sx={{ p: 1.5, borderRadius: 2.5, bgcolor: 'action.hover' }}><Stack direction="row" alignItems="center" gap={1.25}><Avatar sx={{ width: 34, height: 34, bgcolor: 'rgba(155,108,255,.14)', color: 'primary.main' }}>{icon}</Avatar><Typography color="text.secondary">{label}</Typography></Stack><Typography variant="h6" fontWeight={850}>{Number(value).toLocaleString()}</Typography></Stack>)}</Stack> : <Alert severity="info">Navidrome stats are unavailable. Check the server connection.</Alert>}</CardContent></Card></Grid>
    </Grid>

    <Card sx={{ mt: 3 }}><CardContent sx={{ p: { xs: 2, sm: 3 } }}><Stack direction="row" justifyContent="space-between" alignItems="center"><Box><Typography variant="h5">Activity feed</Typography><Typography variant="body2" color="text.secondary">A quick read on the latest library work</Typography></Box><Chip label="Live" size="small" color="secondary" variant="outlined" /></Stack><Divider sx={{ my: 2 }} /><Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}><Box sx={{ flex: 1, display: 'flex', gap: 1.5 }}><Avatar sx={{ bgcolor: 'rgba(98,217,154,.14)', color: 'success.main' }}><SuccessIcon /></Avatar><Box><Typography fontWeight={750}>{totals.downloaded ? `${totals.downloaded} tracks added recently` : 'No new tracks yet'}</Typography><Typography variant="body2" color="text.secondary">Keep your discovery sources running to grow the library.</Typography></Box></Box><Box sx={{ flex: 1, display: 'flex', gap: 1.5 }}><Avatar sx={{ bgcolor: totals.failed ? 'rgba(244,201,93,.14)' : 'rgba(155,108,255,.14)', color: totals.failed ? 'warning.main' : 'primary.main' }}>{totals.failed ? <WarningIcon /> : <PlayIcon />}</Avatar><Box><Typography fontWeight={750}>{totals.failed ? `${totals.failed} tracks need attention` : 'Everything looks clean'}</Typography><Typography variant="body2" color="text.secondary">Review failed downloads from Running Tasks when needed.</Typography></Box></Box></Stack></CardContent></Card>
    <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } } .spin-icon { animation: spin 1.8s linear infinite; }`}</style>
  </Box>;
};

export default Dashboard;
