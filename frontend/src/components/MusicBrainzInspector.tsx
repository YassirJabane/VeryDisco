import React, { useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  TextField,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Chip,
  CircularProgress,
  Alert,
  Grid,
  Link,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Stack,
  Divider,
} from '@mui/material';
import {
  Search as SearchIcon,
  EmojiEvents as WinnerIcon,
  ExpandMore as ExpandMoreIcon,
  Album as AlbumIcon,
  MusicNote as TrackIcon,
  OpenInNew as OpenInNewIcon,
  Layers as DiscIcon,
} from '@mui/icons-material';
import apiService from '../api';

export const MusicBrainzInspector: React.FC = () => {
  const [artist, setArtist] = useState('Drake');
  const [album, setAlbum] = useState('Scorpion');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<any | null>(null);

  const handleInspect = async (searchArtist = artist, searchAlbum = album) => {
    if (!searchArtist.trim() || !searchAlbum.trim()) {
      setError('Please enter both Artist Name and Album Title.');
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const result = await apiService.inspectMusicBrainzRelease(searchArtist, searchAlbum);
      setData(result);
    } catch (err: any) {
      console.error('Failed to inspect MusicBrainz release:', err);
      setError(err.response?.data?.detail || 'Failed to fetch MusicBrainz data. Check network or query.');
    } finally {
      setLoading(false);
    }
  };

  const handleQuickSearch = (quickArtist: string, quickAlbum: string) => {
    setArtist(quickArtist);
    setAlbum(quickAlbum);
    handleInspect(quickArtist, quickAlbum);
  };

  return (
    <Box sx={{ p: { xs: 2, sm: 3 }, maxWidth: 1200, margin: '0 auto' }}>
      {/* Header */}
      <Box sx={{ mb: 4 }}>
        <Typography variant="h4" sx={{ fontWeight: 800, mb: 1, display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <AlbumIcon color="primary" sx={{ fontSize: 36 }} />
          MusicBrainz Release Inspector
        </Typography>
        <Typography variant="body1" color="text.secondary">
          Inspect candidate MusicBrainz releases, evaluate scoring algorithms (CD format, official status, worldwide country), and preview multi-disc tracklists.
        </Typography>
      </Box>

      {/* Search Bar */}
      <Paper elevation={2} sx={{ p: 3, mb: 4, borderRadius: 3, backdropFilter: 'blur(10px)' }}>
        <Grid container spacing={2} alignItems="center">
          <Grid item xs={12} sm={5}>
            <TextField
              fullWidth
              label="Artist Name"
              value={artist}
              onChange={(e) => setArtist(e.target.value)}
              placeholder="e.g. Drake"
              variant="outlined"
              size="medium"
            />
          </Grid>
          <Grid item xs={12} sm={5}>
            <TextField
              fullWidth
              label="Album Title"
              value={album}
              onChange={(e) => setAlbum(e.target.value)}
              placeholder="e.g. Scorpion"
              variant="outlined"
              size="medium"
              onKeyDown={(e) => e.key === 'Enter' && handleInspect()}
            />
          </Grid>
          <Grid item xs={12} sm={2}>
            <Button
              fullWidth
              variant="contained"
              size="large"
              onClick={() => handleInspect()}
              disabled={loading}
              startIcon={loading ? <CircularProgress size={20} color="inherit" /> : <SearchIcon />}
              sx={{ height: 56, borderRadius: 2, fontWeight: 700 }}
            >
              {loading ? 'Inspecting' : 'Inspect'}
            </Button>
          </Grid>
        </Grid>

        {/* Quick Search Preset Chips */}
        <Box sx={{ mt: 2, display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
            Quick Examples:
          </Typography>
          <Chip
            label="Drake — Scorpion (2 Discs)"
            size="small"
            onClick={() => handleQuickSearch('Drake', 'Scorpion')}
            clickable
          />
          <Chip
            label="Radiohead — OK Computer"
            size="small"
            onClick={() => handleQuickSearch('Radiohead', 'OK Computer')}
            clickable
          />
          <Chip
            label="Drake — Views"
            size="small"
            onClick={() => handleQuickSearch('Drake', 'Views')}
            clickable
          />
          <Chip
            label="Radiohead — Amnesiac"
            size="small"
            onClick={() => handleQuickSearch('Radiohead', 'Amnesiac')}
            clickable
          />
        </Box>
      </Paper>

      {/* Error Alert */}
      {error && (
        <Alert severity="error" sx={{ mb: 4, borderRadius: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* Results View */}
      {data && (
        <Stack spacing={4}>
          {/* Winner Card */}
          {data.winner ? (
            <Card
              elevation={3}
              sx={{
                borderRadius: 3,
                border: '2px solid',
                borderColor: 'primary.main',
                background: (theme) =>
                  theme.palette.mode === 'dark'
                    ? 'linear-gradient(135deg, rgba(179, 136, 255, 0.08) 0%, rgba(18, 17, 24, 0.9) 100%)'
                    : 'linear-gradient(135deg, rgba(98, 0, 234, 0.04) 0%, rgba(255, 255, 255, 1) 100%)',
              }}
            >
              <CardContent sx={{ p: 3 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 2, mb: 2 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                    <WinnerIcon sx={{ fontSize: 32, color: 'warning.main' }} />
                    <Box>
                      <Typography variant="h5" sx={{ fontWeight: 800 }}>
                        {data.winner.title}
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        Authoritative MusicBrainz Selected Release
                      </Typography>
                    </Box>
                  </Box>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Chip label={`Status: ${data.winner.status || 'Official'}`} color="primary" variant="outlined" size="small" />
                    <Chip label={`Country: ${data.winner.country || 'Global'}`} color="info" variant="outlined" size="small" />
                    <Chip label={`${data.winner.disc_total} Disc(s)`} color="secondary" size="small" icon={<DiscIcon />} />
                  </Stack>
                </Box>

                <Typography variant="caption" sx={{ display: 'block', mb: 3, fontFamily: 'monospace' }}>
                  MBID: {' '}
                  <Link
                    href={`https://musicbrainz.org/release/${data.winner.id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}
                  >
                    {data.winner.id} <OpenInNewIcon sx={{ fontSize: 14 }} />
                  </Link>
                </Typography>

                <Divider sx={{ my: 2 }} />

                {/* Discs & Tracklists */}
                <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>
                  Multi-Disc Tracklist Preview
                </Typography>

                {data.winner.discs?.map((disc: any) => (
                  <Accordion key={disc.disc_num} defaultExpanded sx={{ mb: 1.5, borderRadius: '8px !important' }}>
                    <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                      <Typography sx={{ fontWeight: 700, display: 'flex', alignItems: 'center', gap: 1 }}>
                        <DiscIcon color="action" fontSize="small" />
                        Disc {disc.disc_num} ({disc.track_count} tracks, Format: {disc.format})
                      </Typography>
                    </AccordionSummary>
                    <AccordionDetails sx={{ p: 0 }}>
                      <TableContainer>
                        <Table size="small">
                          <TableHead sx={{ bgcolor: 'action.hover' }}>
                            <TableRow>
                              <TableCell sx={{ fontWeight: 700, width: 80 }}>Track #</TableCell>
                              <TableCell sx={{ fontWeight: 700 }}>Track Title</TableCell>
                              <TableCell sx={{ fontWeight: 700 }}>Recording MBID</TableCell>
                            </TableRow>
                          </TableHead>
                          <TableBody>
                            {disc.tracks?.map((track: any) => (
                              <TableRow key={track.position} hover>
                                <TableCell>
                                  <Chip
                                    label={`${disc.disc_num}-${String(track.position).padStart(2, '0')}`}
                                    size="small"
                                    variant="outlined"
                                    sx={{ fontFamily: 'monospace', fontWeight: 600 }}
                                  />
                                </TableCell>
                                <TableCell sx={{ fontWeight: 600 }}>
                                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                    <TrackIcon fontSize="small" color="action" />
                                    {track.title}
                                  </Box>
                                </TableCell>
                                <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.8rem', color: 'text.secondary' }}>
                                  {track.recording_id ? (
                                    <Link
                                      href={`https://musicbrainz.org/recording/${track.recording_id}`}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                    >
                                      {track.recording_id.substring(0, 18)}...
                                    </Link>
                                  ) : (
                                    'N/A'
                                  )}
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </TableContainer>
                    </AccordionDetails>
                  </Accordion>
                ))}
              </CardContent>
            </Card>
          ) : (
            <Alert severity="warning">No winning release could be selected.</Alert>
          )}

          {/* Candidate Table Card */}
          <Card elevation={2} sx={{ borderRadius: 3 }}>
            <CardContent sx={{ p: 3 }}>
              <Typography variant="h6" sx={{ fontWeight: 800, mb: 2 }}>
                Candidate Releases & Algorithm Scoring ({data.candidates?.length || 0} Found)
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                Scoring rules: Official Status (+20), Physical CD (+15), Worldwide/US (+10), Title Match (+10), Explicit (+5), Clean/Live/Compilation (-15 to -20).
              </Typography>

              <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 2 }}>
                <Table>
                  <TableHead sx={{ bgcolor: 'action.hover' }}>
                    <TableRow>
                      <TableCell sx={{ fontWeight: 700 }}>Rank / Score</TableCell>
                      <TableCell sx={{ fontWeight: 700 }}>Status</TableCell>
                      <TableCell sx={{ fontWeight: 700 }}>Country</TableCell>
                      <TableCell sx={{ fontWeight: 700 }}>Formats</TableCell>
                      <TableCell sx={{ fontWeight: 700 }}>Title & Disambiguation</TableCell>
                      <TableCell sx={{ fontWeight: 700 }}>MBID</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {data.candidates?.map((candidate: any, index: number) => (
                      <TableRow
                        key={candidate.id}
                        hover
                        selected={candidate.is_winner}
                        sx={{
                          bgcolor: candidate.is_winner ? 'action.selected' : 'inherit',
                        }}
                      >
                        <TableCell>
                          <Stack direction="row" spacing={1} alignItems="center">
                            <Chip
                              label={candidate.score >= 0 ? `+${candidate.score}` : candidate.score}
                              color={candidate.score >= 40 ? 'success' : candidate.score >= 20 ? 'info' : 'default'}
                              sx={{ fontWeight: 800 }}
                            />
                            {candidate.is_winner && (
                              <Chip
                                label="WINNER 🏆"
                                color="warning"
                                size="small"
                                sx={{ fontWeight: 800 }}
                              />
                            )}
                          </Stack>
                        </TableCell>
                        <TableCell>
                          <Chip
                            label={candidate.status}
                            size="small"
                            variant="outlined"
                            color={candidate.status?.toLowerCase() === 'official' ? 'success' : 'default'}
                          />
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" sx={{ fontWeight: 700 }}>
                            {candidate.country || '?'}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" color="text.secondary">
                            {candidate.formats?.join(', ') || 'Unknown'}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" sx={{ fontWeight: candidate.is_winner ? 700 : 500 }}>
                            {candidate.title}
                          </Typography>
                          {candidate.disambiguation && (
                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                              ({candidate.disambiguation})
                            </Typography>
                          )}
                          {candidate.secondary_types?.length > 0 && (
                            <Typography variant="caption" color="error.main" sx={{ display: 'block' }}>
                              Types: {candidate.secondary_types.join(', ')}
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell>
                          <Link
                            href={`https://musicbrainz.org/release/${candidate.id}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            sx={{ fontFamily: 'monospace', fontSize: '0.8rem' }}
                          >
                            {candidate.id.substring(0, 13)}...
                          </Link>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </CardContent>
          </Card>
        </Stack>
      )}
    </Box>
  );
};

export default MusicBrainzInspector;
