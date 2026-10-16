import React from 'react';
import { alpha } from '@mui/material/styles';
import { Alert, AlertTitle, Avatar, Box, Button, Card, CardContent, Chip, CircularProgress, Grid, LinearProgress, Skeleton, Stack, Typography } from '@mui/material';
import AlbumRounded from '@mui/icons-material/AlbumRounded';
import ArrowForwardRounded from '@mui/icons-material/ArrowForwardRounded';
import CheckRounded from '@mui/icons-material/CheckRounded';
import ExploreRounded from '@mui/icons-material/ExploreRounded';
import GraphicEqRounded from '@mui/icons-material/GraphicEqRounded';
import LibraryMusicRounded from '@mui/icons-material/LibraryMusicRounded';
import PeopleRounded from '@mui/icons-material/PeopleRounded';
import SearchRounded from '@mui/icons-material/SearchRounded';
import SyncRounded from '@mui/icons-material/SyncRounded';
import WarningAmberRounded from '@mui/icons-material/WarningAmberRounded';
import { apiService, GetStatusResponse, RunRecord } from '../api';
import { syncProgress } from '../syncProgress';

const SOURCES = ['daily-jams', 'weekly-jams', 'weekly-exploration'];
const sourceName = (value: string) => value.replace(/[-_]/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase());
const go = (page: string) => window.dispatchEvent(new CustomEvent('verydisco-navigate', { detail: page }));
const dateLabel = (value: string) => { const date = new Date(value); return Number.isFinite(date.getTime()) ? date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : 'Date unavailable'; };
type Stats = Awaited<ReturnType<typeof apiService.getNavidromeStats>>;

function Metric({ label, value, icon, color, caption }: { label: string; value: number | null; icon: React.ReactNode; color: string; caption: string }) {
  return <Card sx={{ height: '100%' }}><CardContent sx={{ p: { xs: 2.5, sm: 3 } }}>
    <Stack direction="row" alignItems="center" justifyContent="space-between" gap={1}><Typography variant="body2" color="text.secondary" fontWeight={600}>{label}</Typography><Avatar variant="rounded" sx={{ width: 36, height: 36, bgcolor: alpha(color, .12), color, borderRadius: 2.5 }}>{icon}</Avatar></Stack>
    <Typography variant="h3" sx={{ mt: 1.25, mb: .5, fontVariantNumeric: 'tabular-nums', letterSpacing: '-.045em' }}>{value === null ? '—' : value.toLocaleString()}</Typography>
    <Typography variant="caption" color="text.secondary">{caption}</Typography>
  </CardContent></Card>;
}

export default function HomeDashboard({ onNavigateToConfig }: { onNavigateToConfig: () => void }) {
  const [status, setStatus] = React.useState<GetStatusResponse | null>(null);
  const [stats, setStats] = React.useState<Stats | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [statsLoading, setStatsLoading] = React.useState(true);
  const [updatedAt, setUpdatedAt] = React.useState<Date | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [statsError, setStatsError] = React.useState(false);
  const [action, setAction] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<{ text: string; type: 'success' | 'error' } | null>(null);
  const active = React.useRef(false);
  const statusRequest = React.useRef<AbortController | null>(null);
  const statsRequest = React.useRef<AbortController | null>(null);
  const actionPending = React.useRef(false);

  const refreshStatus = React.useCallback(async () => {
    if (statusRequest.current || !active.current) return;
    const controller = new AbortController(); statusRequest.current = controller;
    try {
      const response = await apiService.getStatus({ signal: controller.signal });
      if (active.current && !controller.signal.aborted) { setStatus(response); setError(null); }
    } catch { if (active.current && !controller.signal.aborted) setError('Could not reach VeryDisco. Displayed data may be out of date.'); }
    finally { if (statusRequest.current === controller) statusRequest.current = null; if (active.current && !controller.signal.aborted) setLoading(false); }
  }, []);
  const refreshStats = React.useCallback(async () => {
    if (statsRequest.current || !active.current) return;
    const controller = new AbortController(); statsRequest.current = controller;
    try {
      const response = await apiService.getNavidromeStats({ signal: controller.signal });
      if (!active.current || controller.signal.aborted) return;
      if (response.available === false) { setStatsError(true); return; }
      setStats(response); setUpdatedAt(new Date()); setStatsError(false);
    } catch { if (active.current && !controller.signal.aborted) setStatsError(true); }
    finally { if (statsRequest.current === controller) statsRequest.current = null; if (active.current && !controller.signal.aborted) setStatsLoading(false); }
  }, []);
  React.useEffect(() => {
    active.current = true;
    const refresh = () => { void refreshStatus(); void refreshStats(); };
    const visibleRefresh = () => { if (document.visibilityState === 'visible') refresh(); };
    refresh();
    const statusTimer = window.setInterval(() => { if (document.visibilityState === 'visible') void refreshStatus(); }, 5000);
    const statsTimer = window.setInterval(() => { if (document.visibilityState === 'visible') void refreshStats(); }, 30000);
    window.addEventListener('verydisco-data-changed', refresh); window.addEventListener('focus', visibleRefresh);
    document.addEventListener('visibilitychange', visibleRefresh);
    return () => {
      active.current = false; statusRequest.current?.abort(); statsRequest.current?.abort(); statusRequest.current = null; statsRequest.current = null;
      window.clearInterval(statusTimer); window.clearInterval(statsTimer);
      window.removeEventListener('verydisco-data-changed', refresh); window.removeEventListener('focus', visibleRefresh); document.removeEventListener('visibilitychange', visibleRefresh);
    };
  }, [refreshStatus, refreshStats]);

  const playlists = Array.from(new Set([...SOURCES, ...(status?.active_playlists || []), ...Object.keys(status?.latest_runs || {})]));
  const runs = Object.entries(status?.latest_runs || {}).filter((entry): entry is [string, RunRecord] => entry[1] !== null);
  const totals = runs.reduce((sum, [, run]) => ({ found: sum.found + (run.tracks_found || 0), added: sum.added + (run.tracks_downloaded || 0), failed: sum.failed + (run.tracks_failed || 0) }), { found: 0, added: 0, failed: 0 });
  const syncing = Boolean(status?.is_syncing);
  const sync = async (source: string) => {
    if (actionPending.current) return;
    actionPending.current = true; setAction(source);
    try { await apiService.triggerSyncForSource(source); if (active.current) { setNotice({ text: `${sourceName(source)} sync queued`, type: 'success' }); await refreshStatus(); } }
    catch { if (active.current) setNotice({ text: `Could not start ${sourceName(source)}. Try again.`, type: 'error' }); }
    finally { actionPending.current = false; if (active.current) setAction(null); }
  };
  const readiness = error ? 'Connection interrupted' : !status?.is_configured ? 'Setup required' : syncing ? 'Sync in progress' : 'Ready to discover';
  const latest = [...runs].sort((a, b) => (Date.parse(b[1].timestamp) || 0) - (Date.parse(a[1].timestamp) || 0)).slice(0, 3);
  if (loading && !status) return <Box role="status" aria-label="Loading dashboard"><Skeleton height={260} variant="rounded" sx={{ mb: 3 }} /><Grid container spacing={2}>{[0, 1, 2].map(item => <Grid item xs={12} sm={4} key={item}><Skeleton height={150} variant="rounded" /></Grid>)}</Grid></Box>;

  return <Box sx={{ maxWidth: 1440, mx: 'auto' }}>
    {error && <Alert severity="error" sx={{ mb: 2 }} action={<Button color="inherit" onClick={() => void refreshStatus()}>Retry</Button>}>{error}</Alert>}
    {status && !status.is_configured && <Alert severity="warning" sx={{ mb: 2 }} action={<Button color="inherit" onClick={onNavigateToConfig}>Configure</Button>}><AlertTitle>Finish your setup</AlertTitle>Connect your providers before starting a sync.</Alert>}
    {notice && <Alert severity={notice.type} onClose={() => setNotice(null)} sx={{ mb: 2 }}>{notice.text}</Alert>}
    <Card sx={theme => ({ mb: 3, position: 'relative', overflow: 'hidden', background: theme.palette.mode === 'dark' ? 'linear-gradient(115deg, #221c38 0%, #171b29 65%, #17282b)' : 'linear-gradient(115deg, #f0eaff, #ffffff 65%, #e7f7f2)', borderColor: alpha(theme.palette.primary.main, .2) })}>
      <Box aria-hidden="true" sx={{ position: 'absolute', width: 330, height: 330, right: -55, top: -140, borderRadius: '50%', border: '36px solid rgba(160,136,255,.05)', outline: '1px solid rgba(160,136,255,.1)', boxShadow: '0 0 0 36px rgba(160,136,255,.025)', pointerEvents: 'none' }} />
      <CardContent sx={{ p: { xs: 3, sm: 4.5 } }}><Stack direction={{ xs: 'column', md: 'row' }} justifyContent="space-between" alignItems={{ xs: 'stretch', md: 'center' }} gap={3}>
        <Box><Typography variant="overline" color="primary.main">THE SOUND OF YOUR COLLECTION</Typography><Typography component="h1" variant="h3" sx={{ fontSize: { xs: '2.15rem', sm: '3.15rem' }, mt: 1, maxWidth: 650 }}>Your music, in motion.</Typography><Typography color="text.secondary" sx={{ mt: 1.25, maxWidth: 530 }}>Fresh discoveries. A beautifully organised library. More time for the music you love.</Typography>
          <Stack direction="row" alignItems="center" gap={1.5} flexWrap="wrap" mt={2.5}><Chip size="small" variant="outlined" icon={syncing ? <SyncRounded /> : error ? <WarningAmberRounded /> : <CheckRounded />} color={error ? 'warning' : syncing ? 'primary' : 'default'} label={readiness} />{status?.next_run && <Typography variant="caption" color="text.secondary">Next sync · {dateLabel(status.next_run)}</Typography>}</Stack>
        </Box><Stack gap={1} sx={{ minWidth: { md: 170 }, flexShrink: 0 }}><Button variant="contained" size="large" startIcon={action ? <CircularProgress size={18} color="inherit" /> : <SyncRounded />} disabled={!status?.is_configured || !!error || syncing || action !== null} onClick={() => void sync(status?.active_playlists?.[0] || SOURCES[0])}>Sync now</Button><Button onClick={() => go('explore')} endIcon={<ArrowForwardRounded />} sx={{ color: 'text.secondary' }}>Explore your picks</Button></Stack>
      </Stack></CardContent>
    </Card>
    <Grid container spacing={2} sx={{ mb: 3 }}>
      <Grid item xs={12} sm={4}><Metric label="Tracks discovered" value={status ? totals.found : null} icon={<GraphicEqRounded fontSize="small" />} color="#a58bea" caption="Across the latest run of each source" /></Grid>
      <Grid item xs={12} sm={4}><Metric label="Tracks added" value={status ? totals.added : null} icon={<LibraryMusicRounded fontSize="small" />} color="#43b99a" caption="Downloads from those source runs" /></Grid>
      <Grid item xs={12} sm={4}><Metric label="Needs attention" value={status ? totals.failed : null} icon={<WarningAmberRounded fontSize="small" />} color="#c8983b" caption="Failed tracks in those source runs" /></Grid>
    </Grid>
    <Grid container spacing={3}>
      <Grid item xs={12} lg={8}><Card sx={{ height: '100%' }}><CardContent sx={{ p: { xs: 2.5, sm: 3 } }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" gap={2} mb={2.5}><Box><Typography variant="overline" color="text.secondary">YOUR DISCOVERY ROUTINE</Typography><Typography variant="h5">Sync sources</Typography></Box><Chip variant="outlined" size="small" label={`${status?.active_playlists?.length || 0} automatic`} /></Stack>
        <Stack spacing={1.5}>{playlists.map((source, index) => {
          const run = status?.latest_runs?.[source]; const progress = syncProgress(run); const automatic = status?.active_playlists?.includes(source);
          return <Box key={source} sx={{ border: '1px solid', borderColor: 'divider', p: { xs: 1.75, sm: 2 }, borderRadius: 3 }}>
            <Stack direction="row" alignItems="center" gap={1.5} mb={1.75}><Avatar variant="rounded" sx={{ width: 42, height: 42, borderRadius: 2.5, bgcolor: index === 2 ? 'rgba(87,191,172,.1)' : 'action.selected', color: index === 2 ? 'secondary.main' : 'primary.main' }}>{index === 2 ? <ExploreRounded /> : <GraphicEqRounded />}</Avatar><Box sx={{ flex: 1, minWidth: 0 }}><Typography fontWeight={700}>{sourceName(source)}</Typography><Typography variant="caption" color="text.secondary">{run ? dateLabel(run.timestamp) : 'Your first discovery is waiting'} · {automatic ? 'Automatic' : 'Manual only'}</Typography></Box>
              <Button size="small" variant="outlined" aria-label={`Sync ${sourceName(source)}`} disabled={!status?.is_configured || !!error || syncing || action !== null} onClick={() => void sync(source)} sx={{ minWidth: 40, px: 1.25 }}>{action === source ? <CircularProgress size={17} /> : <SyncRounded fontSize="small" />}</Button>
            </Stack><LinearProgress aria-label={`${sourceName(source)} completion`} variant="determinate" value={progress.percent} color={run?.tracks_failed || run?.status === 'failed' ? 'warning' : 'primary'} sx={{ height: 5, borderRadius: 5, bgcolor: 'action.selected' }} />
            <Stack direction="row" justifyContent="space-between" alignItems="center" gap={1} mt={1.25} flexWrap="wrap"><Typography variant="caption" color="text.secondary">{run ? `${run.tracks_downloaded || 0} added · ${run.tracks_skipped || 0} skipped${run.tracks_failed ? ` · ${run.tracks_failed} failed` : ''}` : 'No recorded run'}</Typography><Typography variant="caption" color="text.secondary">{run ? `${progress.percent}% processed` : 'Not started'}</Typography></Stack>
          </Box>;
        })}</Stack>
      </CardContent></Card></Grid>
      <Grid item xs={12} lg={4}><Stack spacing={3} sx={{ height: '100%' }}>
        <Card sx={{ flex: 1 }}><CardContent sx={{ p: { xs: 2.5, sm: 3 } }}><Stack direction="row" alignItems="center" justifyContent="space-between" mb={2.5}><Box><Typography variant="overline" color="text.secondary">THE BIGGER PICTURE</Typography><Typography variant="h5">Library pulse</Typography></Box><Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: statsError || !stats ? 'text.disabled' : 'success.main' }} aria-label={statsError ? 'Statistics unavailable' : stats ? 'Statistics loaded' : 'Loading statistics'} /></Stack>
          {statsError && <Alert severity="warning" sx={{ mb: 2 }}>{stats ? 'Showing the last successful snapshot. Navidrome could not be refreshed.' : 'Navidrome statistics are unavailable, not zero. Check your connection.'}</Alert>}
          <Stack spacing={2}>{[['Tracks', stats?.songs, <LibraryMusicRounded />], ['Albums', stats?.albums, <AlbumRounded />], ['Artists', stats?.artists, <PeopleRounded />]].map(([label, value, icon]) => <Stack key={String(label)} direction="row" alignItems="center" gap={1.5}><Box sx={{ color: 'text.secondary', display: 'flex' }}>{icon}</Box><Typography variant="body2" color="text.secondary" sx={{ flex: 1 }}>{label}</Typography>{statsLoading ? <Skeleton width={50} /> : <Typography variant="h6" sx={{ fontVariantNumeric: 'tabular-nums' }}>{typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : '—'}</Typography>}</Stack>)}</Stack>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 2.5 }}>{updatedAt ? `Navidrome snapshot · ${updatedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : 'Waiting for a verified Navidrome snapshot'}</Typography>
        </CardContent></Card>
        <Card><CardContent sx={{ p: { xs: 2.5, sm: 3 } }}><Typography variant="h6" mb={1.5}>Make it yours</Typography>{[['search', 'Find your next favourite', <SearchRounded />], ['library-manager', 'Browse your collection', <LibraryMusicRounded />], ['metadata-rebuild', 'Fine-tune your metadata', <GraphicEqRounded />]].map(([id, title, icon]) => <Button key={String(id)} fullWidth startIcon={icon} endIcon={<ArrowForwardRounded fontSize="small" />} onClick={() => go(String(id))} sx={{ justifyContent: 'flex-start', alignItems: 'center', color: 'text.secondary', px: 1.25, py: 1.25, minWidth: 0, height: 'auto', '& .MuiButton-startIcon': { flexShrink: 0, ml: 0 }, '& .MuiButton-endIcon': { ml: 1, flexShrink: 0 } }}><Box component="span" sx={{ flex: 1, minWidth: 0, textAlign: 'left', whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{title}</Box></Button>)}</CardContent></Card>
      </Stack></Grid>
    </Grid>
    <Card sx={{ mt: 3 }}><CardContent sx={{ p: { xs: 2.5, sm: 3 } }}><Stack direction="row" justifyContent="space-between" alignItems="center" mb={1.5}><Box><Typography variant="h5">Recent source activity</Typography><Typography variant="body2" color="text.secondary">Latest recorded run for each source</Typography></Box><Button size="small" onClick={() => go('history')} endIcon={<ArrowForwardRounded />}>History</Button></Stack>
      {latest.length ? latest.map(([source, run]) => <Stack key={source} direction="row" gap={1.5} alignItems="center" sx={{ py: 1.5, borderTop: '1px solid', borderColor: 'divider' }}><Avatar sx={{ width: 32, height: 32, bgcolor: 'action.hover', color: run.status === 'failed' || run.tracks_failed ? 'warning.main' : 'secondary.main' }}>{run.status === 'failed' || run.tracks_failed ? <WarningAmberRounded fontSize="small" /> : <CheckRounded fontSize="small" />}</Avatar><Box sx={{ flex: 1 }}><Typography variant="body2" fontWeight={650}>{sourceName(source)}</Typography><Typography variant="caption" color="text.secondary">{run.status} · {run.tracks_downloaded || 0} added · {run.tracks_failed || 0} failed</Typography></Box><Typography variant="caption" color="text.secondary">{dateLabel(run.timestamp)}</Typography></Stack>) : <Typography color="text.secondary" sx={{ py: 2 }}>Your sync history will appear here after your first run.</Typography>}
    </CardContent></Card>
  </Box>;
}
