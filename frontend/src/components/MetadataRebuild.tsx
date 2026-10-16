import React from 'react';
import {
  Alert, Accordion, AccordionDetails, AccordionSummary, Box, Button, Chip,
  CircularProgress, FormControlLabel, LinearProgress, MenuItem, Paper, Select,
  Stack, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import RebuildIcon from '@mui/icons-material/AutoFixHigh';
import { thumbnailUrl } from '../thumbnail';
import ExpandIcon from '@mui/icons-material/ExpandMore';
import RestoreIcon from '@mui/icons-material/Restore';
import ScanIcon from '@mui/icons-material/Search';
import apiService, { getErrorMessage } from '../api';

const fields = ['title', 'artist', 'album', 'album_artist', 'date', 'track', 'disc', 'bpm', 'key'];
const tagLabel = (value: any): string => Array.isArray(value) ? value.join(', ') || '∅' : value === null || value === undefined || value === '' ? '∅' : String(value);
const bpmMessages: Record<string, string> = {
  preserved: 'Existing BPM preserved', not_found: 'BPM missing · no archive data',
  missing_mbid: 'BPM missing · recording MBID required', unavailable: 'BPM missing · provider unavailable; retry scan',
  duration_mismatch: 'BPM missing · archive duration differs', unmatched: 'BPM missing · match recording first',
  recording_mismatch: 'BPM missing · AcoustID mismatch', disabled: 'BPM missing · lookup disabled',
};

const MetadataRebuild: React.FC = () => {
  const [status, setStatus] = React.useState<any>({ status: 'idle', processed: 0, total: 0 });
  const [albums, setAlbums] = React.useState<any[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [verifyAcoustid, setVerifyAcoustid] = React.useState(false);
  const [lookupMissingBpm, setLookupMissingBpm] = React.useState(true);
  const [audioFeatures, setAudioFeatures] = React.useState(true);
  const [localAudio, setLocalAudio] = React.useState(false);
  const [integerBpm, setIntegerBpm] = React.useState(true);
  const [includeArtwork, setIncludeArtwork] = React.useState(true);
  const [allowItunes, setAllowItunes] = React.useState(true);
  const [manualMbids, setManualMbids] = React.useState<Record<string, string>>({});
  const lastPlansAt = React.useRef(-1);
  const [message, setMessage] = React.useState<{ type: 'success' | 'error' | 'warning'; text: string } | null>(null);

  const refresh = React.useCallback(async (forcePlans: boolean = false) => {
    try {
      const nextStatus = await apiService.getMetadataRebuildStatus();
      setStatus(nextStatus);
      const active = ['queued', 'scanning', 'cancelling'].includes(nextStatus.status);
      const planMarker = Number(nextStatus.processed || 0);
      if (forcePlans || !active || planMarker !== lastPlansAt.current) {
        setAlbums(await apiService.getMetadataRebuildAlbums());
        lastPlansAt.current = planMarker;
      }
      return nextStatus;
    } catch (error) {
      setMessage({ type: 'error', text: getErrorMessage(error, 'Unable to load metadata rebuild state.') });
    }
  }, []);

  React.useEffect(() => { refresh(true); }, [refresh]);
  React.useEffect(() => {
    if (!['queued', 'scanning', 'cancelling', 'applying'].includes(status.status)) return;
    const timer = window.setInterval(refresh, 2500);
    return () => window.clearInterval(timer);
  }, [status.status, refresh]);

  const startScan = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await apiService.startMetadataRebuildScan(verifyAcoustid, lookupMissingBpm, {
        audio_features_enabled: audioFeatures, local_audio_analysis: localAudio, bpm_integer: integerBpm,
      });
      setStatus({ status: 'scanning', processed: 0, total: 0, message: 'Starting read-only inventory…' });
    } catch (error) {
      setMessage({ type: 'error', text: getErrorMessage(error, 'Scan could not be started.') });
    } finally {
      setBusy(false);
    }
  };

  const cancelScan = async () => {
    setBusy(true);
    try {
      await apiService.cancelMetadataRebuildScan();
      setStatus((current: any) => ({ ...current, status: 'cancelling', message: 'Stopping metadata scan…' }));
    } catch (error) {
      setMessage({ type: 'error', text: getErrorMessage(error, 'Scan could not be cancelled.') });
    } finally { setBusy(false); }
  };

  const chooseRelease = async (planId: string, releaseMbid: string) => {
    setBusy(true);
    try {
      await apiService.selectMetadataRelease(planId, releaseMbid);
      await refresh(true);
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
      await refresh(true);
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
      await refresh(true);
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
            Release-centric reconstruction using MusicBrainz, Cover Art Archive and optional AcoustID verification. Missing BPM can be looked up by recording MBID in AcousticBrainz. Deezer is not used.
          </Typography>
        </Box>
        {['queued', 'scanning', 'cancelling'].includes(status.status)
          ? <Button color="warning" variant="outlined" disabled={busy || status.status === 'cancelling'} onClick={cancelScan}>Stop scan</Button>
          : <Button variant="contained" startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <ScanIcon />}
              disabled={busy} onClick={startScan}>Scan entire library</Button>}
      </Stack>

      {message && <Alert severity={message.type} sx={{ mb: 2 }} onClose={() => setMessage(null)}>{message.text}</Alert>}
      <Alert severity="info" sx={{ mb: 2 }}>
        <strong>1. Scan:</strong> reads tags and prepares proposals without changing files.{' '}
        <strong>2. Review:</strong> open an album and verify the selected release and changed fields.{' '}
        <strong>3. Apply:</strong> writes one reviewed album and creates a rollback backup first.
        Artist aliases are resolved automatically through MusicBrainz; cosmetic spellings such as JAY-Z are preserved instead of being rewritten to JAŸ-Z.
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
            <FormControlLabel control={<Switch checked={lookupMissingBpm} onChange={e => setLookupMissingBpm(e.target.checked)} />} label="Look up missing BPM" />
            <FormControlLabel control={<Switch checked={integerBpm} onChange={e => setIntegerBpm(e.target.checked)} />} label="Round new BPM to integers" />
            <FormControlLabel control={<Switch checked={audioFeatures} onChange={e => setAudioFeatures(e.target.checked)} />} label="Audio features and missing key" />
            <FormControlLabel control={<Switch checked={localAudio} onChange={e => setLocalAudio(e.target.checked)} />} label="Analyze locally with Essentia" />
            <FormControlLabel control={<Switch checked={includeArtwork} onChange={e => setIncludeArtwork(e.target.checked)} />} label="Replace artwork" />
            <FormControlLabel control={<Switch checked={allowItunes} onChange={e => setAllowItunes(e.target.checked)} disabled={!includeArtwork} />} label="Allow verified digital artwork fallback" />
          </Stack>
        </Stack>
        <Typography variant="caption" color="text.secondary" display="block" mt={1}>
          BPM lookups use archived audio-analysis estimates, not MusicBrainz metadata. AcousticBrainz stopped collecting data in 2022, so coverage is limited. Existing BPM is preserved; unavailable values stay missing. Scan again to refresh saved plans.
          {' '}Local analysis requires the optional Essentia package on the server, runs sequentially in an isolated worker and never uploads audio. Mood/genre classifiers are suggestions only; archive loudness is not applied as ReplayGain.
        </Typography>
        {['scanning', 'queued', 'cancelling'].includes(status.status) && <Box mt={2}>
          <LinearProgress variant={status.total ? 'determinate' : 'indeterminate'} value={progress} />
          <Typography variant="caption" color="text.secondary">{status.message} {status.total ? `(${status.processed}/${status.total})` : ''}</Typography>
          {status.phase === 'inventory' && <Typography display="block" variant="caption" color="text.secondary">
            Files read: {status.files_scanned || 0} · album folders found: {status.folders_found || 0}
          </Typography>}
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
                  {plan.bpm_summary && <Typography variant="caption" color="text.secondary" display="block">
                    BPM at scan: {plan.bpm_summary.missing} missing · {plan.bpm_summary.proposed} proposed · {plan.bpm_summary.unresolved} unresolved
                  </Typography>}
                </Box>
                <Chip size="small" label={`${Math.round(plan.confidence * 100)}%`} color={plan.confidence >= .9 ? 'success' : plan.confidence >= .6 ? 'warning' : 'error'} />
                <Chip size="small" label={plan.state} variant="outlined" />
              </Stack>
            </AccordionSummary>
            <AccordionDetails>
              <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} mb={2}>
                {release?.artwork_url && <Stack spacing={.5}><Box component="img" src={thumbnailUrl(release.artwork_url)} loading="lazy" decoding="async" alt="MusicBrainz cover preview" sx={{ width: 150, height: 150, objectFit: 'cover', borderRadius: 2, bgcolor: 'action.hover' }} /><Typography variant="caption" color="text.secondary">Selected MusicBrainz release artwork comes first. Verified digital artwork is only a fallback when enabled.</Typography></Stack>}
                <Box flex={1}>
                  <Typography variant="subtitle2" mb={1}>Selected MusicBrainz release</Typography>
                  <Select size="small" fullWidth value={release?.release_mbid || ''} displayEmpty
                    onChange={e => chooseRelease(plan.id, String(e.target.value))} disabled={busy}>
                    {!release && <MenuItem value="" disabled>No confident release selected</MenuItem>}
                    {plan.candidates.map((candidate: any) => {
                      const dateLabel = candidate.release_date && candidate.release_date !== candidate.date
                        ? `first released ${candidate.date} · edition ${candidate.release_date}`
                        : candidate.date || 'unknown date';
                      return <MenuItem key={candidate.release_mbid} value={candidate.release_mbid}>
                        {candidate.album_artist} — {candidate.album} · {dateLabel} · {candidate.country || 'unknown country'} · {candidate.track_total} tracks
                      </MenuItem>;
                    })}
                  </Select>
                  <Stack direction={{ xs: 'column', sm: 'row' }} gap={1} mt={1}>
                    <TextField size="small" fullWidth label="Or paste an exact MusicBrainz release MBID"
                      value={manualMbids[plan.id] || ''}
                      onChange={e => setManualMbids(current => ({ ...current, [plan.id]: e.target.value.trim() }))} />
                    <Button variant="outlined" disabled={busy || !(manualMbids[plan.id] || '').trim()}
                      onClick={() => chooseRelease(plan.id, manualMbids[plan.id].trim())}>Load MBID</Button>
                  </Stack>
                  {plan.provider_error && <Alert severity="error" sx={{ mt: 1.5 }}>
                    Provider lookup failed for this album: {plan.provider_error}{' '}
                    {plan.provider_error_retryable
                      ? 'Try the scan again later. Changing the release MBID does not resolve a provider outage.'
                      : 'Paste a release MBID or run the scan again.'}
                  </Alert>}
                  {release && <Stack direction="row" gap={1} flexWrap="wrap" mt={1}>
                    <Chip size="small" label={`MBID ${release.release_mbid}`} />
                    <Chip size="small" label={release.date || 'No release date'} />
                    <Chip size="small" label={release.status || 'Unknown status'} />
                    <Chip size="small" label="Artwork policy enabled" color="primary" variant="outlined" />
                  </Stack>}
                  {plan.variants?.album_artists?.length > 1 && <Alert severity="warning" sx={{ mt: 1.5 }}>
                    Existing Album Artist variants: {plan.variants.album_artists.join(' · ')}. They are evidence only and are never copied into the proposal.
                  </Alert>}
                </Box>
              </Stack>

              <Box sx={{ overflowX: 'auto' }}>
                <Table size="small">
                  <TableHead><TableRow><TableCell>File</TableCell><TableCell>Changed fields</TableCell><TableCell>BPM</TableCell><TableCell>AcoustID</TableCell></TableRow></TableHead>
                  <TableBody>{plan.tracks.map((track: any) => {
                    const changed = track.proposed ? fields.filter(field => Object.prototype.hasOwnProperty.call(track.proposed, field) && String(track.current[field] ?? '') !== String(track.proposed[field] ?? '')) : [];
                    return <TableRow key={track.path}>
                      <TableCell sx={{ maxWidth: 320, wordBreak: 'break-all' }}>{track.path.split(/[\\/]/).pop()}</TableCell>
                      <TableCell>{track.proposed ? changed.map(field => <Chip key={field} size="small" label={`${field}: ${tagLabel(track.current[field])} → ${tagLabel(track.proposed[field])}`} sx={{ m: .25 }} />) : <Chip size="small" color="error" label="unmatched" />}</TableCell>
                      <TableCell>
                        {track.bpm_lookup?.status === 'found'
                          ? <><Chip size="small" color="info" variant="outlined" label={`${track.proposed?.bpm?.join(', ')} BPM ${plan.state === 'applied' ? 'applied' : 'proposed'}`} /><Typography variant="caption" display="block">{track.bpm_lookup.source} · estimate, review before applying{track.bpm_lookup.raw_bpm ? ` · raw ${track.bpm_lookup.raw_bpm}` : ''}</Typography></>
                          : <Typography variant="caption">{track.current.bpm?.length ? `${track.current.bpm.join(', ')} BPM · preserved` : bpmMessages[track.bpm_lookup?.status] || 'BPM missing · run a new scan'}</Typography>}
                        {track.audio_analysis && <Box component="details" sx={{ mt: 1 }}>
                          <Box component="summary" sx={{ cursor: 'pointer' }}>Audio analysis details</Box>
                          {track.audio_analysis.local && <Typography variant="caption" display="block">Local: {track.audio_analysis.local.status}</Typography>}
                          {Object.entries(track.audio_analysis.features || {}).map(([name, value]) => <Typography key={name} variant="caption" display="block">{name}: {String(value)}</Typography>)}
                          {Object.entries(track.audio_analysis.classifiers?.features || {}).map(([name, value]: [string, any]) => <Typography key={name} variant="caption" display="block">{name}: {value.value} · model score {value.probability.toFixed(2)} · suggestion only</Typography>)}
                        </Box>}
                      </TableCell>
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
