import React, { useState } from 'react';
import {
  Box, Card, CardContent, TextField, Button, Typography,
  CircularProgress, Alert, InputAdornment, IconButton,
  useTheme, Stepper, Step, StepLabel, Checkbox, FormControlLabel,
} from '@mui/material';
import Visibility from '@mui/icons-material/Visibility';
import VisibilityOff from '@mui/icons-material/VisibilityOff';
import BrandLogo from './BrandLogo';
import MusicIcon from '@mui/icons-material/MusicNote';
import CloudIcon from '@mui/icons-material/Cloud';
import NextIcon from '@mui/icons-material/ChevronRight';
import PrevIcon from '@mui/icons-material/ChevronLeft';
import DoneIcon from '@mui/icons-material/CheckCircle';
import { apiService } from '../api';
import { useAuth } from '../context/AuthContext';
import { prepareLibraryOnboarding } from '../libraryOnboarding';

const Setup: React.FC = () => {
  const theme = useTheme();
  const { refreshUser } = useAuth();
  const isDark = theme.palette.mode === 'dark';

  const [activeStep, setActiveStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Form fields
  const [navidromeUrl, setNavidromeUrl] = useState('');
  const [navidromeUsername, setNavidromeUsername] = useState('');
  const [navidromePassword, setNavidromePassword] = useState('');
  const [showNavidromePassword, setShowNavidromePassword] = useState(false);

  const [slskdUrl, setSlskdUrl] = useState('http://slskd:5030');
  const [slskdApiKey, setSlskdApiKey] = useState('');
  const [reviewLibrary, setReviewLibrary] = useState(true);

  const handleNext = () => {
    setError(null);
    if (activeStep === 0) {
      if (!navidromeUrl || !navidromeUsername || !navidromePassword) {
        setError('Please fill in all Navidrome admin configuration details.');
        return;
      }
    }
    setActiveStep(prev => prev + 1);
  };

  const handleBack = () => {
    setError(null);
    setActiveStep(prev => prev - 1);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (activeStep < steps.length - 1) {
      handleNext();
      return;
    }
    setError(null);
    setLoading(true);

    try {
      const result = await apiService.setup({
        navidrome_url: navidromeUrl.trim(),
        navidrome_username: navidromeUsername.trim(),
        navidrome_password: navidromePassword,
        slskd_url: slskdUrl.trim(),
        slskd_api_key: slskdApiKey.trim(),
      });

      if (result.status === 'ok') {
        await refreshUser();
        window.location.href = await prepareLibraryOnboarding(reviewLibrary);
      }
    } catch (err: any) {
      setError(
        err?.response?.data?.detail ||
        'Bootstrap failed. Please verify Navidrome credentials and connection.'
      );
      setLoading(false);
    }
  };

  const steps = ['Navidrome Admin', 'Soulseek Setup'];

  return (
    <Box
      sx={{
        minHeight: '100vh',
        '@supports (min-height: 100dvh)': { minHeight: '100dvh' },
        pl: 'env(safe-area-inset-left)',
        pr: 'env(safe-area-inset-right)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: isDark
          ? 'linear-gradient(135deg, #0d1017 0%, #1b1932 50%, #0d1017 100%)'
          : 'linear-gradient(135deg, #f6f7fb 0%, #efebfa 50%, #f0faf8 100%)',
        position: 'relative',
        overflow: 'hidden',
        pt: 'max(32px, env(safe-area-inset-top))',
        pb: 'max(32px, env(safe-area-inset-bottom))',
      }}
    >
      <Box sx={{
        position: 'absolute', top: '10%', left: '5%',
        width: 450, height: 450, borderRadius: '50%',
        background: isDark
          ? 'radial-gradient(circle, rgba(110,70,255,0.18) 0%, transparent 70%)'
          : 'radial-gradient(circle, rgba(98,0,234,0.08) 0%, transparent 70%)',
        pointerEvents: 'none',
      }} />

      <Card
        sx={{
          width: '100%',
          maxWidth: 520,
          mx: 2,
          borderRadius: 6,
          background: isDark
            ? 'rgba(23, 27, 37, 0.95)'
            : 'rgba(255, 255, 255, 0.9)',
          backdropFilter: 'blur(24px)',
          border: '1px solid',
          borderColor: isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)',
          boxShadow: isDark
            ? '0 32px 80px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.04)'
            : '0 32px 80px rgba(0,0,0,0.12)',
        }}
      >
        <CardContent sx={{ p: { xs: 2.5, sm: 5 } }}>
          <Box sx={{ mb: 4 }}>
            <BrandLogo width={320} sx={{ mb: 2 }} />
            <Typography component="h1" variant="caption" color="text.secondary" sx={{ fontWeight: 600, letterSpacing: 1 }}>
              INITIAL SETUP & ONBOARDING
            </Typography>
          </Box>

          <Stepper activeStep={activeStep} alternativeLabel sx={{ mb: 4 }}>
            {steps.map((label) => (
              <Step key={label}>
                <StepLabel>{label}</StepLabel>
              </Step>
            ))}
          </Stepper>

          {error && (
            <Alert severity="error" sx={{ mb: 3, borderRadius: 2 }} onClose={() => setError(null)}>
              {error}
            </Alert>
          )}

          <Box component="form" onSubmit={handleSubmit}>
            {activeStep === 0 && (
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.5 }}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                  Connect VeryDisco to your Navidrome music server. We need your server URL and your Navidrome admin login.
                </Typography>

                <TextField
                  label="Navidrome Server URL"
                  placeholder="e.g. http://192.168.1.50:4533"
                  value={navidromeUrl}
                  onChange={e => setNavidromeUrl(e.target.value)}
                  fullWidth
                  size="medium"
                  helperText="Use container network name or direct IP (do not include trailing /rest)"
                  sx={{ '& .MuiOutlinedInput-root': { borderRadius: 2.5 } }}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <MusicIcon color="action" />
                      </InputAdornment>
                    ),
                  }}
                />

                <TextField
                  label="Navidrome Admin Username"
                  value={navidromeUsername}
                  onChange={e => setNavidromeUsername(e.target.value)}
                  fullWidth
                  size="medium"
                  sx={{ '& .MuiOutlinedInput-root': { borderRadius: 2.5 } }}
                />

                <TextField
                  label="Navidrome Admin Password"
                  type={showNavidromePassword ? 'text' : 'password'}
                  value={navidromePassword}
                  onChange={e => setNavidromePassword(e.target.value)}
                  fullWidth
                  size="medium"
                  sx={{ '& .MuiOutlinedInput-root': { borderRadius: 2.5 } }}
                  InputProps={{
                    endAdornment: (
                      <InputAdornment position="end">
                        <IconButton
                          onClick={() => setShowNavidromePassword(v => !v)}
                          edge="end"
                          tabIndex={-1}
                        >
                          {showNavidromePassword ? <VisibilityOff /> : <Visibility />}
                        </IconButton>
                      </InputAdornment>
                    ),
                  }}
                />
              </Box>
            )}

            {activeStep === 1 && (
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.5 }}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                  Set up Soulseek (slskd) parameters to allow downloading missing songs.
                </Typography>

                <TextField
                  label="slskd Base URL"
                  placeholder="e.g. http://slskd:5030"
                  value={slskdUrl}
                  onChange={e => setSlskdUrl(e.target.value)}
                  fullWidth
                  size="medium"
                  sx={{ '& .MuiOutlinedInput-root': { borderRadius: 2.5 } }}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <CloudIcon color="action" />
                      </InputAdornment>
                    ),
                  }}
                />

                <TextField
                  label="slskd API Key"
                  type="password"
                  placeholder="Optional"
                  value={slskdApiKey}
                  onChange={e => setSlskdApiKey(e.target.value)}
                  fullWidth
                  size="medium"
                  helperText="Leave empty if authentication is not enabled on your slskd instance"
                  sx={{ '& .MuiOutlinedInput-root': { borderRadius: 2.5 } }}
                />
              </Box>
            )}

            {activeStep === steps.length - 1 && <Box sx={{ mt: 3, p: 2, borderRadius: 2.5, bgcolor: 'action.hover', border: '1px solid', borderColor: 'divider' }}>
              <FormControlLabel control={<Checkbox checked={reviewLibrary} onChange={event => setReviewLibrary(event.target.checked)} disabled={loading} />} label="Scan my existing library after setup" />
              <Typography variant="body2" color="text.secondary">Prepare a read-only inventory for Metadata Rebuild using your existing music. Review album matches and artist credits before applying changes, then preview file and folder names.</Typography>
            </Box>}

            <Box sx={{ display: 'flex', justifyContent: 'space-between', mt: 4 }}>
              <Button
                type="button"
                disabled={activeStep === 0 || loading}
                onClick={handleBack}
                startIcon={<PrevIcon />}
                sx={{ textTransform: 'none', fontWeight: 600 }}
              >
                Back
              </Button>
              
              {activeStep < steps.length - 1 ? (
                <Button
                  type="button"
                  variant="contained"
                  onClick={event => { event.preventDefault(); handleNext(); }}
                  endIcon={<NextIcon />}
                  sx={{
                    borderRadius: 2.5,
                    textTransform: 'none',
                    fontWeight: 700,
                    px: 3,
                  }}
                >
                  Next
                </Button>
              ) : (
                <Button
                  type="submit"
                  variant="contained"
                  disabled={loading}
                  startIcon={loading ? <CircularProgress size={18} color="inherit" /> : <DoneIcon />}
                  sx={{
                    borderRadius: 2.5,
                    textTransform: 'none',
                    fontWeight: 700,
                    px: 3.5,
                    bgcolor: 'primary.main',
                    '&:hover': {
                      bgcolor: 'primary.dark',
                    },
                  }}
                >
                  {loading ? 'Bootstrapping…' : 'Complete Setup'}
                </Button>
              )}
            </Box>
          </Box>
        </CardContent>
      </Card>
    </Box>
  );
};

export default Setup;
