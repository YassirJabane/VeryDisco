import React, { useEffect, useState } from 'react';
import { 
  Box, Card, CardContent, Typography, Grid, Button, IconButton, 
  Dialog, DialogTitle, DialogContent, DialogActions, TextField, 
  CircularProgress, Avatar, Tooltip, CardActionArea, Divider, Chip,
  List, ListItem, ListItemAvatar, ListItemText, useTheme, useMediaQuery
} from '@mui/material';
import { 
  Person as ArtistIcon, 
  Add as AddIcon, 
  Delete as DeleteIcon, 
  Close as CloseIcon, 
  Download as DownloadIcon,
  Album as AlbumIcon,
  Visibility as ShowIcon,
  VisibilityOff as HideIcon,
  MusicNote as MusicIcon,
  Favorite as FavoriteIcon,
  HeartBroken as HeartBrokenIcon,
  DeleteForever as DangerousIcon,
  LibraryMusic as LibraryIcon
} from '@mui/icons-material';
import apiService from '../api';
import { useNotification } from '../context/NotificationContext';

interface PinnedArtist {
  id: number;
  artist_name: string;
  mbid?: string;
  deezer_id?: number;
  picture_url: string;
}

interface Release {
  id: number;
  title: string;
  cover_medium: string;
  release_date: string;
  record_type: 'album' | 'single' | 'ep';
  // Enriched properties
  exists?: boolean;
  albumStatus?: 'full' | 'partial' | 'missing';
  upgradeAvailable?: boolean;
  qualityStatus?: 'same' | 'worse' | 'better';
  checking?: boolean;
}

export const MyArtists: React.FC = () => {
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down('sm'));
  const { notify, confirm } = useNotification();
  const [artists, setArtists] = useState<PinnedArtist[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [filterText, setFilterText] = useState<string>('');
  
  // Add Artist Modal
  const [addDialogOpen, setAddDialogOpen] = useState<boolean>(false);
  const [searchName, setSearchName] = useState<string>('');
  const [searching, setSearching] = useState<boolean>(false);
  const [searchResult, setSearchResult] = useState<any>(null);
  const [searchError, setSearchError] = useState<string>('');

  // Artist Detail Modal
  const [detailArtist, setDetailArtist] = useState<PinnedArtist | null>(null);
  const [releases, setReleases] = useState<any[]>([]);
  const [releasesLoading, setReleasesLoading] = useState<boolean>(false);
  const [hideAlbums, setHideAlbums] = useState<boolean>(false);
  const [downloadingKeys, setDownloadingKeys] = useState<Set<string>>(new Set());
  const [expandedAlbumId, setExpandedAlbumId] = useState<number | null>(null);

  const fetchArtists = async () => {
    setLoading(true);
    try {
      const data = await apiService.getPinnedArtists();
      setArtists(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error("Failed to load pinned artists", err);
      setArtists([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchArtists();
  }, []);

  const handleOpenAddDialog = () => {
    setSearchName('');
    setSearchResult(null);
    setSearchError('');
    setAddDialogOpen(true);
  };

  const handleSearchArtist = async (nameToSearch?: string) => {
    const q = nameToSearch || searchName;
    if (!q.trim()) return;
    setSearching(true);
    setSearchError('');
    setSearchResult(null);
    try {
      const resp = await apiService.searchDeezer(q, 'artist');
      const data = resp.data || [];
      if (data.length === 0) {
        setSearchError(`No artist found matching "${q}"`);
      } else {
        setSearchResult(data[0]); // Pick the first match
      }
    } catch (err) {
      setSearchError("Failed to search artist on Deezer.");
    } finally {
      setSearching(false);
    }
  };

  const handlePinArtist = async () => {
    if (!searchResult) return;
    try {
      await apiService.pinArtist(searchResult.name, searchResult.id, searchResult.picture_medium);
      setAddDialogOpen(false);
      notify(`Pinned artist "${searchResult.name}"!`, "success");
      fetchArtists();
    } catch (err: any) {
      notify(err.response?.data?.detail || "Failed to pin artist.", "error");
    }
  };

  const handleUnpinArtist = async (e: React.MouseEvent, id: number, name: string) => {
    e.stopPropagation(); // Prevent opening detail modal
    confirm({
      title: `Delete "${name}"?`,
      message: `Are you sure you want to delete "${name}"? This will permanently delete their music files from disk and Navidrome.`,
      confirmText: "Delete Artist",
      isDangerous: true,
      onConfirm: async () => {
        try {
          await apiService.unpinArtist(id);
          notify(`Deleted "${name}".`, "info");
          fetchArtists();
        } catch (err) {
          notify("Failed to delete artist.", "error");
        }
      }
    });
  };

  const handlePurgeAllClick = () => {
    confirm({
      title: "🚨 DANGER: Purge All Pinned Artists?",
      message: "Are you sure you want to purge all pinned artists? This will wipe the artist database clean so you can recreate it from scratch. This action cannot be undone!",
      confirmText: "Purge All Artists",
      cancelText: "Cancel",
      isDangerous: true,
      onConfirm: async () => {
        try {
          await apiService.purgePinnedArtists();
          notify("Successfully purged all artists and recreated artist database!", "success");
          fetchArtists();
        } catch (err: any) {
          notify(err.response?.data?.detail || "Failed to purge artists.", "error");
        }
      }
    });
  };

  const handleOpenDetail = async (artist: PinnedArtist) => {
    setDetailArtist(artist);
    setReleasesLoading(true);
    setReleases([]);
    setExpandedAlbumId(null);
    try {
      const data = await apiService.getArtistReleases(artist.mbid || artist.deezer_id || artist.id, artist.mbid);
      const listData = Array.isArray(data) ? data : [];
      
      // Initially set checking=true for all releases
      const initialReleases = listData
        .filter((r: any) => r && typeof r === 'object')
        .map((r: any) => ({
          id: r.id || Math.random(),
          title: r.title || 'Unknown Title',
          cover_medium: r.cover_medium || r.cover_small || r.cover || '',
          release_date: r.release_date || '',
          record_type: (r.record_type || 'album').toLowerCase(),
          checking: true,
          tracks: []
        }));
      setReleases(initialReleases);

      // Perform library existence checks in throttled batches of 5
      const batchSize = 5;
      for (let i = 0; i < initialReleases.length; i += batchSize) {
        const batch = initialReleases.slice(i, i + batchSize);
        await Promise.all(batch.map(async (r: Release) => {
          const index = initialReleases.findIndex(item => item.id === r.id);
          try {
            const isAlbum = r.record_type === 'album' || r.record_type === 'ep';
            const check = await apiService.checkTrackExists(
              artist.artist_name, 
              r.title, 
              isAlbum ? r.id : undefined
            );
            
            setReleases(prev => {
              const next = [...prev];
              if (index >= 0 && next[index]) {
                if (isAlbum) {
                  next[index] = {
                    ...next[index],
                    exists: check.exists,
                    albumStatus: check.status,
                    upgradeAvailable: check.upgrade_available,
                    tracks: check.tracks || [],
                    checking: false
                  };
                } else {
                  next[index] = {
                    ...next[index],
                    exists: check.exists,
                    qualityStatus: check.quality_status,
                    checking: false
                  };
                }
              }
              return next;
            });
          } catch {
            setReleases(prev => {
              const next = [...prev];
              if (index >= 0 && next[index]) {
                next[index] = { ...next[index], checking: false };
              }
              return next;
            });
          }
        }));
      }

    } catch (err) {
      console.error("Failed to load artist releases", err);
    } finally {
      setReleasesLoading(false);
    }
  };

  const handleDownloadRelease = async (release: Release) => {
    if (!detailArtist) return;
    const isAlbum = release.record_type === 'album' || release.record_type === 'ep';
    const key = `${isAlbum ? 'album' : 'track'}-${detailArtist.artist_name}-${release.title}`;
    
    let force = false;
    if (isAlbum) {
      if (release.albumStatus === 'full' && !release.upgradeAvailable) {
        const confirm = window.confirm(`You already have the album "${detailArtist.artist_name} - ${release.title}" fully in the desired quality. Do you want to download/overwrite it anyway?`);
        if (!confirm) return;
        force = true;
      }
    } else {
      if (release.exists && release.qualityStatus !== 'worse') {
        const confirm = window.confirm(`You already have the track "${detailArtist.artist_name} - ${release.title}" in the desired quality. Do you want to download/overwrite it anyway?`);
        if (!confirm) return;
        force = true;
      }
    }

    setDownloadingKeys(prev => new Set(prev).add(key));
    try {
      if (isAlbum) {
        await apiService.downloadAlbum(detailArtist.artist_name, release.title, force);
        alert(`Full album download queued for "${detailArtist.artist_name} - ${release.title}".`);
      } else {
        await apiService.downloadTrack(detailArtist.artist_name, release.title, release.title, force);
        alert(`Single track download queued for "${detailArtist.artist_name} - ${release.title}".`);
      }
    } catch (err: any) {
      alert(err.response?.data?.detail || "Failed to trigger download.");
    } finally {
      setDownloadingKeys(prev => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  };

  const albums = releases.filter(r => r && r.record_type === 'album');
  const singlesAndEps = releases.filter(r => r && (r.record_type === 'single' || r.record_type === 'ep'));
  const otherReleases = releases.filter(r => r && ['live', 'compilation', 'mixtape', 'remix', 'demo', 'other', 'interview', 'soundtrack', 'broadcast'].includes(r.record_type));

  const filteredArtists = artists.filter(a => 
    a.artist_name.toLowerCase().includes(filterText.toLowerCase())
  );

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <Box display="flex" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={2}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 800 }}>My Artists</Typography>
          <Typography variant="body2" color="text.secondary">
            Browse pinned artist discographies, check library coverage, and queue downloads.
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', width: { xs: '100%', md: 'auto' } }}>
          <TextField
            size="small"
            placeholder="Search artists..."
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            sx={{ width: 250 }}
          />
          <Button 
            variant="contained" 
            startIcon={<AddIcon />} 
            onClick={handleOpenAddDialog}
            sx={{ fontWeight: 700, flexShrink: 0 }}
          >
            Add Artist
          </Button>
          <Button 
            variant="contained" 
            color="error" 
            startIcon={<DangerousIcon />} 
            onClick={handlePurgeAllClick}
            sx={{ 
              fontWeight: 700, 
              flexShrink: 0,
              bgcolor: '#d32f2f',
              boxShadow: '0 0 12px rgba(211, 47, 47, 0.4)',
              '&:hover': {
                bgcolor: '#b71c1c',
                boxShadow: '0 0 18px rgba(211, 47, 47, 0.8)'
              }
            }}
          >
            Purge All
          </Button>
        </Box>
      </Box>

      {loading ? (
        <Box display="flex" justifyContent="center" py={8}><CircularProgress /></Box>
      ) : artists.length === 0 ? (
        <Card sx={{ border: '1px dashed', borderColor: 'divider', borderRadius: 4, bgcolor: 'transparent' }}>
          <CardContent sx={{ py: 6, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
            <ArtistIcon sx={{ fontSize: 48, color: 'text.secondary' }} />
            <Typography variant="h6" color="text.secondary" sx={{ fontWeight: 700 }}>No artists pinned yet</Typography>
            <Typography variant="body2" color="text.secondary" textAlign="center">
              Click the "Add Artist" button to pin your first artist.
            </Typography>
          </CardContent>
        </Card>
      ) : filteredArtists.length === 0 && filterText.trim().length > 0 ? (
        <Card sx={{ border: '1px dashed', borderColor: 'warning.main', borderRadius: 4, bgcolor: 'transparent' }}>
          <CardContent sx={{ py: 6, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
            <ArtistIcon sx={{ fontSize: 48, color: 'warning.main' }} />
            <Typography variant="h6" color="text.secondary" sx={{ fontWeight: 700 }}>
              Artist "{filterText}" is not in your library
            </Typography>
            <Typography variant="body2" color="text.secondary" textAlign="center" mb={1}>
              Would you like to search Deezer and add them to your pinned artists list?
            </Typography>
            <Button 
              variant="contained" 
              color="warning"
              onClick={() => {
                setSearchName(filterText);
                setSearchResult(null);
                setSearchError('');
                setAddDialogOpen(true);
                handleSearchArtist(filterText);
              }}
              sx={{ fontWeight: 700 }}
            >
              Search & Pin "{filterText}"
            </Button>
          </CardContent>
        </Card>
      ) : (
        <Grid container spacing={{ xs: 2, sm: 3 }}>
          {filteredArtists.map((artist) => (
            <Grid item xs={6} sm={4} md={3} lg={2} key={artist.id}>
              <Card 
                sx={{ 
                  borderRadius: 4, 
                  overflow: 'visible',
                  position: 'relative',
                  '&:hover .delete-btn': { opacity: 1 }
                }}
              >
                <IconButton
                  className="delete-btn"
                  onClick={(e) => handleUnpinArtist(e, artist.id, artist.artist_name)}
                  sx={{
                    position: 'absolute',
                    top: -10,
                    right: -10,
                    bgcolor: 'error.main',
                    color: 'white',
                    opacity: 0,
                    transition: 'opacity 0.2s',
                    zIndex: 10,
                    width: 28,
                    height: 28,
                    '&:hover': { bgcolor: 'error.dark' }
                  }}
                  size="small"
                >
                  <DeleteIcon sx={{ fontSize: 16 }} />
                </IconButton>

                <CardActionArea 
                  onClick={() => handleOpenDetail(artist)}
                  sx={{ borderRadius: 4 }}
                >
                  <CardContent sx={{ p: 2, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1.5 }}>
                    <Avatar 
                      src={artist.picture_url} 
                      variant="rounded"
                      sx={{ 
                        width: '100%', 
                        height: 'auto', 
                        aspectRatio: '1/1',
                        borderRadius: 3,
                        boxShadow: '0 4px 12px rgba(0,0,0,0.15)'
                      }}
                    >
                      <ArtistIcon sx={{ fontSize: 48 }} />
                    </Avatar>
                    <Typography 
                      variant="subtitle2" 
                      textAlign="center" 
                      noWrap 
                      sx={{ fontWeight: 800, width: '100%' }}
                    >
                      {artist.artist_name}
                    </Typography>
                  </CardContent>
                </CardActionArea>
              </Card>
            </Grid>
          ))}
        </Grid>
      )}

      {/* Add Artist Dialog */}
      <Dialog open={addDialogOpen} onClose={() => setAddDialogOpen(false)} fullWidth maxWidth="xs">
        <DialogTitle sx={{ fontWeight: 800 }}>Pin New Artist</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2.5, pt: 1 }}>
          <TextField
            autoFocus
            fullWidth
            label="Artist Name"
            placeholder="e.g. Michael Jackson"
            value={searchName}
            onChange={(e) => setSearchName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearchArtist()}
          />

          {searching && <Box display="flex" justifyContent="center"><CircularProgress size={24} /></Box>}

          {searchError && (
            <Typography variant="body2" color="error" textAlign="center">
              {searchError}
            </Typography>
          )}

          {searchResult && (
            <Card sx={{ border: '1px solid', borderColor: 'divider', bgcolor: 'action.hover', borderRadius: 3 }}>
              <CardContent sx={{ display: 'flex', alignItems: 'center', gap: 2, py: 2 }}>
                <Avatar src={searchResult.picture_medium} sx={{ width: 56, height: 56, boxShadow: '0 2px 8px rgba(0,0,0,0.1)' }} />
                <Typography sx={{ fontWeight: 800 }}>{searchResult.name}</Typography>
              </CardContent>
            </Card>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 3 }}>
          <Button onClick={() => setAddDialogOpen(false)} color="inherit">Cancel</Button>
          {searchResult ? (
            <Button variant="contained" color="primary" onClick={handlePinArtist} sx={{ fontWeight: 700 }}>Pin Artist</Button>
          ) : (
            <Button variant="contained" onClick={() => handleSearchArtist()} disabled={!searchName.trim()} sx={{ fontWeight: 700 }}>Search</Button>
          )}
        </DialogActions>
      </Dialog>

      {/* Artist Details Popup Modal */}
      <Dialog 
        open={detailArtist !== null} 
        onClose={() => setDetailArtist(null)} 
        fullWidth 
        maxWidth="md"
        PaperProps={{ sx: { borderRadius: 4, m: { xs: 1.5, sm: 3 }, maxHeight: '85vh' } }}>
        {detailArtist && (
          <>
            <DialogTitle sx={{ m: 0, p: { xs: 2, sm: 3 }, display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid', borderColor: 'divider' }}>
              <Box display="flex" alignItems="center" gap={2}>
                <Avatar src={detailArtist.picture_url} sx={{ width: 48, height: 48, boxShadow: '0 2px 8px rgba(0,0,0,0.1)' }} />
                <Typography variant="h5" sx={{ fontWeight: 800 }}>{detailArtist.artist_name}</Typography>
              </Box>
              <IconButton onClick={() => setDetailArtist(null)}>
                <CloseIcon />
              </IconButton>
            </DialogTitle>

            <DialogContent sx={{ p: 3, display: 'flex', flexDirection: 'column', gap: 3, maxHeight: '70vh' }}>
              {releasesLoading && releases.length === 0 ? (
                <Box display="flex" justifyContent="center" py={6}><CircularProgress /></Box>
              ) : (
                <>
                  {/* Category: Albums */}
                  {albums.length > 0 && (
                    <Box display="flex" flexDirection="column" gap={1.5}>
                      <Box display="flex" justifyContent="space-between" alignItems="center">
                        <Typography variant="h6" sx={{ fontWeight: 800, display: 'flex', alignItems: 'center', gap: 1 }}>
                          <AlbumIcon color="primary" /> Albums ({albums.length})
                        </Typography>
                        <Button 
                          size="small" 
                          startIcon={hideAlbums ? <ShowIcon /> : <HideIcon />}
                          onClick={() => setHideAlbums(!hideAlbums)}
                        >
                          {hideAlbums ? 'Show' : 'Hide'}
                        </Button>
                      </Box>
                      
                      {!hideAlbums && (
                        <List sx={{ bgcolor: 'action.hover', borderRadius: 3, overflow: 'hidden' }}>
                          {albums.map((release) => {
                            const dlKey = `album-${detailArtist.artist_name}-${release.title}`;
                            const isDl = downloadingKeys.has(dlKey);
                            const isExpanded = expandedAlbumId === release.id;
                            return (
                              <React.Fragment key={release.id}>
                                <ListItem divider sx={{ py: 1.5, px: 2, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 2 }}>
                                  <Box 
                                    display="flex" 
                                    alignItems="center" 
                                    gap={2}
                                    onClick={() => setExpandedAlbumId(isExpanded ? null : release.id)}
                                    sx={{ cursor: 'pointer', '&:hover': { opacity: 0.85 }, flexGrow: 1 }}
                                  >
                                    <Avatar src={release.cover_medium} variant="rounded" sx={{ width: 50, height: 50 }} />
                                    <ListItemText 
                                      primary={<Typography sx={{ fontWeight: 700 }}>{release.title}</Typography>}
                                      secondary={release.release_date ? `Released: ${new Date(release.release_date).getFullYear()}` : ''}
                                    />
                                  </Box>
                                  <Box display="flex" alignItems="center" gap={1.5}>
                                    {release.checking ? (
                                      <CircularProgress size={16} />
                                    ) : (
                                      (() => {
                                        const status = release.albumStatus || 'missing';
                                        const upgrade = release.upgradeAvailable || false;
                                        if (status === 'full') {
                                          return (
                                            <Chip 
                                              label={upgrade ? "Fully in Library (Upgrade)" : "Fully in Library"} 
                                              size="small"
                                              sx={{ 
                                                bgcolor: upgrade ? 'rgba(237, 108, 2, 0.1)' : 'rgba(46, 125, 50, 0.1)', 
                                                color: upgrade ? '#ed6c02' : '#2e7d32', 
                                                fontWeight: 700,
                                                border: upgrade ? '1px solid rgba(237, 108, 2, 0.3)' : '1px solid rgba(46, 125, 50, 0.3)'
                                              }}
                                            />
                                          );
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
                                    <IconButton 
                                      color="primary" 
                                      disabled={isDl || release.checking} 
                                      onClick={() => handleDownloadRelease(release)}
                                    >
                                      {isDl ? <CircularProgress size={20} color="inherit" /> : <DownloadIcon />}
                                    </IconButton>
                                  </Box>
                                </ListItem>

                                {/* Collapsible Tracklist (same system as Library Manager) */}
                                {isExpanded && release.tracks && release.tracks.length > 0 && (
                                  <Box sx={{ pl: { xs: 2, sm: 8 }, pr: 2, py: 1.5, bgcolor: 'action.selected', borderBottom: '1px solid', borderColor: 'divider' }}>
                                    <Typography variant="subtitle2" sx={{ fontWeight: 800, mb: 1, color: 'text.secondary' }}>Tracklist</Typography>
                                    <List disablePadding>
                                      {release.tracks.map((track: any, tidx: number) => {
                                        const trackDlKey = `track-${detailArtist.artist_name}-${track.title}`;
                                        const isTrackDl = downloadingKeys.has(trackDlKey);
                                        return (
                                          <ListItem 
                                            key={tidx} 
                                            sx={{ 
                                              py: 0.5, 
                                              px: 0, 
                                              display: 'flex', 
                                              justifyContent: 'space-between',
                                              '&:hover': { bgcolor: 'action.hover' }
                                            }}
                                          >
                                            <Box display="flex" alignItems="center" gap={1.5}>
                                              <Typography variant="body2" sx={{ color: 'text.secondary', width: 24, textAlign: 'right' }}>
                                                {tidx + 1}
                                              </Typography>
                                              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                                                {track.title}
                                              </Typography>
                                            </Box>
                                            <Box display="flex" alignItems="center" gap={1}>
                                              {track.exists ? (
                                                <Chip 
                                                  label={track.quality_status === 'better' ? 'FLAC' : 'MP3'} 
                                                  size="small" 
                                                  color={track.quality_status === 'worse' ? 'warning' : 'success'} 
                                                  variant="outlined"
                                                  sx={{ fontSize: '0.75rem', height: 20 }}
                                                />
                                              ) : (
                                                <Chip 
                                                  label="Missing" 
                                                  size="small" 
                                                  color="error" 
                                                  variant="outlined"
                                                  sx={{ fontSize: '0.75rem', height: 20 }}
                                                />
                                              )}
                                              <Tooltip title="Love track on ListenBrainz">
                                                <IconButton 
                                                  size="small" 
                                                  color="error"
                                                  sx={{ display: { xs: 'none', sm: 'inline-flex' } }}
                                                  onClick={async () => {
                                                    try {
                                                      await apiService.likeTrack(detailArtist.artist_name, track.title, release.title, 1);
                                                      alert("Loved on ListenBrainz!");
                                                    } catch (de: any) {
                                                      alert(de.response?.data?.detail || "Failed to submit love feedback.");
                                                    }
                                                  }}
                                                >
                                                  <FavoriteIcon sx={{ fontSize: 16 }} />
                                                </IconButton>
                                              </Tooltip>
                                              <Tooltip title="Hate track on ListenBrainz">
                                                <IconButton 
                                                  size="small" 
                                                  color="warning"
                                                  sx={{ display: { xs: 'none', sm: 'inline-flex' } }}
                                                  onClick={async () => {
                                                    try {
                                                      await apiService.likeTrack(detailArtist.artist_name, track.title, release.title, -1);
                                                      alert("Hated on ListenBrainz!");
                                                    } catch (de: any) {
                                                      alert(de.response?.data?.detail || "Failed to submit hate feedback.");
                                                    }
                                                  }}
                                                >
                                                  <HeartBrokenIcon sx={{ fontSize: 16 }} />
                                                </IconButton>
                                              </Tooltip>
                                              <IconButton 
                                                size="small" 
                                                color="primary"
                                                disabled={isTrackDl || (track.exists && track.quality_status !== 'worse')}
                                                onClick={async () => {
                                                  setDownloadingKeys(prev => new Set(prev).add(trackDlKey));
                                                  try {
                                                    await apiService.downloadTrack(detailArtist.artist_name, track.title, track.title, false);
                                                    alert(`Download queued for track "${detailArtist.artist_name} - ${track.title}".`);
                                                  } catch (de: any) {
                                                    alert(de.response?.data?.detail || "Failed to download track.");
                                                  } finally {
                                                    setDownloadingKeys(prev => {
                                                      const next = new Set(prev);
                                                      next.delete(trackDlKey);
                                                      return next;
                                                    });
                                                  }
                                                }}
                                              >
                                                {isTrackDl ? <CircularProgress size={14} color="inherit" /> : <DownloadIcon sx={{ fontSize: 16 }} />}
                                              </IconButton>
                                            </Box>
                                          </ListItem>
                                        );
                                      })}
                                    </List>
                                  </Box>
                                )}
                              </React.Fragment>
                            );
                          })}
                        </List>
                      )}
                    </Box>
                  )}

                  {albums.length > 0 && singlesAndEps.length > 0 && <Divider />}

                  {/* Category: Singles & EPs */}
                  {singlesAndEps.length > 0 && (
                    <Box display="flex" flexDirection="column" gap={1.5}>
                      <Typography variant="h6" sx={{ fontWeight: 800, display: 'flex', alignItems: 'center', gap: 1 }}>
                        <MusicIcon color="primary" /> Singles & EPs ({singlesAndEps.length})
                      </Typography>
                      
                      <List sx={{ bgcolor: 'action.hover', borderRadius: 3, overflow: 'hidden' }}>
                        {singlesAndEps.map((release) => {
                          const isAlbumType = release.record_type === 'ep';
                          const dlKey = `${isAlbumType ? 'album' : 'track'}-${detailArtist.artist_name}-${release.title}`;
                          const isDl = downloadingKeys.has(dlKey);
                          const isExpanded = expandedAlbumId === release.id;
                          return (
                            <React.Fragment key={release.id}>
                              <ListItem divider sx={{ py: 1.5, px: 2, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 2 }}>
                                <Box 
                                  display="flex" 
                                  alignItems="center" 
                                  gap={2}
                                  onClick={() => isAlbumType && setExpandedAlbumId(isExpanded ? null : release.id)}
                                  sx={{ cursor: isAlbumType ? 'pointer' : 'default', '&:hover': { opacity: isAlbumType ? 0.85 : 1 }, flexGrow: 1 }}
                                >
                                  <Avatar src={release.cover_medium} variant="rounded" sx={{ width: 50, height: 50 }} />
                                  <ListItemText 
                                    primary={<Typography sx={{ fontWeight: 700 }}>{release.title}</Typography>}
                                    secondary={`${(release.record_type || 'single').toUpperCase()} ${release.release_date ? `• ${new Date(release.release_date).getFullYear()}` : ''}`}
                                  />
                                </Box>
                                <Box display="flex" alignItems="center" gap={1.5}>
                                  {release.checking ? (
                                    <CircularProgress size={16} />
                                  ) : (
                                    (() => {
                                      if (isAlbumType) {
                                        const status = release.albumStatus || 'missing';
                                        const upgrade = release.upgradeAvailable || false;
                                        if (status === 'full') {
                                          return (
                                            <Chip 
                                              label={upgrade ? "Fully in Library (Upgrade)" : "Fully in Library"} 
                                              size="small"
                                              sx={{ 
                                                bgcolor: upgrade ? 'rgba(237, 108, 2, 0.1)' : 'rgba(46, 125, 50, 0.1)', 
                                                color: upgrade ? '#ed6c02' : '#2e7d32', 
                                                fontWeight: 700,
                                                border: upgrade ? '1px solid rgba(237, 108, 2, 0.3)' : '1px solid rgba(46, 125, 50, 0.3)'
                                              }}
                                            />
                                          );
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
                                      } else {
                                        if (release.exists) {
                                          const upgrade = release.qualityStatus === 'worse';
                                          return (
                                            <Chip 
                                              label={upgrade ? "Already in Library (Upgrade)" : "Already in Library"} 
                                              size="small"
                                              sx={{ 
                                                bgcolor: upgrade ? 'rgba(237, 108, 2, 0.1)' : 'rgba(46, 125, 50, 0.1)', 
                                                color: upgrade ? '#ed6c02' : '#2e7d32', 
                                                fontWeight: 700,
                                                border: upgrade ? '1px solid rgba(237, 108, 2, 0.3)' : '1px solid rgba(46, 125, 50, 0.3)'
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
                                      }
                                    })()
                                  )}
                                  <IconButton 
                                    color="primary" 
                                    disabled={isDl || release.checking} 
                                    onClick={() => handleDownloadRelease(release)}
                                  >
                                    {isDl ? <CircularProgress size={20} color="inherit" /> : <DownloadIcon />}
                                  </IconButton>
                                </Box>
                              </ListItem>

                              {/* Collapsible Tracklist for EP */}
                              {isExpanded && isAlbumType && release.tracks && release.tracks.length > 0 && (
                                <Box sx={{ pl: { xs: 2, sm: 8 }, pr: 2, py: 1.5, bgcolor: 'action.selected', borderBottom: '1px solid', borderColor: 'divider' }}>
                                  <Typography variant="subtitle2" sx={{ fontWeight: 800, mb: 1, color: 'text.secondary' }}>Tracklist</Typography>
                                  <List disablePadding>
                                    {release.tracks.map((track: any, tidx: number) => {
                                      const trackDlKey = `track-${detailArtist.artist_name}-${track.title}`;
                                      const isTrackDl = downloadingKeys.has(trackDlKey);
                                      return (
                                        <ListItem 
                                          key={tidx} 
                                          sx={{ 
                                            py: 0.5, 
                                            px: 0, 
                                            display: 'flex', 
                                            justifyContent: 'space-between',
                                            '&:hover': { bgcolor: 'action.hover' }
                                          }}
                                        >
                                          <Box display="flex" alignItems="center" gap={1.5}>
                                            <Typography variant="body2" sx={{ color: 'text.secondary', width: 24, textAlign: 'right' }}>
                                              {tidx + 1}
                                            </Typography>
                                            <Typography variant="body2" sx={{ fontWeight: 600 }}>
                                              {track.title}
                                            </Typography>
                                          </Box>
                                          <Box display="flex" alignItems="center" gap={1}>
                                            {track.exists ? (
                                              <Chip 
                                                label={track.quality_status === 'better' ? 'FLAC' : 'MP3'} 
                                                size="small" 
                                                color={track.quality_status === 'worse' ? 'warning' : 'success'} 
                                                variant="outlined"
                                                sx={{ fontSize: '0.75rem', height: 20 }}
                                              />
                                            ) : (
                                              <Chip 
                                                label="Missing" 
                                                size="small" 
                                                color="error" 
                                                variant="outlined"
                                                sx={{ fontSize: '0.75rem', height: 20 }}
                                              />
                                            )}
                                              <Tooltip title="Love track on ListenBrainz">
                                                <IconButton 
                                                  size="small" 
                                                  color="error"
                                                  sx={{ display: { xs: 'none', sm: 'inline-flex' } }}
                                                  onClick={async () => {
                                                    try {
                                                      await apiService.likeTrack(detailArtist.artist_name, track.title, release.title, 1);
                                                      alert("Loved on ListenBrainz!");
                                                    } catch (de: any) {
                                                      alert(de.response?.data?.detail || "Failed to submit love feedback.");
                                                    }
                                                  }}
                                                >
                                                  <FavoriteIcon sx={{ fontSize: 16 }} />
                                                </IconButton>
                                              </Tooltip>
                                              <Tooltip title="Hate track on ListenBrainz">
                                                <IconButton 
                                                  size="small" 
                                                  color="warning"
                                                  sx={{ display: { xs: 'none', sm: 'inline-flex' } }}
                                                  onClick={async () => {
                                                    try {
                                                      await apiService.likeTrack(detailArtist.artist_name, track.title, release.title, -1);
                                                      alert("Hated on ListenBrainz!");
                                                    } catch (de: any) {
                                                      alert(de.response?.data?.detail || "Failed to submit hate feedback.");
                                                    }
                                                  }}
                                                >
                                                  <HeartBrokenIcon sx={{ fontSize: 16 }} />
                                                </IconButton>
                                              </Tooltip>
                                              <IconButton 
                                                size="small" 
                                                color="primary"
                                                disabled={isTrackDl || (track.exists && track.quality_status !== 'worse')}
                                                onClick={async () => {
                                                  setDownloadingKeys(prev => new Set(prev).add(trackDlKey));
                                                  try {
                                                    await apiService.downloadTrack(detailArtist.artist_name, track.title, track.title, false);
                                                    alert(`Download queued for track "${detailArtist.artist_name} - ${track.title}".`);
                                                  } catch (de: any) {
                                                    alert(de.response?.data?.detail || "Failed to download track.");
                                                  } finally {
                                                    setDownloadingKeys(prev => {
                                                      const next = new Set(prev);
                                                      next.delete(trackDlKey);
                                                      return next;
                                                    });
                                                  }
                                                }}
                                              >
                                                {isTrackDl ? <CircularProgress size={14} color="inherit" /> : <DownloadIcon sx={{ fontSize: 16 }} />}
                                              </IconButton>
                                            </Box>
                                        </ListItem>
                                      );
                                    })}
                                  </List>
                                </Box>
                              )}
                            </React.Fragment>
                          );
                        })}
                      </List>
                    </Box>
                  )}

                  {otherReleases.length > 0 && <Divider />}

                  {/* Category: Compilations, Live, Mixtapes & Demos */}
                  {otherReleases.length > 0 && (
                    <Box display="flex" flexDirection="column" gap={1.5}>
                      <Typography variant="h6" sx={{ fontWeight: 800, display: 'flex', alignItems: 'center', gap: 1 }}>
                        <LibraryIcon color="primary" /> Other Releases ({otherReleases.length})
                      </Typography>
                      
                      <List sx={{ bgcolor: 'action.hover', borderRadius: 3, overflow: 'hidden' }}>
                        {otherReleases.map((release) => {
                          const dlKey = `album-${detailArtist.artist_name}-${release.title}`;
                          const isDl = downloadingKeys.has(dlKey);
                          const isExpanded = expandedAlbumId === release.id;
                          return (
                            <React.Fragment key={release.id}>
                              <ListItem divider sx={{ py: 1.5, px: 2, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 2 }}>
                                <Box 
                                  display="flex" 
                                  alignItems="center" 
                                  gap={2}
                                  onClick={() => setExpandedAlbumId(isExpanded ? null : release.id)}
                                  sx={{ cursor: 'pointer', '&:hover': { opacity: 0.85 }, flexGrow: 1 }}
                                >
                                  <Avatar src={release.cover_medium} variant="rounded" sx={{ width: 50, height: 50 }} />
                                  <ListItemText 
                                    primary={<Typography sx={{ fontWeight: 700 }}>{release.title}</Typography>}
                                    secondary={`${(release.record_type || 'other').toUpperCase()} ${release.release_date ? `• ${new Date(release.release_date).getFullYear()}` : ''}`}
                                  />
                                </Box>
                                <Box display="flex" alignItems="center" gap={1.5}>
                                  {release.checking ? (
                                    <CircularProgress size={16} />
                                  ) : (
                                    (() => {
                                      const status = release.albumStatus || 'missing';
                                      const upgrade = release.upgradeAvailable || false;
                                      if (status === 'full') {
                                        return (
                                          <Chip 
                                            label={upgrade ? "Fully in Library (Upgrade)" : "Fully in Library"} 
                                            size="small"
                                            sx={{ 
                                              bgcolor: upgrade ? 'rgba(237, 108, 2, 0.1)' : 'rgba(46, 125, 50, 0.1)', 
                                              color: upgrade ? '#ed6c02' : '#2e7d32', 
                                              fontWeight: 700,
                                              border: upgrade ? '1px solid rgba(237, 108, 2, 0.3)' : '1px solid rgba(46, 125, 50, 0.3)'
                                            }}
                                          />
                                        );
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
                                  <IconButton 
                                    color="primary" 
                                    disabled={isDl || release.checking} 
                                    onClick={() => handleDownloadRelease(release)}
                                  >
                                    {isDl ? <CircularProgress size={20} color="inherit" /> : <DownloadIcon />}
                                  </IconButton>
                                </Box>
                              </ListItem>
                            </React.Fragment>
                          );
                        })}
                      </List>
                    </Box>
                  )}
                </>
              )}
            </DialogContent>
          </>
        )}
      </Dialog>
    </Box>
  );
};

export default MyArtists;
