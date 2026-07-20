import React, { useState, useEffect } from 'react';
import {
  Box, Typography, Button, Paper, Stack, CircularProgress,
  List, ListItem, ListItemText, Divider, Alert, TextField, IconButton
} from '@mui/material';
import {
  RecentActors as ArtistsIcon,
  Save as SaveIcon,
  Delete as DeleteIcon,
  Add as AddIcon,
  Search as SearchIcon
} from '@mui/icons-material';
import apiService from '../api';

const ArtistAliases: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [aliases, setAliases] = useState<{ original: string; mapped: string }[]>([]);
  const [newOriginal, setNewOriginal] = useState('');
  const [newMapped, setNewMapped] = useState('');
  const [resolving, setResolving] = useState(false);

  const showToast = (msg: string, type: 'success' | 'error' = 'success') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  };

  const loadAliases = async () => {
    setLoading(true);
    try {
      const data = await apiService.getArtistAliases();
      const aliasesObj = data.aliases || {};
      const arr = Object.keys(aliasesObj).map(k => ({ original: k, mapped: aliasesObj[k] }));
      setAliases(arr);
    } catch (e: any) {
      showToast(e?.response?.data?.detail ?? 'Failed to load aliases.', 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAliases();
  }, []);

  const handleSave = async () => {
    setLoading(true);
    try {
      const payload: Record<string, string> = {};
      aliases.forEach(a => {
        if (a.original.trim() && a.mapped.trim()) {
          payload[a.original.trim()] = a.mapped.trim();
        }
      });
      await apiService.updateArtistAliases(payload);
      showToast('Aliases saved successfully.');
    } catch (e: any) {
      showToast(e?.response?.data?.detail ?? 'Failed to save aliases.', 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleAdd = () => {
    if (!newOriginal.trim() || !newMapped.trim()) {
      showToast('Both fields are required to add an alias.', 'error');
      return;
    }
    if (aliases.some(a => a.original.toLowerCase() === newOriginal.toLowerCase())) {
      showToast('This original artist alias already exists.', 'error');
      return;
    }
    setAliases([{ original: newOriginal.trim(), mapped: newMapped.trim() }, ...aliases]);
    setNewOriginal('');
    setNewMapped('');
  };

  const handleRemove = (idx: number) => {
    const newAliases = [...aliases];
    newAliases.splice(idx, 1);
    setAliases(newAliases);
  };

  const handleResolveMusicBrainz = async () => {
    if (!newOriginal.trim()) return;
    setResolving(true);
    try {
      const data = await apiService.resolveArtistMusicBrainz(newOriginal.trim());
      if (data.resolved_name) {
        setNewMapped(data.resolved_name);
        showToast(`MusicBrainz resolved "${newOriginal}" to "${data.resolved_name}"`);
      } else {
        showToast(`Could not resolve "${newOriginal}" on MusicBrainz.`, 'error');
      }
    } catch (e) {
      showToast('Failed to contact MusicBrainz.', 'error');
    } finally {
      setResolving(false);
    }
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Header */}
      <Box display="flex" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={2}>
        <Box display="flex" alignItems="center" gap={1.5}>
          <ArtistsIcon sx={{ color: 'primary.main', fontSize: 36 }} />
          <Box>
            <Typography variant="h5" sx={{ fontWeight: 800 }}>Artist Aliases</Typography>
            <Typography variant="body2" color="text.secondary">
              Map alternate artist names to their canonical names (e.g., "Ye" &rarr; "Kanye West") to keep your library grouped properly.
            </Typography>
          </Box>
        </Box>
        <Button
          variant="contained"
          startIcon={loading ? <CircularProgress size={16} color="inherit" /> : <SaveIcon />}
          onClick={handleSave}
          disabled={loading}
          sx={{ borderRadius: 2.5, fontWeight: 700, textTransform: 'none' }}
        >
          Save Aliases
        </Button>
      </Box>

      {toast && (
        <Alert severity={toast.type} onClose={() => setToast(null)} sx={{ borderRadius: 2 }}>
          {toast.msg}
        </Alert>
      )}

      {/* Main Card */}
      <Paper sx={{ p: 3, borderRadius: 4, border: '1px solid', borderColor: 'divider', boxShadow: 'none' }}>
        <Typography variant="h6" fontWeight={700} mb={2}>Add New Alias</Typography>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} mb={3}>
          <TextField
            label="Original Name (e.g. Ye)"
            value={newOriginal}
            onChange={e => setNewOriginal(e.target.value)}
            fullWidth
            size="small"
          />
          <Button
            variant="outlined"
            onClick={handleResolveMusicBrainz}
            disabled={resolving || !newOriginal.trim()}
            startIcon={resolving ? <CircularProgress size={16} /> : <SearchIcon />}
            sx={{ minWidth: 160, borderRadius: 2, textTransform: 'none' }}
          >
            Find Canonical
          </Button>
          <TextField
            label="Mapped Name (e.g. Kanye West)"
            value={newMapped}
            onChange={e => setNewMapped(e.target.value)}
            fullWidth
            size="small"
          />
          <Button
            variant="contained"
            color="secondary"
            onClick={handleAdd}
            startIcon={<AddIcon />}
            sx={{ borderRadius: 2, textTransform: 'none', px: 3 }}
          >
            Add
          </Button>
        </Stack>

        <Divider sx={{ mb: 2 }} />

        <Typography variant="subtitle2" fontWeight={700} mb={1}>Current Aliases ({aliases.length})</Typography>
        {aliases.length === 0 ? (
          <Typography variant="body2" color="text.secondary" py={2} textAlign="center">
            No aliases configured yet.
          </Typography>
        ) : (
          <List>
            {aliases.map((a, idx) => (
              <ListItem
                key={idx}
                divider={idx < aliases.length - 1}
                secondaryAction={
                  <IconButton edge="end" color="error" onClick={() => handleRemove(idx)}>
                    <DeleteIcon />
                  </IconButton>
                }
              >
                <ListItemText
                  primary={
                    <Stack direction="row" alignItems="center" spacing={2}>
                      <Typography variant="body1" fontWeight={700} sx={{ flex: 1 }}>{a.original}</Typography>
                      <Typography variant="body2" color="text.secondary" sx={{ flexShrink: 0 }}>&rarr;</Typography>
                      <Typography variant="body1" fontWeight={700} color="primary.main" sx={{ flex: 1 }}>{a.mapped}</Typography>
                    </Stack>
                  }
                />
              </ListItem>
            ))}
          </List>
        )}
      </Paper>
    </Box>
  );
};

export default ArtistAliases;
