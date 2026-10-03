import React from 'react';
import {
  Alert, Accordion, AccordionDetails, AccordionSummary, Box, Button, Chip,
  CircularProgress, FormControlLabel, LinearProgress, MenuItem, Paper, Select,
  Stack, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import {
  AutoFixHigh as RebuildIcon, ExpandMore as ExpandIcon, Restore as RestoreIcon,
  Search as ScanIcon,
} from '@mui/icons-material';
import apiService, { getErrorMessage } from '../api';

const fields = ['title', 'artist', 'album', 'album_artist', 'date', 'track', 'disc'];

const MetadataRebuild: React.FC = () => {
  const [status, setStatus] = React.useState<any>({ status: 'idle', processed: 0, total: 0 });
  const [albums, setAlbums] = React.useState<any[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [verifyAcoustid, setVerifyAcoustid] = React.useState(false);
  const [includeArtwork, setIncludeArtwork] = React.useState(true);
  const [allowItunes, setAllowItunes] = React.useState(true);
  const [manualMbids, setManualMbids] = React.useState<Record<string, string>>({});
  const [message, setMessage] = React.useState<{ type: 'success' | 'error' | 'warning'; text: string } | null>(null);

  const refresh = React.useCallback(async () => {
    try {
      const [nextStatus, nextAlbums] = await Promise.all([
        apiService.getMetadataRebuildStatus(),
        apiService.getMetadataRebuildAlbums(),
      ]);
      setStatus(nextStatus);
      setAlbums(nextAlbums);
      return nextStatus;
    } catch (error) {
      setMessage({ type: 'error', text: getErrorMessage(error, 'Unable to load metadata rebuild state.') });
    }
  }, []);

  React.useEffect(() => { refresh(); }, [refresh]);
  React.useEffect(() => {
    if (!['queued', 'scanning', 'applying'].includes(status.status)) return;
    const timer = window.setInterval(refresh, 2500);
    return () => window.clearInterval(timer);
  }, [status.status, refresh]);

  const startScan = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await apiService.startMetadataRebuildScan(verifyAcoustid);
      setStatus({ status: 'scanning', processed: 0, total: 0, message: 'Starting read-only inventory…' });
    } catch (error) {
      setMessage({ type: 'error', text: getErrorMessage(error, 'Scan could not be started.') });
    } finally {
      setBusy(false);
    }
  };

  const chooseRelease = async (planId: string, releaseMbid: string) => {
    setBusy(true);
    try {
      await apiService.selectMetadataRelease(planId, releaseMbid);
      await refresh();
    } catch (error) {
      setMessage({ type: 'error', text: getErrorMessage(error, 'Release selection failed.') });
    } finally { setBusy(false); }
  };

  const apply = async (plan: any) => {
    if (!window.confirm(`Apply the reviewed release metadata to all ${plan.total_tracks} tracks in “${plan.album}”? A rollback backup will be created.`)) return;
    setBusy(true);
    try {
      const result = await apiService.applyMetadataAlbum(plan.id, includeArtwork, allowItunes);
      setMessage({ type: 'success', text: `Updated ${result.updated_tracks} tracks. Artwork: ${result.artwork_source || 'preserved existing'}.` });
      await refresh();
    } catch (error) {
      setMessage({ type: 'error', text: getErrorMessage(error, 'Album update failed; written files were rolled back.') });
    } finally { setBusy(false); }
  };

  const rollback = async (plan: any) => {
    if (!window.confirm(`Restore the metadata backup for “${plan.album}”?`)) return;
    setBusy(true);
    try {
      const result = await apiService.rollbackMetadataAlbum(plan.id);
      setMessage({ type: 'success', text: `Restored ${result.restored_tracks} tracks.` });
      await refresh();
    } catch (error) {
      setMessage({ type: 'error', text: getErrorMessage(error, 'Rollback failed.') });
    } finally { setBusy(false); }
  };

  const progress = status.total ? Math.round((status.processed / status.total) * 100) : 0;
  const automatic = albums.filter(a => a.review === 'automatic' && a.state === 'planned').length;
  const review = albums.filter(a => a.review === 'required' || a.review === 'selected').length;
  const unmatched = albums.filter(a => a.review === 'unmatched').length;

  return (
    <Box>
      <Stack direction={{ xs: 'column', md: 'row' }} justifyContent="space-between" gap={2} mb={3}>
        <Box>
          <Stack direction="row" alignItems="center" spacing={1.5}>
            <RebuildIcon color="primary" sx={{ fontSize: 34 }} />
            <Typography variant="h5" fontWeight={800}>Metadata Rebuild</Typography>
          </Stack>
          <Typography variant="body2" color="text.secondary" mt={1}>
            Release-centric reconstruction using MusicBrainz, Cover Art Archive and optional AcoustID verification. Deezer is not used.
          </Typography>
        </Box>
        <Button variant="contained" startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <ScanIcon />}
          disabled={busy || ['scanning', 'queued'].includes(status.status)} onClick={startScan}>
          Scan entire library
        </Button>
      </Stack>

      {message && <Alert severity={message.type} sx={{ mb: 2 }} onClose={() => setMessage(null)}>{message.text}</Alert>}
      <Alert severity="info" sx={{ mb: 2 }}>
        Scans never write media files. Apply is available per album only after every local track is matched; each apply creates a rollback backup.
      </Alert>

      <Paper variant="outlined" sx={{ p: 2.5, mb: 2.5, borderRadius: 3 }}>
        <Stack direction={{ xs: 'column', md: 'row' }} gap={2} justifyContent="space-between">
          <Stack direction="row" gap={1} flexWrap="wrap">
            <Chip label={`${automatic} automatic`} color="success" variant="outlined" />
            <Chip label={`${review} review`} color="warning" variant="outlined" />
            <Chip label={`${unmatched} unmatched`} color="error" variant="outlined" />
          </Stack>
          <Stack direction={{ xs: 'column', sm: 'row' }} gap={1}>
            <FormControlLabel control={<Switch checked={verifyAcoustid} onChange={e => setVerifyAcoustid(e.target.checked)} />} label="Verify recordings with AcoustID" />
            <FormControlLabel control={<Switch checked={includeArtwork} onChange={e => setIncludeArtwork(e.target.checked)} />} label="Replace artwork" />
            <FormControlLabel control={<Switch checked={allowItunes} onChange={e => setAllowItunes(e.target.checked)} disabled={!includeArtwork} />} label="Verified iTunes fallback" />
          </Stack>
        </Stack>
        {['scanning', 'queued'].includes(status.status) && <Box mt={2}>
          <LinearProgress variant={status.total ? 'determinate' : 'indeterminate'} value={progress} />
          <Typography variant="caption" color="text.secondary">{status.message} {status.total ? `(${status.processed}/${status.total})` : ''}</Typography>
        </Box>}
      </Paper>

      <Stack spacing={1.5}>
        {albums.map(plan => {
          const release = plan.selected_release;
          const fullyMatched = plan.matched_tracks === plan.total_tracks && !!release;
          return <Accordion key={plan.id} disableGutters sx={{ border: '1px solid', borderColor: 'divider', borderRadius: '12px !important' }}>
            <AccordionSummary expandIcon={<ExpandIcon />}>
              <Stack direction={{ xs: 'column', sm: 'row' }} alignItems={{ sm: 'center' }} gap={1.5} width="100%">
                <Box flex={1}>
                  <Typography fontWeight={750}>{plan.album_artist} — {plan.album}</Typography>
                  <Typography variant="caption" color="text.secondary">{plan.folder} · {plan.matched_tracks}/{plan.total_tracks} tracks matched</Typography>
                </Box>
                <Chip size="small" label={`${Math.round(plan.confidence * 100)}%`} color={plan.confidence >= .9 ? 'success' : plan.confidence >= .6 ? 'warning' : 'error'} />
                <Chip size="small" label={plan.state} variant="outlined" />
              </Stack>
            </AccordionSummary>
            <AccordionDetails>
              <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} mb={2}>
                {release?.artwork_url && <Box component="img" src={release.artwork_url} alt="Candidate cover" sx={{ width: 150, height: 150, objectFit: 'cover', borderRadius: 2, bgcolor: 'action.hover' }} />}
                <Box flex={1}>
                  <Typography variant="subtitle2" mb={1}>Selected MusicBrainz release</Typography>
                  <Select size="small" fullWidth value={release?.release_mbid || ''} displayEmpty
                    onChange={e => chooseRelease(plan.id, String(e.target.value))} disabled={busy}>
                    {!release && <MenuItem value="" disabled>No confident release selected</MenuItem>}
                    {plan.candidates.map((candidate: any) => <MenuItem key={candidate.release_mbid} value={candidate.release_mbid}>
                      {candidate.album_artist} — {candidate.album} · {candidate.date || 'unknown date'} · {candidate.country || 'unknown country'} · {candidate.track_total} tracks
                    </MenuItem>)}
                  </Select>
                  <Stack direction={{ xs: 'column', sm: 'row' }} gap={1} mt={1}>
                    <TextField size="small" fullWidth label="Or paste an exact MusicBrainz release MBID"
                      value={manualMbids[plan.id] || ''}
                      onChange={e => setManualMbids(current => ({ ...current, [plan.id]: e.target.value.trim() }))} />
                    <Button variant="outlined" disabled={busy || !(manualMbids[plan.id] || '').trim()}
                      onClick={() => chooseRelease(plan.id, manualMbids[plan.id].trim())}>Load MBID</Button>
                  </Stack>
                  {plan.provider_error && <Alert severity="error" sx={{ mt: 1.5 }}>
                    Provider lookup failed for this album: {plan.provider_error}. Paste a release MBID or run the scan again.
                  </Alert>}
                  {release && <Stack direction="row" gap={1} flexWrap="wrap" mt={1}>
                    <Chip size="small" label={`MBID ${release.release_mbid}`} />
                    <Chip size="small" label={release.date || 'No release date'} />
                    <Chip size="small" label={release.status || 'Unknown status'} />
                    <Chip size="small" label="Cover Art Archive" color="primary" variant="outlined" />
                  </Stack>}
                  {plan.variants?.album_artists?.length > 1 && <Alert severity="warning" sx={{ mt: 1.5 }}>
                    Existing Album Artist variants: {plan.variants.album_artists.join(' · ')}. They are evidence only and are never copied into the proposal.
                  </Alert>}
                </Box>
              </Stack>

              <Box sx={{ overflowX: 'auto' }}>
                <Table size="small">
                  <TableHead><TableRow><TableCell>File</TableCell><TableCell>Changed fields</TableCell><TableCell>AcoustID</TableCell></TableRow></TableHead>
                  <TableBody>{plan.tracks.map((track: any) => {
                    const changed = track.proposed ? fields.filter(field => String(track.current[field] ?? '') !== String(track.proposed[field] ?? '')) : [];
                    return <TableRow key={track.path}>
                      <TableCell sx={{ maxWidth: 320, wordBreak: 'break-all' }}>{track.path.split(/[\\/]/).pop()}</TableCell>
                      <TableCell>{track.proposed ? changed.map(field => <Chip key={field} size="small" label={`${field}: ${track.current[field] || '∅'} → ${track.proposed[field] || '∅'}`} sx={{ m: .25 }} />) : <Chip size="small" color="error" label="unmatched" />}</TableCell>
                      <TableCell>{track.acoustid?.status || 'not checked'}</TableCell>
                    </TableRow>;
                  })}</TableBody>
                </Table>
              </Box>

              <Stack direction="row" justifyContent="flex-end" gap={1} mt={2}>
                {plan.state === 'applied' && <Button color="warning" startIcon={<RestoreIcon />} onClick={() => rollback(plan)} disabled={busy}>Rollback</Button>}
                <Button variant="contained" startIcon={<RebuildIcon />} disabled={busy || !fullyMatched || plan.state === 'applied'} onClick={() => apply(plan)}>
                  Apply reviewed album
                </Button>
              </Stack>
            </AccordionDetails>
          </Accordion>;
        })}
        {!albums.length && <Paper variant="outlined" sx={{ p: 5, textAlign: 'center', borderRadius: 3 }}>
          <Typography color="text.secondary">Run a read-only scan to create album-level metadata plans.</Typography>
        </Paper>}
      </Stack>
    </Box>
  );
};

export default MetadataRebuild;
