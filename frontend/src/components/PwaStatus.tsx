import React from 'react';
import { Alert, Box, Button, IconButton, Snackbar } from '@mui/material';
import CloseRounded from '@mui/icons-material/CloseRounded';
import { useRegisterSW } from 'virtual:pwa-register/react';

/** Only the static app shell is available offline; server data is never cached. */
export default function PwaStatus() {
  const [offline, setOffline] = React.useState(!navigator.onLine);
  const [dismissedOffline, setDismissedOffline] = React.useState(false);
  const [error, setError] = React.useState(false);
  const registration = React.useRef<ServiceWorkerRegistration>();
  const lastCheck = React.useRef(0);
  const activating = React.useRef(false);
  const reloadAfterActivation = React.useCallback(() => {
    if (!activating.current) return;
    activating.current = false;
    window.location.reload();
  }, []);
  const { needRefresh: [needRefresh, setNeedRefresh], updateServiceWorker } = useRegisterSW({
    onRegisteredSW(_url, next) { registration.current = next; },
    onNeedReload: reloadAfterActivation,
  });
  React.useEffect(() => {
    const connected = () => {
      setOffline(!navigator.onLine);
      setDismissedOffline(false);
      if (navigator.onLine && Date.now() - lastCheck.current > 60000) {
        lastCheck.current = Date.now();
        void registration.current?.update().catch(() => { /* Server may be temporarily unreachable. */ });
      }
    };
    window.addEventListener('online', connected);
    window.addEventListener('offline', connected);
    window.addEventListener('focus', connected);
    // Also covers an update arriving on the same page as the very first install.
    navigator.serviceWorker?.addEventListener('controllerchange', reloadAfterActivation);
    return () => {
      window.removeEventListener('online', connected);
      window.removeEventListener('offline', connected);
      window.removeEventListener('focus', connected);
      navigator.serviceWorker?.removeEventListener('controllerchange', reloadAfterActivation);
    };
  }, [reloadAfterActivation]);
  const reload = async () => {
    setError(false);
    activating.current = true;
    try { await updateServiceWorker(true); }
    catch { activating.current = false; setError(true); }
  };
  const dismiss = () => { setNeedRefresh(false); setDismissedOffline(true); setError(false); };
  return <Snackbar open={needRefresh || error || (offline && !dismissedOffline)} anchorOrigin={{ vertical: 'top', horizontal: 'center' }}
    sx={{ top: 'max(16px, env(safe-area-inset-top)) !important', maxWidth: 600 }}>
    <Alert severity={error || offline ? 'warning' : 'info'} variant="filled"
      onClose={dismiss}
      action={needRefresh && !offline ? <Box sx={{ display: 'flex', alignItems: 'center', gap: .5 }}><Button color="inherit" size="small" onClick={() => void reload()}>Update now</Button><IconButton color="inherit" size="small" aria-label="Dismiss update" onClick={dismiss}><CloseRounded fontSize="small" /></IconButton></Box> : undefined}>
      {offline ? 'You are offline. The app layout is available, but music, sign-in and library actions need a connection to your server.'
        : error ? 'The update could not be activated. Try again when your server is available.'
          : 'A new version is ready. Save any changes before updating.'}
    </Alert>
  </Snackbar>;
}
