import React, { useEffect, useState } from 'react';
import { Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Stack, Typography } from '@mui/material';
import { apiService } from '../api';
import { useAuth } from '../context/AuthContext';

const MusicRequests: React.FC = () => {
  const { user } = useAuth();
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<number | null>(null);

  const refresh = async () => {
    try {
      const result = await apiService.getMusicRequests();
      setItems(result.requests);
      setError('');
    } catch (failure: any) {
      setError(failure.response?.data?.detail || 'Could not load requests.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 15000);
    return () => window.clearInterval(timer);
  }, []);

  const decide = async (id: number, decision: 'approve' | 'decline') => {
    setBusy(id);
    try {
      await apiService.decideMusicRequest(id, decision);
      await refresh();
    } catch (failure: any) {
      setError(failure.response?.data?.detail || 'Could not update request.');
    } finally {
      setBusy(null);
    }
  };

  return (
    <Box>
      <Typography variant="h4" gutterBottom>Music requests</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>
        Request a track or album from Search Music. Pending requests need administrator approval; queued requests are not yet in the library.
      </Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {loading ? <CircularProgress /> : items.length === 0 ? <Alert severity="info">No requests yet.</Alert> : (
        <Stack spacing={1.5}>
          {items.map((item) => (
            <Card key={item.id} variant="outlined"><CardContent>
              <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" gap={1}>
                <Box>
                  <Typography variant="h6">{item.artist} — {item.kind === 'album' ? item.album : item.title}</Typography>
                  <Typography variant="body2" color="text.secondary">
                    {item.kind === 'track' ? `Track${item.album ? ` · ${item.album}` : ''}` : 'Album'} · {item.created_at}
                    {user?.isAdmin ? ` · User ${item.user_id}` : ''}
                  </Typography>
                  {item.error && <Typography color="error" variant="body2">{item.error}</Typography>}
                </Box>
                <Stack direction="row" alignItems="center" gap={1}>
                  <Chip size="small" label={item.status} color={item.status === 'completed' ? 'success' : item.status === 'failed' || item.status === 'partial' ? 'warning' : 'default'} />
                  {user?.isAdmin && item.status === 'pending' && <>
                    <Button disabled={busy === item.id} onClick={() => void decide(item.id, 'approve')}>Approve</Button>
                    <Button disabled={busy === item.id} color="error" onClick={() => void decide(item.id, 'decline')}>Decline</Button>
                  </>}
                </Stack>
              </Stack>
            </CardContent></Card>
          ))}
        </Stack>
      )}
    </Box>
  );
};

export default MusicRequests;
