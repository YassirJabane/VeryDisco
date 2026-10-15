import React from 'react';
import { Box, Button, Stack, Typography } from '@mui/material';
import RefreshRounded from '@mui/icons-material/RefreshRounded';
import SentimentDissatisfiedRounded from '@mui/icons-material/SentimentDissatisfiedRounded';

export default class PageErrorBoundary extends React.Component<React.PropsWithChildren, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { console.error('VeryDisco page failed to render', error); }
  render() {
    if (!this.state.failed) return this.props.children;
    return <Box role="alert" sx={{ p: { xs: 3, sm: 6 }, my: 4, textAlign: 'center', borderRadius: 4, border: '1px solid', borderColor: 'divider', bgcolor: 'background.paper' }}>
      <SentimentDissatisfiedRounded sx={{ fontSize: 48, color: 'text.secondary', mb: 2 }} />
      <Typography variant="h5" gutterBottom>This page hit a snag</Typography>
      <Typography color="text.secondary" sx={{ maxWidth: 420, mx: 'auto' }}>Try again, or open another section from the navigation. Reloading may discard unsaved edits on this page.</Typography>
      <Stack direction="row" justifyContent="center" gap={1} mt={3}><Button variant="contained" onClick={() => this.setState({ failed: false })}>Try again</Button><Button startIcon={<RefreshRounded />} onClick={() => window.location.reload()}>Reload app</Button></Stack>
    </Box>;
  }
}
