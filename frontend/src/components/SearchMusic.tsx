import React, { useState } from 'react';
import { 
  Box, Card, CardContent, Typography, TextField, FormControl, 
  InputLabel, Select, MenuItem, Button, Grid, CircularProgress, 
  List, ListItem, ListItemAvatar, ListItemText, Avatar, Chip, 
  Tooltip, IconButton, useTheme, Dialog, DialogTitle, DialogContent, DialogActions,
  Collapse, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper
} from '@mui/material';
import { 
  LibraryMusic as MusicIcon,
  CloudDownload as DownloadIcon,
  FolderZip as AlbumIcon,
  CheckCircle as ExistIcon
} from '@mui/icons-material';
import { apiService } from '../api';
import LyricsPreviewDialog from './LyricsPreviewDialog';
import { useNotification } from '../context/NotificationContext';

export const SearchMusic: React.FC = () => {
  const theme = useTheme();
  const { notify, confirm } = useNotification();
  const [searchQuery, setSearchQuery] = useState('');
  const [searchType, setSearchType] = useState<'track' | 'album'>('track');
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [downloadingKeys, setDownloadingKeys] = useState<Set<string>>(new Set());
  const [albumVersions, setAlbumVersions] = useState<Record<number, any[]>>({});
  const [selectedVersions, setSelectedVersions] = useState<Record<number, any>>({});
  const [versionsLoading, setVersionsLoading] = useState<Record<number, boolean>>({});
  const [selectedLyricsTrack, setSelectedLyricsTrack] = useState<{ artist: string; title: string; album: string; duration: number } | null>(null);
  const [lyricsOpen, setLyricsOpen] = useState(false);

  const [manualGrabOpen, setManualGrabOpen] = useState(false);
  const [manualGrabArtist, setManualGrabArtist] = useState('');
  const [manualGrabAlbum, setManualGrabAlbum] = useState('');
  const [searchingCandidates, setSearchingCandidates] = useState(false);
  const [candidates, setCandidates] = useState<any[]>([]);
  const [grabbingKey, setGrabbingKey] = useState<string | null>(null);

  const [expandedCandIdx, setExpandedCandIdx] = useState<number | null>(null);

  const handleOpenManualGrab = async (artist: string, album: string) => {
    setManualGrabArtist(artist);
    setManualGrabAlbum(album);
    setManualGrabOpen(true);
    setSearchingCandidates(true);
    setCandidates([]);
    setExpandedCandIdx(null);
    try {
      const results = await apiService.searchAlbumCandidates(artist, album);
      setCandidates(results);
    } catch (err: any) {
      notify("Failed to search album candidates on Slskd.", "error");
    } finally {
      setSearchingCandidates(false);
    }
  };

  const getFilenameOnly = (filepath: string) => {
    const parts = filepath.split(/[\\/]/);
    return parts[parts.length - 1];
  };

  const formatSize = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const handleGrabAlbum = async (cand: any) => {
    const key = `${cand.username}-${cand.folder}`;
    setGrabbingKey(key);
    try {
      await apiService.grabAlbum(
        manualGrabArtist,
        manualGrabAlbum,
        cand.username,
        cand.folder,
        cand.files
      );
      notify(`Manual grab initiated from "${cand.username}" for "${manualGrabAlbum}".`, "success");
      setManualGrabOpen(false);
    } catch (err: any) {
      notify(err.response?.data?.detail || "Failed to initiate grab.", "error");
    } finally {
      setGrabbingKey(null);
    }
  };

  const handleOpenLyricsPreview = (artist: string, title: string, album: string, durationSecs: number) => {
    setSelectedLyricsTrack({ artist, title, album, duration: durationSecs });
    setLyricsOpen(true);
  };

  const fetchVersions = async (albumId: number, artistId: number, baseTitle: string) => {
    if (albumVersions[albumId] || versionsLoading[albumId] || !artistId) return;
    setVersionsLoading(prev => ({ ...prev, [albumId]: true }));
    try {
      const data = await apiService.getArtistAlbums(artistId);
      const artistAlbums = data.data || [];
      
      const getBaseAlbumName = (t: string) => {
        return t
          .replace(/\(.*?\)/g, '')
          .replace(/\[.*?\]/g, '')
          .replace(/\b(deluxe|remastered|special|expanded|edition|anniversary|super|remaster|collector|bonus|tracks|version|lp|ep|cd)\b/gi, '')
          .trim();
      };
      
      const baseName = (getBaseAlbumName(baseTitle) || '').toLowerCase();
      
      const matching = artistAlbums.filter((alb: any) => {
        const albTitle = (alb.title || '').toLowerCase();
        return albTitle.includes(baseName) || baseName.includes(albTitle);
      });
      
      // Ensure the original album is in the list
      if (!matching.some((a: any) => a.id === albumId)) {
        // We don't have the original, let's prepend or find it, but matching filter should cover it.
      }
      
      setAlbumVersions(prev => ({ ...prev, [albumId]: matching }));
    } catch (err) {
      console.error("Failed to fetch album versions", err);
    } finally {
      setVersionsLoading(prev => ({ ...prev, [albumId]: false }));
    }
  };

  const handleSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!searchQuery.trim()) return;
    
    setAlbumVersions({});
    setSelectedVersions({});
    setVersionsLoading({});
    setSearchLoading(true);
    try {
      const data = await apiService.searchDeezer(searchQuery, searchType);
      const items = data.data || [];
      
      if (searchType === 'track') {
        // Enrich items in parallel by checking if they already exist in local library
        const enriched = await Promise.all(items.map(async (item: any) => {
          const artistName = item.artist?.name || '';
          const trackTitle = item.title || '';
          try {
            const check = await apiService.checkTrackExists(artistName, trackTitle);
            return { 
              ...item, 
              exists: check.exists,
              qualityStatus: check.quality_status,
              existingQuality: check.existing_quality
            };
          } catch {
            return { ...item, exists: false, qualityStatus: 'worse', existingQuality: null };
          }
        }));
        setSearchResults(enriched);
      } else {
        // Enrich albums in parallel
        const enriched = await Promise.all(items.map(async (item: any) => {
          const artistName = item.artist?.name || '';
          const albumTitle = item.title || '';
          try {
            const check = await apiService.checkTrackExists(artistName, albumTitle, item.id);
            return { 
              ...item, 
              exists: check.exists,
              albumStatus: check.status,
              upgradeAvailable: check.upgrade_available
            };
          } catch {
            return { ...item, exists: false, albumStatus: 'missing', upgradeAvailable: false };
          }
        }));
        setSearchResults(enriched);
      }
    } catch (err: any) {
      console.error("Deezer search failed", err);
      notify("Failed to perform Deezer search.", "error");
    } finally {
      setSearchLoading(false);
    }
  };

  const executeDownloadTrack = async (artist: string, title: string, album: string, force: boolean, key: string) => {
    setDownloadingKeys(prev => new Set(prev).add(key));
    try {
      await apiService.downloadTrack(artist, title, album, force);
      notify(`Single track search/download queued for "${artist} - ${title}".`, "success");
      
      setSearchResults(prev => prev.map(item => {
        if (item.artist?.name === artist && item.title === title) {
          return { ...item, exists: true, qualityStatus: 'same' };
        }
        return item;
      }));
    } catch (err: any) {
      notify(err.response?.data?.detail || "Failed to queue track download.", "error");
    } finally {
      setDownloadingKeys(prev => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  };

  const handleDownloadTrack = async (artist: string, title: string, album: string, existingExists?: boolean, existingQualityStatus?: string) => {
    const key = `track-${artist}-${title}`;
    if (existingExists && existingQualityStatus !== 'worse') {
      confirm({
        title: "Re-download Track?",
        message: `You already have "${artist} - ${title}" in the desired quality. Do you want to download/overwrite it anyway?`,
        confirmText: "Download Anyway",
        onConfirm: () => executeDownloadTrack(artist, title, album, true, key)
      });
      return;
    }
    executeDownloadTrack(artist, title, album, false, key);
  };

  const executeDownloadAlbum = async (artist: string, album: string, force: boolean, key: string) => {
    setDownloadingKeys(prev => new Set(prev).add(key));
    try {
      await apiService.downloadAlbum(artist, album, force);
      notify(`Full album download queued for "${artist} - ${album}".`, "success");
    } catch (err: any) {
      notify(err.response?.data?.detail || "Failed to queue album download.", "error");
    } finally {
      setDownloadingKeys(prev => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  };

  const handleDownloadAlbum = async (artist: string, album: string, existingStatus?: string, existingUpgradeAvailable?: boolean) => {
    const key = `album-${artist}-${album}`;
    if (existingStatus === 'full' && !existingUpgradeAvailable) {
      confirm({
        title: "Re-download Album?",
        message: `You already have the album "${artist} - ${album}" fully in the desired quality. Do you want to download/overwrite it anyway?`,
        confirmText: "Download Anyway",
        onConfirm: () => executeDownloadAlbum(artist, album, true, key)
      });
      return;
    }
    executeDownloadAlbum(artist, album, false, key);
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <Box>
        <Typography variant="h4" sx={{ fontWeight: 800 }}>Search Music</Typography>
        <Typography variant="body2" color="text.secondary">
          Find songs and albums on Deezer, check if they exist, and download them
        </Typography>
      </Box>

      <Card sx={{ 
        background: theme.palette.mode === 'dark' ? '#1c1b22' : '#ffffff',
        borderRadius: 4,
        boxShadow: '0 4px 20px rgba(0,0,0,0.05)'
      }}>
        <CardContent sx={{ p: { xs: 2, sm: 4 } }}>
          {/* Search Form */}
          <form onSubmit={handleSearch}>
            <Grid container spacing={{ xs: 1.5, sm: 2 }} alignItems="center" sx={{ mb: 4 }}>
              <Grid item xs={12} sm={6} md={7}>
                <TextField
                  fullWidth
                  label="Search artist, track or album..."
                  variant="outlined"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="e.g. Travis Scott, Utopia, Cigarette Daydreams"
                />
              </Grid>
              <Grid item xs={12} sm={3} md={2}>
                <FormControl fullWidth>
                  <InputLabel>Type</InputLabel>
                  <Select
                    value={searchType}
                    label="Type"
                    onChange={(e) => setSearchType(e.target.value as 'track' | 'album')}
                  >
                    <MenuItem value="track">Track</MenuItem>
                    <MenuItem value="album">Album</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} sm={3} md={3}>
                <Button
                  fullWidth
                  type="submit"
                  variant="contained"
                  color="primary"
                  disabled={searchLoading || !searchQuery.trim()}
                  sx={{ height: 56, fontWeight: 700 }}
                >
                  {searchLoading ? <CircularProgress size={24} color="inherit" /> : 'Search'}
                </Button>
              </Grid>
            </Grid>
          </form>

          {searchLoading ? (
            <Box display="flex" justifyContent="center" p={4}><CircularProgress /></Box>
          ) : searchResults.length === 0 ? (
            <Typography color="text.secondary" textAlign="center" py={4}>
              No search results to display. Type something above and press Search.
            </Typography>
          ) : (
            <List sx={{ width: '100%', bgcolor: 'transparent' }}>
              {searchResults.map((item, i) => {
                const artistName = item.artist?.name || 'Unknown Artist';
                const isTrack = searchType === 'track';
                const selectedVersion = !isTrack ? (selectedVersions[item.id] || item) : null;
                const titleLabel = isTrack ? (item.title || 'Unknown Title') : (selectedVersion?.title || item.title || 'Unknown Album');
                const subtitleLabel = isTrack 
                  ? `${artistName} ${item.album?.title ? `• ${item.album.title}` : ''}`
                  : artistName;
                const coverUrl = isTrack ? (item.album?.cover_medium || '') : (selectedVersion?.cover_medium || item.cover_medium || '');
                const trackAlbum = isTrack ? (item.album?.title || '') : '';

                const trackKey = `track-${artistName}-${titleLabel}`;
                const albumKey = `album-${artistName}-${isTrack ? trackAlbum : titleLabel}`;

                return (
                  <ListItem
                    key={i}
                    divider={i < searchResults.length - 1}
                    sx={{ 
                      py: 2,
                      px: { xs: 0, sm: 2 },
                      flexDirection: { xs: 'column', sm: 'row' },
                      alignItems: { xs: 'stretch', sm: 'center' },
                      gap: { xs: 1.5, sm: 0 },
                      transition: 'background-color 0.2s',
                      '&:hover': { bgcolor: theme.palette.mode === 'dark' ? 'rgba(255,255,255,0.02)' : 'rgba(0,0,0,0.02)' }
                    }}
                  >
                    <Box display="flex" alignItems="center" sx={{ width: '100%' }}>
                    <ListItemAvatar>
                      <Avatar
                        variant="rounded"
                        src={coverUrl}
                        sx={{ width: 64, height: 64, mr: 2, boxShadow: '0 4px 10px rgba(0,0,0,0.15)' }}
                      >
                        <MusicIcon />
                      </Avatar>
                    </ListItemAvatar>
                    <ListItemText
                      primary={
                        <Box display="flex" alignItems="center" flexWrap="wrap" gap={1.5}>
                          <Typography sx={{ fontWeight: 700, fontSize: '1.1rem' }}>{titleLabel}</Typography>
                          {isTrack ? (
                            item.exists ? (
                              item.qualityStatus === 'worse' ? (
                                <Chip 
                                  label="Already in Library (Upgrade)" 
                                  size="small"
                                  sx={{ 
                                    bgcolor: 'rgba(237, 108, 2, 0.1)', 
                                    color: '#ed6c02', 
                                    fontWeight: 700,
                                    border: '1px solid rgba(237, 108, 2, 0.3)'
                                  }}
                                />
                              ) : (
                                <Chip 
                                  label="Already in Library" 
                                  size="small"
                                  sx={{ 
                                    bgcolor: 'rgba(46, 125, 50, 0.1)', 
                                    color: '#2e7d32', 
                                    fontWeight: 700,
                                    border: '1px solid rgba(46, 125, 50, 0.3)'
                                  }}
                                />
                              )
                            ) : (
                              <Chip 
                                label="Not in Library" 
                                size="small"
                                  sx={{ 
                                    bgcolor: 'rgba(211, 47, 47, 0.1)', 
                                    color: '#d32f2f', 
                                    fontWeight: 700,
                                    border: '1px solid rgba(211, 47, 47, 0.3)'
                                  }}
                              />
                            )
                          ) : (
                            (() => {
                              const status = selectedVersion?.albumStatus || item.albumStatus || 'missing';
                              const upgrade = selectedVersion?.upgradeAvailable || item.upgradeAvailable || false;
                              
                              if (status === 'full') {
                                if (upgrade) {
                                  return (
                                    <Chip 
                                      label="Fully in Library (Upgrade)" 
                                      size="small"
                                      sx={{ 
                                        bgcolor: 'rgba(237, 108, 2, 0.1)', 
                                        color: '#ed6c02', 
                                        fontWeight: 700,
                                        border: '1px solid rgba(237, 108, 2, 0.3)'
                                      }}
                                    />
                                  );
                                } else {
                                  return (
                                    <Chip 
                                      label="Fully in Library" 
                                      size="small"
                                      sx={{ 
                                        bgcolor: 'rgba(46, 125, 50, 0.1)', 
                                        color: '#2e7d32', 
                                        fontWeight: 700,
                                        border: '1px solid rgba(46, 125, 50, 0.3)'
                                      }}
                                    />
                                  );
                                }
                              } else if (status === 'partial') {
                                return (
                                  <Chip 
                                    label="Partially in Library" 
                                    size="small"
                                    sx={{ 
                                      bgcolor: 'rgba(25, 118, 210, 0.1)', 
                                      color: '#1976d2', 
                                      fontWeight: 700,
                                      border: '1px solid rgba(25, 118, 210, 0.3)'
                                    }}
                                  />
                                );
                              } else {
                                return (
                                  <Chip 
                                    label="Not in Library" 
                                    size="small"
                                    sx={{ 
                                      bgcolor: 'rgba(211, 47, 47, 0.1)', 
                                      color: '#d32f2f', 
                                      fontWeight: 700,
                                      border: '1px solid rgba(211, 47, 47, 0.3)'
                                    }}
                                  />
                                );
                              }
                            })()
                          )}
                        </Box>
                      }
                      secondary={
                        <Box display="flex" flexDirection="column" gap={1} sx={{ mt: 0.5 }}>
                          <Typography variant="body2" color="text.secondary">
                            {subtitleLabel}
                          </Typography>
                          {!isTrack && (
                            <FormControl size="small" fullWidth sx={{ mt: 1, maxWidth: { xs: '100%', sm: 280 } }}>
                              <InputLabel id={`version-select-label-${item.id}`}>Release Version</InputLabel>
                              <Select
                                labelId={`version-select-label-${item.id}`}
                                value={selectedVersions[item.id]?.id || item.id}
                                label="Release Version"
                                onOpen={() => fetchVersions(item.id, item.artist?.id, item.title)}
                                onChange={(e) => {
                                  const val = e.target.value;
                                  if (val === item.id) {
                                    setSelectedVersions(prev => {
                                      const next = { ...prev };
                                      delete next[item.id];
                                      return next;
                                    });
                                  } else {
                                    const matching = albumVersions[item.id] || [];
                                    const found = matching.find((a: any) => a.id === val);
                                    if (found) {
                                      setSelectedVersions(prev => ({ ...prev, [item.id]: found }));
                                    }
                                  }
                                }}
                              >
                                <MenuItem value={item.id}>
                                  <em>{item.title} (Default)</em>
                                </MenuItem>
                                {versionsLoading[item.id] ? (
                                  <MenuItem disabled>
                                    <CircularProgress size={16} sx={{ mr: 1 }} /> Loading versions...
                                  </MenuItem>
                                ) : (
                                  (albumVersions[item.id] || [])
                                    .filter((v: any) => v.id !== item.id)
                                    .map((v: any) => (
                                      <MenuItem key={v.id} value={v.id}>
                                        {v.title}
                                      </MenuItem>
                                    ))
                                )}
                                </Select>
                              </FormControl>
                            )}
                          </Box>
                        }
                      />
                    </Box>
                    <Box display="flex" alignItems="center" gap={1} flexWrap="wrap" sx={{ pl: { xs: 10, sm: 0 }, mt: { xs: 1, sm: 0 }, justifyContent: { xs: 'flex-start', sm: 'flex-end' } }}>
                       {isTrack ? (
                        <>
                          <Button
                            variant="outlined"
                            color="secondary"
                            size="small"
                            onClick={() => handleOpenLyricsPreview(artistName, titleLabel, trackAlbum || '', item.duration)}
                            sx={{ fontWeight: 700, whiteSpace: 'nowrap' }}
                          >
                            Lyrics
                          </Button>
                          <Button
                            variant="contained"
                            color="primary"
                            size="small"
                            startIcon={downloadingKeys.has(trackKey) ? <CircularProgress size={14} color="inherit" /> : <DownloadIcon />}
                            disabled={downloadingKeys.has(trackKey)}
                            onClick={() => handleDownloadTrack(artistName, titleLabel, trackAlbum, item.exists, item.qualityStatus)}
                            sx={{ fontWeight: 700, whiteSpace: 'nowrap' }}
                          >
                            Download
                          </Button>
                          <Button
                            variant="outlined"
                            color="success"
                            size="small"
                            startIcon={downloadingKeys.has(albumKey) ? <CircularProgress size={14} color="inherit" /> : <AlbumIcon />}
                            disabled={downloadingKeys.has(albumKey) || !trackAlbum}
                            onClick={() => handleDownloadAlbum(artistName, trackAlbum, item.albumStatus, item.upgradeAvailable)}
                            sx={{ fontWeight: 700, whiteSpace: 'nowrap' }}
                          >
                            Album
                          </Button>
                          <Button
                            variant="outlined"
                            color="warning"
                            size="small"
                            onClick={() => handleOpenManualGrab(artistName, trackAlbum || '')}
                            sx={{ fontWeight: 700, whiteSpace: 'nowrap' }}
                          >
                            Manual
                          </Button>
                        </>
                      ) : (
                        <Box display="flex" gap={1} flexWrap="wrap">
                          <Button
                            variant="contained"
                            color="success"
                            size="small"
                            startIcon={downloadingKeys.has(albumKey) ? <CircularProgress size={14} color="inherit" /> : <DownloadIcon />}
                            disabled={downloadingKeys.has(albumKey)}
                            onClick={() => handleDownloadAlbum(
                              artistName, 
                              titleLabel, 
                              selectedVersion?.albumStatus || item.albumStatus, 
                              selectedVersion?.upgradeAvailable || item.upgradeAvailable
                            )}
                            sx={{ fontWeight: 700, whiteSpace: 'nowrap' }}
                          >
                            Download
                          </Button>
                          <Button
                            variant="outlined"
                            color="warning"
                            size="small"
                            onClick={() => handleOpenManualGrab(artistName, titleLabel)}
                            sx={{ fontWeight: 700, whiteSpace: 'nowrap' }}
                          >
                            Manual
                          </Button>
                        </Box>
                      )}
                    </Box>
                  </ListItem>
                );
              })}
            </List>
          )}
        </CardContent>
      </Card>
      {selectedLyricsTrack && (
        <LyricsPreviewDialog
          open={lyricsOpen}
          onClose={() => {
            setLyricsOpen(false);
            setSelectedLyricsTrack(null);
          }}
          defaultArtist={selectedLyricsTrack.artist}
          defaultTitle={selectedLyricsTrack.title}
          defaultAlbum={selectedLyricsTrack.album}
          localDuration={selectedLyricsTrack.duration}
          onDownloadQueued={() => {
            notify(`Staged lyrics & queued search/download for "${selectedLyricsTrack.artist} - ${selectedLyricsTrack.title}".`, "success");
          }}
        />
      )}

      <Dialog
        open={manualGrabOpen}
        onClose={() => !grabbingKey && setManualGrabOpen(false)}
        maxWidth="md"
        fullWidth
        fullScreen={false}
        PaperProps={{
          sx: {
            background: theme.palette.mode === 'dark' ? '#1c1b22' : '#ffffff',
            borderRadius: { xs: 0, sm: 4 },
            p: { xs: 0, sm: 2 },
            m: { xs: 0, sm: 2 },
            width: { xs: '100%', sm: 'auto' },
            maxHeight: { xs: '100%', sm: '90vh' }
          }
        }}
      >
        <DialogTitle sx={{ fontWeight: 800 }}>
          Manual Release Grab — {manualGrabArtist} - {manualGrabAlbum}
        </DialogTitle>
        <DialogContent dividers sx={{ maxHeight: '60vh', overflowX: 'hidden' }}>
          {searchingCandidates ? (
            <Box display="flex" flexDirection="column" alignItems="center" gap={2} p={4}>
              <CircularProgress size={48} />
              <Typography variant="body2" color="text.secondary">
                Searching Soulseek peers for release folders... (this takes ~8 seconds)
              </Typography>
            </Box>
          ) : candidates.length === 0 ? (
            <Box p={4} textAlign="center">
              <Typography variant="body1">No album directory candidates found.</Typography>
              <Typography variant="body2" color="text.secondary" mt={1}>
                Peer shares might be offline or using different naming.
              </Typography>
            </Box>
          ) : (
            <List>
              {candidates.map((cand, idx) => {
                const key = `${cand.username}-${cand.folder}`;
                const isFLAC = cand.sample_bitrate === 0 || (cand.files && cand.files[0]?.filename?.toLowerCase()?.endsWith('.flac'));
                const qualityStr = isFLAC ? 'FLAC / Lossless' : `${cand.sample_bitrate} kbps`;
                
                return (
                  <ListItem
                    key={idx}
                    sx={{
                      borderBottom: '1px solid rgba(255,255,255,0.08)',
                      py: 2,
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'stretch'
                    }}
                  >
                    <Box display="flex" justifyContent="space-between" alignItems="center" width="100%">
                      <ListItemText
                        primary={
                          <Typography variant="subtitle1" fontWeight={700}>
                            User: {cand.username} ({cand.file_count} files)
                          </Typography>
                        }
                        secondary={
                          <Box sx={{ mt: 0.5 }}>
                            <Typography variant="body2" color="primary" fontWeight={600} display="inline" mr={2}>
                              {qualityStr}
                            </Typography>
                            <Typography variant="caption" color="text.secondary" display="block" sx={{ wordBreak: 'break-all', mt: 0.5 }}>
                              {cand.folder}
                            </Typography>
                            <Button 
                              size="small" 
                              variant="text" 
                              onClick={() => setExpandedCandIdx(expandedCandIdx === idx ? null : idx)}
                              sx={{ mt: 0.5, textTransform: 'none', fontWeight: 600, p: 0, display: 'block' }}
                            >
                              {expandedCandIdx === idx ? 'Hide files' : 'Show files'}
                            </Button>
                          </Box>
                        }
                      />
                      <Button
                        variant="contained"
                        color="success"
                        startIcon={grabbingKey === key ? <CircularProgress size={16} color="inherit" /> : <DownloadIcon />}
                        disabled={grabbingKey !== null}
                        onClick={() => handleGrabAlbum(cand)}
                        sx={{ fontWeight: 700, minWidth: 120, height: 40 }}
                      >
                        Grab
                      </Button>
                    </Box>
                    <Collapse in={expandedCandIdx === idx} timeout="auto" unmountOnExit sx={{ width: '100%' }}>
                      <Box sx={{ mt: 2, pl: 2, borderLeft: `2px solid ${theme.palette.primary.main}`, width: '100%' }}>
                        <Typography variant="subtitle2" fontWeight={700} color="text.secondary" sx={{ mb: 1 }}>
                          Files found in this folder:
                        </Typography>
                        <TableContainer component={Paper} variant="outlined" sx={{ bgcolor: 'transparent', boxShadow: 'none' }}>
                          <Table size="small">
                            <TableHead>
                              <TableRow>
                                <TableCell>Filename</TableCell>
                                <TableCell align="right">Length</TableCell>
                                <TableCell align="right">Size</TableCell>
                                <TableCell align="right">Bitrate</TableCell>
                              </TableRow>
                            </TableHead>
                            <TableBody>
                              {cand.files && cand.files.map((file: any, fIdx: number) => {
                                const isFileFlac = file?.filename ? file.filename.toLowerCase().endsWith('.flac') : false;
                                const m = Math.floor((file.duration || 0) / 60);
                                const s = Math.floor((file.duration || 0) % 60);
                                const lenStr = file.duration ? `${m}:${s.toString().padStart(2, '0')}` : '-';
                                return (
                                  <TableRow key={fIdx}>
                                    <TableCell sx={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>{getFilenameOnly(file.filename)}</TableCell>
                                    <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>{lenStr}</TableCell>
                                    <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>{formatSize(file.size)}</TableCell>
                                    <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>{file.bitrate ? `${file.bitrate} kbps` : (isFileFlac ? 'FLAC' : 'Unknown')}</TableCell>
                                  </TableRow>
                                );
                              })}
                            </TableBody>
                          </Table>
                        </TableContainer>
                      </Box>
                    </Collapse>
                  </ListItem>
                );
              })}
            </List>
          )}
        </DialogContent>
        <DialogActions>
          <Button disabled={grabbingKey !== null} onClick={() => setManualGrabOpen(false)}>
            Close
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};
export default SearchMusic;
