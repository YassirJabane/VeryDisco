import React, { useState, useCallback } from 'react';
import {
  Box, Typography, Button, Chip, CircularProgress, Alert,
  Card, CardContent, CardActions, Divider, Grid, Tooltip,
  IconButton, Collapse, LinearProgress, Stack, Paper,
} from '@mui/material';
import {
  HealthAndSafety as HealthIcon,
  ContentCopy as DupTrackIcon,
  FolderCopy as SplitAlbumIcon,
  ImageNotSupported as NoCoverIcon,
  ManageSearch as MissingMetaIcon,
  Refresh as RefreshIcon,
  Build as FixIcon,
  VisibilityOff as IgnoreIcon,
  ExpandMore as ExpandIcon,
  ExpandLess as CollapseIcon,
  CheckCircle as OkIcon,
  Warning as WarnIcon,
  DriveFileMove as MisfiledIcon,
  Article as LyricsIcon,
  Fingerprint as FingerprintIcon,
} from '@mui/icons-material';
import apiService, { MaintenanceIssue, AcoustidStats } from '../api';

// ── helpers ───────────────────────────────────────────────────────────────────
const severityColor = (s: string): 'error' | 'warning' | 'info' => {
  if (s === 'high') return 'error';
  if (s === 'medium') return 'warning';
  return 'info';
};

const typeIcon: Record<string, React.ReactNode> = {
  duplicate_track: <DupTrackIcon fontSize="small" />,
  split_album: <SplitAlbumIcon fontSize="small" />,
  missing_cover: <NoCoverIcon fontSize="small" />,
  missing_metadata: <MissingMetaIcon fontSize="small" />,
  misfiled_tracks: <MisfiledIcon fontSize="small" />,
  orphaned_lyrics: <LyricsIcon fontSize="small" />,
  dirty_metadata: <MissingMetaIcon fontSize="small" />,
};

const typeLabel: Record<string, string> = {
  duplicate_track: 'Duplicate Track',
  split_album: 'Split Album',
  missing_cover: 'Missing Cover',
  missing_metadata: 'Missing Metadata',
  misfiled_tracks: 'Misfiled Tracks',
  orphaned_lyrics: 'Orphaned Lyrics',
  dirty_metadata: 'Dirty/Split Metadata',
};

const typeColor: Record<string, string> = {
  duplicate_track: '#f44336',
  split_album: '#ff9800',
  missing_cover: '#2196f3',
  missing_metadata: '#9c27b0',
  misfiled_tracks: '#e91e63',
  orphaned_lyrics: '#00bcd4',
  dirty_metadata: '#4caf50',
};

// ── Summary card ──────────────────────────────────────────────────────────────
const SummaryCard: React.FC<{
  label: string; count: number; icon: React.ReactNode; color: string;
}> = ({ label, count, icon, color }) => (
  <Paper
    elevation={0}
    sx={{
      p: 2.5, borderRadius: 3, border: '1px solid',
      borderColor: count > 0 ? `${color}44` : 'divider',
      background: count > 0
        ? `linear-gradient(135deg, ${color}18 0%, ${color}08 100%)`
        : 'background.paper',
      transition: 'all .2s',
    }}
  >
    <Stack direction="row" alignItems="center" spacing={1.5}>
      <Box sx={{ color, display: 'flex' }}>{icon}</Box>
      <Box>
        <Typography variant="h5" fontWeight={800} sx={{ color: count > 0 ? color : 'text.primary', lineHeight: 1 }}>
          {count}
        </Typography>
        <Typography variant="caption" color="text.secondary" fontWeight={600}>
          {label}
        </Typography>
      </Box>
    </Stack>
  </Paper>
);

// ── Issue card ────────────────────────────────────────────────────────────────
const IssueCard: React.FC<{
  issue: MaintenanceIssue;
  onFix: (issue: MaintenanceIssue, actionIdx: number) => Promise<void>;
  onIgnore: (issue: MaintenanceIssue) => Promise<void>;
  fixing: boolean;
}> = ({ issue, onFix, onIgnore, fixing }) => {
  const [expanded, setExpanded] = useState(false);
  const [preview, setPreview] = useState<any>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  React.useEffect(() => {
    if (expanded && (issue.type === 'dirty_metadata' || issue.type === 'missing_metadata') && !preview && !previewLoading) {
      setPreviewLoading(true);
      apiService.previewMaintenanceFix(issue.type, issue.target_path)
        .then(res => setPreview(res))
        .catch(() => {})
        .finally(() => setPreviewLoading(false));
    }
  }, [expanded, issue.type, issue.target_path, preview, previewLoading]);

  return (
    <Card
      elevation={0}
      sx={{
        border: '1px solid',
        borderColor: 'divider',
        borderLeft: `4px solid ${typeColor[issue.type] ?? '#999'}`,
        borderRadius: 2,
        transition: 'box-shadow .2s',
        '&:hover': { boxShadow: 4 },
      }}
    >
      <CardContent sx={{ pb: 1 }}>
        <Stack direction="row" alignItems="flex-start" spacing={1.5}>
          <Box sx={{ color: typeColor[issue.type], mt: 0.3 }}>{typeIcon[issue.type]}</Box>
          <Box flex={1}>
            <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" mb={0.5}>
              <Chip
                label={typeLabel[issue.type]}
                size="small"
                sx={{
                  bgcolor: `${typeColor[issue.type]}22`,
                  color: typeColor[issue.type],
                  fontWeight: 700,
                  fontSize: '0.7rem',
                }}
              />
              <Chip
                label={issue.severity.toUpperCase()}
                size="small"
                color={severityColor(issue.severity)}
                variant="outlined"
                sx={{ fontWeight: 700, fontSize: '0.65rem' }}
              />
            </Stack>
            <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
              {issue.title}
            </Typography>
            <Collapse in={expanded}>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', display: 'block', mt: 0.5 }}
              >
                {issue.description}
              </Typography>
              {preview && (
                <Box sx={{ mt: 1.5, p: 1.5, borderRadius: 2, bgcolor: '#121118', border: '1px solid', borderColor: 'divider' }}>
                  <Typography variant="caption" sx={{ fontWeight: 800, color: 'primary.main', display: 'block', mb: 1 }}>
                    PROPOSED FIX (DEEZER):
                  </Typography>
                  <Grid container spacing={1.5}>
                    {Object.entries(preview.proposed).map(([key, val]) => (
                      <Grid item xs={6} key={key}>
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontSize: '0.7rem' }}>
                          {key}
                        </Typography>
                        <Typography variant="caption" fontWeight={600} sx={{ fontSize: '0.75rem', color: '#e5e5e9' }}>
                          {val as string}
                        </Typography>
                      </Grid>
                    ))}
                  </Grid>
                </Box>
              )}
              {previewLoading && (
                <Box display="flex" alignItems="center" gap={1} sx={{ mt: 1.5 }}>
                  <CircularProgress size={12} color="primary" />
                  <Typography variant="caption" color="text.secondary">Fetching official metadata from Deezer...</Typography>
                </Box>
              )}
            </Collapse>
          </Box>
          <Tooltip title={expanded ? 'Hide details' : 'Show details'}>
            <IconButton size="small" onClick={() => setExpanded(v => !v)}>
              {expanded ? <CollapseIcon fontSize="small" /> : <ExpandIcon fontSize="small" />}
            </IconButton>
          </Tooltip>
        </Stack>
      </CardContent>
      <CardActions sx={{ px: 2, pb: 1.5, gap: 1, flexWrap: 'wrap' }}>
        {issue.actions.map((a, idx) => (
          <Button
            key={idx}
            size="small"
            variant="contained"
            startIcon={fixing ? <CircularProgress size={12} color="inherit" /> : <FixIcon fontSize="small" />}
            disabled={fixing}
            onClick={() => onFix(issue, idx)}
            sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600, fontSize: '0.75rem' }}
          >
            {a.label}
          </Button>
        ))}
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          startIcon={<IgnoreIcon fontSize="small" />}
          disabled={fixing}
          onClick={() => onIgnore(issue)}
          sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600, fontSize: '0.75rem', opacity: 0.7 }}
        >
          Ignore
        </Button>
      </CardActions>
    </Card>
  );
};

// ── Main component ─────────────────────────────────────────────────────────────
const ServerHealth: React.FC = () => {
  const [issues, setIssues] = useState<MaintenanceIssue[]>([]);
  const [loading, setLoading] = useState(false);
  const [scanned, setScanned] = useState(false);
  const [fixingId, setFixingId] = useState<string | null>(null);
  const [fixingAll, setFixingAll] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);

  const showToast = (msg: string, type: 'success' | 'error' = 'success') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 5000);
  };

  const runScan = useCallback(async (forceScan: boolean = false) => {
    setLoading(true);
    setScanned(false);
    try {
      if (forceScan) {
        await apiService.triggerLibraryScan();
      }
      const result = await apiService.scanMaintenanceIssues(forceScan);
      setIssues(result);
      setScanned(true);
    } catch {
      showToast(forceScan ? 'Scan failed. Is the backend running?' : 'Failed to load cached health issues.', 'error');
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    runScan(false);
  }, [runScan]);

  const handleFixAll = async () => {
    if (issues.length === 0) return;
    setFixingAll(true);
    
    // Fire all requests concurrently and wait for all to settle
    const promises = issues.map(async (issue) => {
      if (issue.actions && issue.actions.length > 0) {
        const action = issue.actions[0];
        try {
          await apiService.fixMaintenanceIssue(issue.type, issue.target_path, action.action, action.params);
          return { id: issue.id, success: true };
        } catch {
          return { id: issue.id, success: false };
        }
      }
      return { id: issue.id, success: false };
    });

    const results = await Promise.all(promises);
    const succeededIds = results.filter(r => r.success).map(r => r.id);
    
    // Batch update the UI so they all disappear at once
    setIssues(prev => prev.filter(i => !succeededIds.includes(i.id)));
    setFixingAll(false);
    
    const successCount = succeededIds.length;
    const failCount = results.length - successCount;
    if (failCount > 0) {
      showToast(`Fixed ${successCount} issues, ${failCount} failed.`, 'error');
    } else {
      showToast(`Successfully fixed all ${successCount} issues!`);
    }
  };

  const handleFix = async (issue: MaintenanceIssue, actionIdx: number) => {
    const action = issue.actions[actionIdx];
    setFixingId(issue.id);
    try {
      await apiService.fixMaintenanceIssue(issue.type, issue.target_path, action.action, action.params);
      setIssues(prev => prev.filter(i => i.id !== issue.id));
      showToast(`Fixed: ${issue.title}`);
    } catch (e: any) {
      showToast(e?.response?.data?.detail ?? 'Fix failed.', 'error');
    } finally {
      setFixingId(null);
    }
  };

  const handleIgnore = async (issue: MaintenanceIssue) => {
    setFixingId(issue.id);
    try {
      await apiService.ignoreMaintenanceIssue(issue.type, issue.target_path);
      setIssues(prev => prev.filter(i => i.id !== issue.id));
      showToast('Issue silenced.');
    } catch {
      showToast('Failed to ignore issue.', 'error');
    } finally {
      setFixingId(null);
    }
  };

  const counts = {
    duplicate_track: issues.filter(i => i.type === 'duplicate_track').length,
    split_album: issues.filter(i => i.type === 'split_album').length,
    missing_cover: issues.filter(i => i.type === 'missing_cover').length,
    missing_metadata: issues.filter(i => i.type === 'missing_metadata').length,
    misfiled_tracks: issues.filter(i => i.type === 'misfiled_tracks').length,
    orphaned_lyrics: issues.filter(i => i.type === 'orphaned_lyrics').length,
    dirty_metadata: issues.filter(i => i.type === 'dirty_metadata').length,
  };

  const totalIssues = issues.length;

  const ordered: MaintenanceIssue[] = [
    ...issues.filter(i => i.type === 'misfiled_tracks'),
    ...issues.filter(i => i.type === 'orphaned_lyrics'),
    ...issues.filter(i => i.type === 'dirty_metadata'),
    ...issues.filter(i => i.type === 'split_album'),
    ...issues.filter(i => i.type === 'duplicate_track'),
    ...issues.filter(i => i.type === 'missing_cover'),
    ...issues.filter(i => i.type === 'missing_metadata'),
  ];

  return (
    <Box>
      <Stack direction="row" alignItems="flex-start" spacing={1.5} mb={3}>
        <HealthIcon sx={{ color: 'primary.main', fontSize: 32, mt: 0.5 }} />
        <Box sx={{ flex: 1 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center">
            <Box>
              <Typography variant="h5" fontWeight={800}>Server Health</Typography>
              <Typography variant="body2" color="text.secondary" mb={2}>
                Scan your library for problems and fix them in one click
              </Typography>
            </Box>
            <Stack direction="row" spacing={1.5}>
              {scanned && issues.length > 0 && (
                <Button
                  variant="contained"
                  color="success"
                  disabled={loading || fixingAll}
                  onClick={handleFixAll}
                  sx={{ borderRadius: 2.5, fontWeight: 700, textTransform: 'none', px: 2.5, py: 1 }}
                >
                  {fixingAll ? 'Fixing all…' : 'Fix All Issues'}
                </Button>
              )}
              <Button
                variant="contained"
                startIcon={loading ? <CircularProgress size={16} color="inherit" /> : <RefreshIcon />}
                disabled={loading || fixingAll}
                onClick={() => runScan(true)}
                sx={{ borderRadius: 2.5, fontWeight: 700, textTransform: 'none', px: 2.5, py: 1 }}
              >
                {loading ? 'Scanning…' : scanned ? 'Re-scan' : 'Run Scan'}
              </Button>
            </Stack>
          </Stack>
        </Box>
      </Stack>

      {(loading || fixingAll) && <LinearProgress sx={{ borderRadius: 4, mb: 3 }} />}

      {toast && (
        <Alert severity={toast.type} sx={{ mb: 2, borderRadius: 2 }} onClose={() => setToast(null)}>
          {toast.msg}
        </Alert>
      )}

      {scanned && (
        <>
          <Grid container spacing={{ xs: 1.5, sm: 2 }} mb={3}>
            <Grid item xs={6} sm={2}>
              <SummaryCard label="Misfiled Tracks" count={counts.misfiled_tracks} icon={<MisfiledIcon />} color="#e91e63" />
            </Grid>
            <Grid item xs={6} sm={2}>
              <SummaryCard label="Orphaned Lyrics" count={counts.orphaned_lyrics} icon={<LyricsIcon />} color="#00bcd4" />
            </Grid>
            <Grid item xs={6} sm={2}>
              <SummaryCard label="Split Albums" count={counts.split_album} icon={<SplitAlbumIcon />} color="#ff9800" />
            </Grid>
            <Grid item xs={6} sm={2}>
              <SummaryCard label="Duplicate Tracks" count={counts.duplicate_track} icon={<DupTrackIcon />} color="#f44336" />
            </Grid>
            <Grid item xs={6} sm={2}>
              <SummaryCard label="Missing Covers" count={counts.missing_cover} icon={<NoCoverIcon />} color="#2196f3" />
            </Grid>
            <Grid item xs={6} sm={2}>
              <SummaryCard label="Missing Metadata" count={counts.missing_metadata} icon={<MissingMetaIcon />} color="#9c27b0" />
            </Grid>
          </Grid>

          {totalIssues === 0 ? (
            <Paper
              elevation={0}
              sx={{ p: 6, textAlign: 'center', border: '1px solid', borderColor: 'divider', borderRadius: 3 }}
            >
              <OkIcon sx={{ fontSize: 56, color: 'success.main', mb: 1 }} />
              <Typography variant="h6" fontWeight={700} color="success.main">All clear!</Typography>
              <Typography variant="body2" color="text.secondary">No issues found in your library.</Typography>
            </Paper>
          ) : (
            <>
              <Stack direction="row" alignItems="center" spacing={1} mb={2}>
                <WarnIcon color="warning" />
                <Typography variant="subtitle1" fontWeight={700}>
                  {totalIssues} issue{totalIssues !== 1 ? 's' : ''} found
                </Typography>
              </Stack>
              <Stack spacing={1.5}>
                {ordered.map(issue => (
                  <IssueCard
                    key={issue.id}
                    issue={issue}
                    onFix={handleFix}
                    onIgnore={handleIgnore}
                    fixing={fixingId === issue.id}
                  />
                ))}
              </Stack>
            </>
          )}
        </>
      )}

      {!scanned && !loading && (
        <Paper
          elevation={0}
          sx={{ p: 8, textAlign: 'center', border: '1px dashed', borderColor: 'divider', borderRadius: 3 }}
        >
          <HealthIcon sx={{ fontSize: 64, color: 'text.disabled', mb: 2 }} />
          <Typography variant="h6" color="text.secondary" fontWeight={600} mb={1}>
            Library not scanned yet
          </Typography>
          <Typography variant="body2" color="text.disabled">
            Click <strong>Run Scan</strong> to detect duplicate tracks, split albums, missing cover art, and incomplete metadata.
          </Typography>
        </Paper>
      )}
    </Box>
  );
};

export default ServerHealth;
