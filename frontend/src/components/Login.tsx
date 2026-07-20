import React, { useState } from 'react';
import {
  Box, Card, CardContent, TextField, Button, Typography,
  CircularProgress, Alert, InputAdornment, IconButton,
  useTheme,
} from '@mui/material';
import {
  Visibility, VisibilityOff, MusicNote as MusicIcon,
  Login as LoginIcon,
} from '@mui/icons-material';
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
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: isDark
          ? 'linear-gradient(135deg, #0d0c12 0%, #1a1830 50%, #0d0c12 100%)'
          : 'linear-gradient(135deg, #f0eeff 0%, #e8f4fd 50%, #f0eeff 100%)',
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
            ? 'rgba(28, 27, 34, 0.85)'
            : 'rgba(255, 255, 255, 0.9)',
          backdropFilter: 'blur(24px)',
          border: '1px solid',
          borderColor: isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)',
          boxShadow: isDark
            ? '0 32px 80px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.04)'
            : '0 32px 80px rgba(0,0,0,0.12)',
        }}
      >
        <CardContent sx={{ p: { xs: 3, sm: 5 } }}>
          {/* Logo */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 4 }}>
            <Box
              sx={{
                width: 48, height: 48, borderRadius: 3,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: 'linear-gradient(135deg, #6e46ff, #b388ff)',
                boxShadow: '0 8px 24px rgba(110,70,255,0.4)',
              }}
            >
              <MusicIcon sx={{ color: '#fff', fontSize: 26 }} />
            </Box>
            <Box>
              <Typography variant="h5" sx={{ fontWeight: 800, lineHeight: 1.1 }}>
                VeryDisco
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600, letterSpacing: 1 }}>
                SIGN IN WITH NAVIDROME
              </Typography>
            </Box>
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
                background: 'linear-gradient(135deg, #6e46ff, #9c70ff)',
                boxShadow: '0 8px 24px rgba(110,70,255,0.35)',
                '&:hover': {
                  background: 'linear-gradient(135deg, #5c38e8, #8560ee)',
                  boxShadow: '0 12px 32px rgba(110,70,255,0.45)',
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
