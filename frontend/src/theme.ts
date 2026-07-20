import { createTheme, responsiveFontSizes } from '@mui/material/styles';

export const getTheme = (mode: 'light' | 'dark') => {
  const theme = createTheme({
    palette: {
      mode,
      primary: {
        main: mode === 'dark' ? '#b388ff' : '#6200ea', // Vibrant neon violet vs deep violet
      },
      secondary: {
        main: mode === 'dark' ? '#80deea' : '#00b0ff', // Soft cyan vs vivid sky blue
      },
      background: {
        default: mode === 'dark' ? '#0b0a0f' : '#f8f9fa',
        paper: mode === 'dark' ? '#121118' : '#ffffff',
      },
    },
    typography: {
      fontFamily: '"Outfit", "Roboto", "Helvetica", "Arial", sans-serif',
      h1: { fontWeight: 800 },
      h2: { fontWeight: 700 },
      h3: { fontWeight: 700 },
      h4: { fontWeight: 600 },
      h5: { fontWeight: 600 },
      h6: { fontWeight: 500 },
      button: { textTransform: 'none', fontWeight: 600 },
    },
    components: {
      MuiCard: {
        styleOverrides: {
          root: {
            borderRadius: 16,
            boxShadow: mode === 'dark' 
              ? '0 8px 32px 0 rgba(0, 0, 0, 0.37)' 
              : '0 8px 32px 0 rgba(31, 38, 135, 0.05)',
            border: mode === 'dark' ? '1px solid rgba(255, 255, 255, 0.07)' : '1px solid rgba(0, 0, 0, 0.03)',
            backgroundImage: 'none',
          },
        },
      },
      MuiButton: {
        styleOverrides: {
          root: {
            borderRadius: 10,
            padding: '8px 16px',
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
              borderRadius: 10,
            },
          },
        },
      },
    },
  });
  return responsiveFontSizes(theme);
};
export default getTheme;
