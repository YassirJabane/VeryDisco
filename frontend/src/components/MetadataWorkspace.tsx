import React from 'react';
import { Alert, Box, LinearProgress, Tab, Tabs, Typography } from '@mui/material';
import MetadataRebuild from './MetadataRebuild';

const NamingConvention = React.lazy(() => import('./NamingConvention'));
const sectionFromUrl = () => window.location.pathname === '/naming' || new URLSearchParams(window.location.search).get('section') === 'naming' ? 'naming' : 'metadata';

/** One review workflow: authoritative release/artist credits, then file organization. */
export default function MetadataWorkspace() {
  const [section, setSection] = React.useState(sectionFromUrl);
  const onboarding = new URLSearchParams(window.location.search).get('onboarding') === '1';
  const [scanError] = React.useState(() => sessionStorage.getItem('vd-onboarding-scan-error'));
  React.useEffect(() => {
    if (onboarding) sessionStorage.removeItem('vd-onboarding-scan-error');
    const onPopState = () => setSection(sectionFromUrl());
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, [onboarding]);
  const changeSection = (_event: React.SyntheticEvent, value: string) => {
    setSection(value);
    const url = new URL(window.location.href);
    url.pathname = '/metadata-rebuild';
    if (value === 'naming') url.searchParams.set('section', 'naming');
    else url.searchParams.delete('section');
    window.history.pushState(null, '', url.pathname + url.search);
  };
  return <Box>
    {onboarding && <Alert severity={scanError ? 'warning' : 'info'} sx={{ mb: 2 }}>
      {scanError || 'Welcome to your library review. The initial inventory only reads existing music. Review release matches and artist credits before applying an album, then organize file and folder names.'}
    </Alert>}
    <Tabs value={section} onChange={changeSection} aria-label="Library metadata workflow" variant="scrollable" scrollButtons="auto" allowScrollButtonsMobile sx={{ mb: 3, borderBottom: '1px solid', borderColor: 'divider' }}>
      <Tab id="metadata-tab" aria-controls="metadata-panel" value="metadata" label="1. Release metadata & artist credits" />
      <Tab id="naming-tab" aria-controls="naming-panel" value="naming" label="2. File & folder names" />
    </Tabs>
    <Box role="tabpanel" id="metadata-panel" aria-labelledby="metadata-tab" hidden={section !== 'metadata'}>
      <MetadataRebuild />
    </Box>
    {section === 'naming' && <Box role="tabpanel" id="naming-panel" aria-labelledby="naming-tab" sx={{ minWidth: 0 }}>
      <Alert severity="info" sx={{ mb: 3 }}>Apply reviewed release metadata and artist credits first. Naming uses those tags and your configured folder/file patterns. Preview the proposed moves before applying them.</Alert>
      <React.Suspense fallback={<Box role="status"><LinearProgress /><Typography sx={{ mt: 1 }}>Opening file organization…</Typography></Box>}><NamingConvention /></React.Suspense>
    </Box>}
  </Box>;
}
