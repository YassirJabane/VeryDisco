import { createTheme, responsiveFontSizes } from '@mui/material/styles';

export const getTheme = (mode: 'light' | 'dark') => {
  const theme = createTheme({
    palette: {
      mode,
      primary: {
        main: mode === 'dark' ? '#9b6cff' : '#6d3df5',
      },
      secondary: {
        main: mode === 'dark' ? '#63e6e2' : '#087f8c',
      },
      background: {
        default: mode === 'dark' ? '#0b0a10' : '#f6f4fb',
        paper: mode === 'dark' ? '#15131d' : '#ffffff',
      },
      text: {
        primary: mode === 'dark' ? '#f7f4ff' : '#181426',
        secondary: mode === 'dark' ? '#aaa3ba' : '#6e687d',
      },
      divider: mode === 'dark' ? 'rgba(255,255,255,0.09)' : 'rgba(24,20,38,0.10)',
    },
    typography: {
      fontFamily: '"Outfit", "Roboto", "Helvetica", "Arial", sans-serif',
      h1: { fontWeight: 850, letterSpacing: '-0.045em' },
      h2: { fontWeight: 800, letterSpacing: '-0.035em' },
      h3: { fontWeight: 800, letterSpacing: '-0.03em' },
      h4: { fontWeight: 750, letterSpacing: '-0.025em' },
      h5: { fontWeight: 750, letterSpacing: '-0.02em' },
      h6: { fontWeight: 700, letterSpacing: '-0.01em' },
      button: { textTransform: 'none', fontWeight: 700, letterSpacing: '-0.01em' },
      overline: { fontWeight: 750, letterSpacing: '0.12em' },
    },
    shape: { borderRadius: 16 },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: {
            background: mode === 'dark'
              ? 'radial-gradient(circle at 80% 0%, rgba(112,76,255,0.10), transparent 28rem), #0b0a10'
              : 'radial-gradient(circle at 80% 0%, rgba(125,92,255,0.08), transparent 28rem), #f6f4fb',
          },
          '::selection': {
            backgroundColor: mode === 'dark' ? 'rgba(155,108,255,0.35)' : 'rgba(109,61,245,0.18)',
          },
        },
      },
      MuiCard: {
        styleOverrides: {
          root: {
            borderRadius: 20,
            boxShadow: mode === 'dark'
              ? '0 18px 60px rgba(0, 0, 0, 0.24)'
              : '0 18px 48px rgba(42, 28, 92, 0.08)',
            border: mode === 'dark' ? '1px solid rgba(255, 255, 255, 0.08)' : '1px solid rgba(31, 20, 68, 0.08)',
            backgroundImage: 'none',
          },
        },
      },
      MuiButton: {
        styleOverrides: {
          root: {
            borderRadius: 12,
            padding: '9px 16px',
            transition: 'transform 160ms ease, box-shadow 160ms ease, background-color 160ms ease',
            '&:hover': { transform: 'translateY(-1px)' },
          },
        },
      },
      MuiInputBase: {
        styleOverrides: {
          root: {
            fontSize: '16px', // Prevent iOS Safari from zooming in on focus
          },
        },
      },
      MuiTextField: {
        styleOverrides: {
          root: {
            '& .MuiOutlinedInput-root': {
              borderRadius: 12,
              transition: 'box-shadow 160ms ease, border-color 160ms ease',
              '&.Mui-focused': {
                boxShadow: mode === 'dark' ? '0 0 0 4px rgba(155,108,255,0.14)' : '0 0 0 4px rgba(109,61,245,0.10)',
              },
            },
          },
        },
      },
      MuiChip: {
        styleOverrides: {
          root: { borderRadius: 8, fontWeight: 700 },
        },
      },
      MuiPaper: {
        styleOverrides: {
          root: { backgroundImage: 'none' },
        },
      },
    },
  });
  return responsiveFontSizes(theme);
};
export default getTheme;
