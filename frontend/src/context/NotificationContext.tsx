import React, { createContext, useContext, useState, useRef, ReactNode } from 'react';
import { 
  Snackbar, Alert, Dialog, DialogTitle, DialogContent, 
  DialogContentText, DialogActions, Button, Typography, Box 
} from '@mui/material';
import AlarmIcon from '@mui/icons-material/WarningAmber';
import DangerousIcon from '@mui/icons-material/DeleteForever';
import { getErrorMessage } from '../api';

type Severity = 'success' | 'info' | 'warning' | 'error';

interface ConfirmOptions {
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  isDangerous?: boolean;
  onConfirm: () => void | Promise<void>;
}

interface NotificationContextType {
  notify: (message: unknown, severity?: Severity) => void;
  confirm: (options: ConfirmOptions) => void;
}

const NotificationContext = createContext<NotificationContextType | undefined>(undefined);

function notificationText(message: unknown): string {
  if (typeof message === 'string') return message;
  if (Array.isArray(message)) return message.map(notificationText).join('; ');
  if (message && typeof message === 'object') {
    const details = message as Record<string, unknown>;
    const value = details.msg ?? details.message ?? details.detail;
    if (value !== undefined && value !== message) return notificationText(value);
    try { return JSON.stringify(message); } catch { return 'An unexpected error occurred.'; }
  }
  return message == null ? '' : String(message);
}

export const NotificationProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  // Toast state
  const [toastOpen, setToastOpen] = useState(false);
  const [toastMessage, setToastMessage] = useState('');
  const [toastSeverity, setToastSeverity] = useState<Severity>('info');

  // Confirm dialog state
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmOptions, setConfirmOptions] = useState<ConfirmOptions | null>(null);
  const [confirmLoading, setConfirmLoading] = useState(false);
  const confirmPending = useRef(false);

  const notify = (message: unknown, severity: Severity = 'info') => {
    setToastMessage(notificationText(message));
    setToastSeverity(severity);
    setToastOpen(true);
  };

  const confirm = (options: ConfirmOptions) => {
    setConfirmOptions(options);
    setConfirmOpen(true);
  };

  const handleToastClose = (_?: React.SyntheticEvent | Event, reason?: string) => {
    if (reason === 'clickaway') return;
    setToastOpen(false);
  };

  const handleConfirmAction = async () => {
    if (!confirmOptions || confirmPending.current) return;
    confirmPending.current = true;
    setConfirmLoading(true);
    try {
      await confirmOptions.onConfirm();
      setConfirmOpen(false);
      setConfirmOptions(null);
    } catch (e) {
      console.error(e);
      notify(getErrorMessage(e, 'The operation failed. Please try again.'), 'error');
    } finally {
      confirmPending.current = false;
      setConfirmLoading(false);
    }
  };

  return (
    <NotificationContext.Provider value={{ notify, confirm }}>
      {children}

      {/* Global In-App Toast Notification */}
      <Snackbar
        open={toastOpen}
        autoHideDuration={4000}
        onClose={handleToastClose}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ top: { xs: '76px !important', sm: '80px !important' } }}
      >
        <Alert 
          onClose={handleToastClose} 
          severity={toastSeverity} 
          variant="filled"
          sx={{ 
            width: '100%', 
            fontWeight: 600,
            borderRadius: 2,
            boxShadow: '0 8px 32px rgba(0,0,0,0.4)'
          }}
        >
          {toastMessage}
        </Alert>
      </Snackbar>

      {/* Global In-App Confirmation Dialog */}
      {confirmOptions && (
        <Dialog
          open={confirmOpen}
          onClose={() => !confirmLoading && setConfirmOpen(false)}
          PaperProps={{
            sx: {
              borderRadius: 3,
              p: 1,
              maxWidth: 480,
              border: confirmOptions.isDangerous ? '1px solid rgba(244, 67, 54, 0.5)' : '1px solid rgba(255, 255, 255, 0.1)',
              bgcolor: 'background.paper',
              boxShadow: '0 16px 48px rgba(0, 0, 0, 0.24)'
            }
          }}
        >
          <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1.5, pb: 1 }}>
            {confirmOptions.isDangerous ? (
              <Box 
                sx={{ 
                  bgcolor: 'error.main', 
                  color: 'error.contrastText', 
                  p: 1, 
                  borderRadius: '50%',
                  display: 'flex'
                }}
              >
                <DangerousIcon />
              </Box>
            ) : (
              <AlarmIcon color="warning" />
            )}
            <Typography component="span" variant="h6" sx={{ fontWeight: 800 }}>
              {confirmOptions.title}
            </Typography>
          </DialogTitle>
          <DialogContent>
            <DialogContentText sx={{ color: 'text.secondary', fontSize: '0.95rem' }}>
              {confirmOptions.message}
            </DialogContentText>
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2 }}>
            <Button 
              onClick={() => setConfirmOpen(false)} 
              disabled={confirmLoading}
              sx={{ color: 'text.secondary', fontWeight: 600 }}
            >
              {confirmOptions.cancelText || 'Cancel'}
            </Button>
            <Button
              onClick={handleConfirmAction}
              disabled={confirmLoading}
              variant="contained"
              color={confirmOptions.isDangerous ? 'error' : 'primary'}
              sx={{ 
                fontWeight: 700,
                borderRadius: 2,
                px: 3
              }}
            >
              {confirmLoading ? 'Processing...' : (confirmOptions.confirmText || 'Confirm')}
            </Button>
          </DialogActions>
        </Dialog>
      )}
    </NotificationContext.Provider>
  );
};

export const useNotification = (): NotificationContextType => {
  const context = useContext(NotificationContext);
  if (!context) {
    throw new Error('useNotification must be used within a NotificationProvider');
  }
  return context;
};
