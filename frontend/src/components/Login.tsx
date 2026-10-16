import React, { useState } from 'react';
import {
  Box, Card, CardContent, TextField, Button, Typography,
  CircularProgress, Alert, InputAdornment, IconButton,
  useTheme,
} from '@mui/material';
import Visibility from '@mui/icons-material/Visibility';
import VisibilityOff from '@mui/icons-material/VisibilityOff';
import BrandLogo from './BrandLogo';
import LoginIcon from '@mui/icons-material/Login';
import { useAuth } from '../context/AuthContext';

const Login: React.FC = () => {
  const theme = useTheme();
  const { login } = useAuth();

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password.trim()) return;
    setLoading(true);
    setError(null);
    try {
      await login(username.trim(), password);
      window.location.href = '/dashboard';
    } catch (err: any) {
      setError(
        err?.response?.data?.detail ||
        'Invalid credentials or Navidrome is unreachable.'
      );
    } finally {
      setLoading(false);
    }
  };

  const isDark = theme.palette.mode === 'dark';

  return (
    <Box
      sx={{
        minHeight: '100vh',
        '@supports (min-height: 100dvh)': { minHeight: '100dvh' },
        pt: 'max(24px, env(safe-area-inset-top))',
        pb: 'max(24px, env(safe-area-inset-bottom))',
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
      }}
    >
      {/* Ambient glow blobs */}
      <Box sx={{
        position: 'absolute', top: '15%', left: '10%',
        width: 400, height: 400, borderRadius: '50%',
        background: isDark
          ? 'radial-gradient(circle, rgba(110,70,255,0.15) 0%, transparent 70%)'
          : 'radial-gradient(circle, rgba(98,0,234,0.08) 0%, transparent 70%)',
        pointerEvents: 'none',
      }} />
      <Box sx={{
        position: 'absolute', bottom: '10%', right: '8%',
        width: 350, height: 350, borderRadius: '50%',
        background: isDark
          ? 'radial-gradient(circle, rgba(179,136,255,0.12) 0%, transparent 70%)'
          : 'radial-gradient(circle, rgba(179,136,255,0.1) 0%, transparent 70%)',
        pointerEvents: 'none',
      }} />

      <Card
        sx={{
          width: '100%',
          maxWidth: 420,
          mx: 2,
          borderRadius: 5,
          background: isDark
            ? 'rgba(23, 27, 37, 0.95)'
            : 'rgba(255, 255, 255, 0.96)',
          backdropFilter: 'blur(24px)',
          border: '1px solid',
          borderColor: 'divider',
          boxShadow: isDark
            ? '0 32px 80px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.04)'
            : '0 32px 80px rgba(0,0,0,0.12)',
        }}
      >
        <CardContent sx={{ p: { xs: 3, sm: 5 } }}>
          {/* Logo */}
          <Box sx={{ mb: 4 }}>
            <BrandLogo width="100%" sx={{ mb: 2 }} />
            <Typography component="h1" variant="caption" color="text.secondary" sx={{ fontWeight: 600, letterSpacing: 1 }}>
              SIGN IN WITH NAVIDROME
            </Typography>
          </Box>

          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            Use your Navidrome username and password to access your personal music dashboard.
          </Typography>

          {error && (
            <Alert severity="error" sx={{ mb: 2.5, borderRadius: 2 }} onClose={() => setError(null)}>
              {error}
            </Alert>
          )}

          <Box component="form" onSubmit={handleSubmit} sx={{ display: 'flex', flexDirection: 'column', gap: 2.5 }}>
            <TextField
              id="login-username"
              label="Navidrome Username"
              value={username}
              onChange={e => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              disabled={loading}
              fullWidth
              size="medium"
              sx={{
                '& .MuiOutlinedInput-root': {
                  borderRadius: 2.5,
                },
              }}
            />

            <TextField
              id="login-password"
              label="Password"
              type={showPassword ? 'text' : 'password'}
              value={password}
              onChange={e => setPassword(e.target.value)}
              autoComplete="current-password"
              disabled={loading}
              fullWidth
              size="medium"
              sx={{
                '& .MuiOutlinedInput-root': { borderRadius: 2.5 },
              }}
              InputProps={{
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton
                      aria-label="toggle password visibility"
                      onClick={() => setShowPassword(v => !v)}
                      edge="end"
                      tabIndex={-1}
                    >
                      {showPassword ? <VisibilityOff /> : <Visibility />}
                    </IconButton>
                  </InputAdornment>
                ),
              }}
            />

            <Button
              id="login-submit"
              type="submit"
              variant="contained"
              size="large"
              disabled={loading || !username.trim() || !password.trim()}
              startIcon={loading ? <CircularProgress size={18} color="inherit" /> : <LoginIcon />}
              sx={{
                mt: 1,
                py: 1.5,
                borderRadius: 2.5,
                fontWeight: 700,
                fontSize: '1rem',
                textTransform: 'none',
                bgcolor: 'primary.main',
                boxShadow: 'none',
                '&:hover': {
                  bgcolor: 'primary.dark',
                  boxShadow: 'none',
                  transform: 'translateY(-1px)',
                },
                '&:active': { transform: 'translateY(0)' },
                transition: 'all 0.2s ease',
              }}
            >
              {loading ? 'Signing in…' : 'Sign in'}
            </Button>
          </Box>

          <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 3, textAlign: 'center' }}>
            Your session is secured with an httpOnly cookie. Credentials are never stored.
          </Typography>
        </CardContent>
      </Card>
    </Box>
  );
};

export default Login;
