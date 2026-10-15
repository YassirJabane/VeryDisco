import React from 'react';
import { alpha } from '@mui/material/styles';
import { Box, IconButton, Slider, Stack, Typography } from '@mui/material';
import CloseRounded from '@mui/icons-material/CloseRounded';
import GraphicEqRounded from '@mui/icons-material/GraphicEqRounded';
import PauseRounded from '@mui/icons-material/PauseRounded';
import PlayArrowRounded from '@mui/icons-material/PlayArrowRounded';
import VolumeUpRounded from '@mui/icons-material/VolumeUpRounded';

export interface PreviewTrack { filepath: string; title: string; artist: string }
export function formatPlaybackTime(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return '0:00';
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
}

export default function GlobalPlayer({ track, onClose }: { track: PreviewTrack | null; onClose: () => void }) {
  const audioRef = React.useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = React.useState(false);
  const [duration, setDuration] = React.useState(0);
  const [position, setPosition] = React.useState(0);
  const [error, setError] = React.useState('');
  const [volume, setVolume] = React.useState(.8);
  const volumeRef = React.useRef(volume);
  const generation = React.useRef(0);
  React.useEffect(() => {
    volumeRef.current = volume;
    if (audioRef.current) audioRef.current.volume = volume;
  }, [volume]);
  React.useEffect(() => {
    const audio = audioRef.current;
    const request = ++generation.current;
    setPlaying(false); setDuration(0); setPosition(0); setError('');
    if (!audio || !track) return;
    audio.volume = volumeRef.current;
    audio.src = `/api/library/tracks/stream?filepath=${encodeURIComponent(track.filepath)}`;
    void audio.play().catch((failure: DOMException) => {
      if (generation.current === request && failure.name !== 'AbortError') setError(failure.name === 'NotAllowedError' ? 'Press play to start your preview' : 'Preview unavailable. Try again.');
    });
    return () => { generation.current += 1; audio.pause(); audio.removeAttribute('src'); audio.load(); };
  }, [track?.filepath]);
  const playPause = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (!audio.paused) { audio.pause(); return; }
    setError('');
    const request = generation.current;
    void audio.play().catch(() => { if (generation.current === request) setError('Preview unavailable. Try again.'); });
  };
  if (!track) return null;
  const safeDuration = Number.isFinite(duration) && duration > 0 ? duration : 0;
  return <Box component="section" aria-label="Audio preview" sx={theme => ({ position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: theme.zIndex.drawer + 1,
    minHeight: 88, pb: 'env(safe-area-inset-bottom)', px: { xs: 2, sm: 3 }, display: 'flex', alignItems: 'center', gap: { xs: 1.5, sm: 3 },
    bgcolor: alpha(theme.palette.background.paper, .96), borderTop: '1px solid', borderColor: 'divider', backdropFilter: 'blur(24px)', boxShadow: '0 -8px 40px rgba(0,0,0,.12)' })}>
    <audio ref={audioRef} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}
      onTimeUpdate={event => setPosition(event.currentTarget.currentTime)} onDurationChange={event => setDuration(event.currentTarget.duration)}
      onError={() => { setPlaying(false); setError('Preview unavailable. Try another track.'); }} />
    <Box sx={{ width: 48, height: 48, flexShrink: 0, borderRadius: 2.5, display: { xs: 'none', sm: 'grid' }, placeItems: 'center', bgcolor: 'action.selected', color: 'primary.main' }}><GraphicEqRounded /></Box>
    <Box sx={{ minWidth: 0, flex: { xs: 1, md: '0 1 260px' } }}>
      <Typography variant="caption" color="primary.main" sx={{ fontSize: '.63rem', textTransform: 'uppercase', letterSpacing: '.13em', fontWeight: 750 }}>Now previewing</Typography>
      <Typography variant="body2" fontWeight={700} noWrap>{track.title}</Typography>
      <Typography variant="caption" color={error ? 'warning.main' : 'text.secondary'} noWrap role={error ? 'status' : undefined}>{error || track.artist}</Typography>
    </Box>
    <Stack direction="row" alignItems="center" gap={1.5} sx={{ flex: { xs: '0 0 auto', md: 1 }, maxWidth: 640 }}>
      <IconButton aria-label={playing ? 'Pause playback' : 'Play track'} onClick={playPause} sx={{ bgcolor: 'primary.main', color: 'primary.contrastText', width: 42, height: 42, '&:hover': { bgcolor: 'primary.dark' } }}>{playing ? <PauseRounded /> : <PlayArrowRounded />}</IconButton>
      <Typography variant="caption" color="text.secondary" sx={{ display: { xs: 'none', md: 'block' }, minWidth: 32, fontVariantNumeric: 'tabular-nums' }}>{formatPlaybackTime(position)}</Typography>
      <Slider aria-label="Seek playback position" size="small" min={0} max={safeDuration || 1} disabled={!safeDuration}
        value={Math.max(0, Math.min(Number.isFinite(position) ? position : 0, safeDuration))}
        onChange={(_, value) => { if (audioRef.current) { audioRef.current.currentTime = value as number; setPosition(value as number); } }}
        sx={{ display: { xs: 'none', md: 'block' }, flex: 1, minWidth: 80 }} />
      <Typography variant="caption" color="text.secondary" sx={{ display: { xs: 'none', md: 'block' }, minWidth: 32, fontVariantNumeric: 'tabular-nums' }}>{formatPlaybackTime(safeDuration)}</Typography>
    </Stack>
    <Stack direction="row" alignItems="center" gap={1} sx={{ ml: 'auto' }}>
      <VolumeUpRounded fontSize="small" sx={{ color: 'text.secondary', display: { xs: 'none', lg: 'block' } }} />
      <Slider aria-label="Volume" size="small" value={volume} min={0} max={1} step={.05} onChange={(_, value) => setVolume(value as number)} sx={{ width: 75, display: { xs: 'none', lg: 'block' } }} />
      <IconButton size="small" onClick={onClose} aria-label="Close player"><CloseRounded fontSize="small" /></IconButton>
    </Stack>
  </Box>;
}
