import { createTheme, responsiveFontSizes } from '@mui/material/styles';

export const getTheme = (mode: 'light' | 'dark') => {
  const theme = createTheme({
    palette: {
      mode,
      primary: {
        main: mode === 'dark' ? '#b397ff' : '#6840dc',
        light: mode === 'dark' ? '#cbb8ff' : '#8665e6',
        dark: mode === 'dark' ? '#9673ee' : '#5130b4',
        contrastText: mode === 'dark' ? '#171024' : '#ffffff',
      },
      secondary: {
        main: mode === 'dark' ? '#70ded0' : '#067d71',
        contrastText: mode === 'dark' ? '#102b27' : '#ffffff',
      },
      background: {
        default: mode === 'dark' ? '#0d1017' : '#f6f7fb',
        paper: mode === 'dark' ? '#171b25' : '#ffffff',
      },
      text: {
        primary: mode === 'dark' ? '#f0f0f7' : '#202133',
        secondary: mode === 'dark' ? '#b0b6c8' : '#586176',
        disabled: mode === 'dark' ? '#81899d' : '#70798c',
      },
      divider: mode === 'dark' ? 'rgba(176,182,200,0.17)' : 'rgba(58,67,89,0.16)',
      action: {
        active: mode === 'dark' ? '#b0b6c8' : '#586176',
        hover: mode === 'dark' ? 'rgba(179,151,255,0.08)' : 'rgba(104,64,220,0.06)',
        selected: mode === 'dark' ? 'rgba(179,151,255,0.16)' : 'rgba(104,64,220,0.10)',
        focus: mode === 'dark' ? 'rgba(179,151,255,0.22)' : 'rgba(104,64,220,0.16)',
        disabled: mode === 'dark' ? '#81899d' : '#70798c',
        disabledBackground: mode === 'dark' ? '#252b38' : '#e9ecf3',
      },
      info: { main: mode === 'dark' ? '#8fbeff' : '#2866b0', contrastText: mode === 'dark' ? '#101e32' : '#ffffff' },
      success: { main: mode === 'dark' ? '#61cfa6' : '#137b60', contrastText: mode === 'dark' ? '#10271e' : '#ffffff' },
      warning: { main: mode === 'dark' ? '#e6b45e' : '#9b6100', contrastText: mode === 'dark' ? '#261c0b' : '#ffffff' },
      error: { main: mode === 'dark' ? '#f1919c' : '#c13f53', contrastText: mode === 'dark' ? '#33131a' : '#ffffff' },
    },
    typography: {
      fontFamily: '"Outfit", "Roboto", "Helvetica", "Arial", sans-serif',
      body1: { lineHeight: 1.65 },
      body2: { lineHeight: 1.6 },
      h1: { fontWeight: 850, letterSpacing: '-0.045em' },
      h2: { fontWeight: 800, letterSpacing: '-0.035em' },
      h3: { fontWeight: 800, letterSpacing: '-0.03em' },
      h4: { fontWeight: 750, letterSpacing: '-0.025em' },
      h5: { fontWeight: 750, letterSpacing: '-0.02em' },
      h6: { fontWeight: 700, letterSpacing: '-0.01em' },
      button: { textTransform: 'none', fontWeight: 700, letterSpacing: '-0.01em' },
      overline: { fontWeight: 750, letterSpacing: '0.12em' },
    },
    shape: { borderRadius: 8 },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: {
            background: mode === 'dark'
              ? 'radial-gradient(ellipse at 80% 0%, rgba(179,151,255,0.035), transparent 45rem), #0d1017'
              : 'radial-gradient(ellipse at 80% 0%, rgba(104,64,220,0.025), transparent 45rem), #f6f7fb',
            WebkitFontSmoothing: 'antialiased',
          },
          '::selection': {
            backgroundColor: mode === 'dark' ? 'rgba(155,108,255,0.35)' : 'rgba(109,61,245,0.18)',
          },
          '*': {
            scrollbarWidth: 'thin',
            scrollbarColor: mode === 'dark' ? 'rgba(155,108,255,.55) transparent' : 'rgba(109,61,245,.38) transparent',
          },
          '*::-webkit-scrollbar': {
            width: 8,
            height: 8,
          },
          '*::-webkit-scrollbar-track': {
            background: 'transparent',
          },
          '*::-webkit-scrollbar-thumb': {
            background: mode === 'dark' ? 'rgba(155,108,255,.45)' : 'rgba(109,61,245,.32)',
            borderRadius: 999,
            border: '2px solid transparent',
            backgroundClip: 'content-box',
          },
          '*::-webkit-scrollbar-thumb:hover': {
            background: mode === 'dark' ? 'rgba(99,230,226,.7)' : 'rgba(8,127,140,.62)',
            backgroundClip: 'content-box',
          },
          ':focus-visible': { outline: `2px solid ${mode === 'dark' ? '#b397ff' : '#6840dc'}`, outlineOffset: 3 },
          '@media (prefers-reduced-motion: reduce)': {
            '*, *::before, *::after': { animationDuration: '0.01ms !important', animationIterationCount: '1 !important', transitionDuration: '0.01ms !important', scrollBehavior: 'auto !important' },
          },
        },
      },
      MuiCard: {
        styleOverrides: {
          root: {
            borderRadius: 18,
            boxShadow: mode === 'dark'
              ? '0 4px 24px rgba(0, 0, 0, 0.12)'
              : '0 4px 24px rgba(35, 36, 65, 0.04)',
            border: mode === 'dark' ? '1px solid rgba(176,182,200,0.17)' : '1px solid rgba(58,67,89,0.14)',
            backgroundImage: 'none',
          },
        },
      },
      MuiButton: {
        defaultProps: { disableElevation: true },
        styleOverrides: {
          root: {
            borderRadius: 12,
            padding: '9px 16px',
            transition: 'transform 160ms ease, box-shadow 160ms ease, background-color 160ms ease',
            '&.Mui-focusVisible': { outline: '2px solid currentColor', outlineOffset: 3 },
            '&.Mui-disabled': { color: mode === 'dark' ? '#81899d' : '#70798c' },
          },
          outlinedPrimary: { borderColor: mode === 'dark' ? 'rgba(179,151,255,0.55)' : 'rgba(104,64,220,0.45)' },
        },
      },
      MuiInputBase: {
        styleOverrides: {
          root: {
            fontSize: '16px', // Prevent iOS Safari from zooming in on focus
          },
        },
      },
      MuiOutlinedInput: {
        styleOverrides: {
          root: {
            borderRadius: 12,
            '& .MuiOutlinedInput-notchedOutline': { borderColor: mode === 'dark' ? '#5f687e' : '#949caf' },
            '&:hover:not(.Mui-disabled) .MuiOutlinedInput-notchedOutline': { borderColor: mode === 'dark' ? '#b0b6c8' : '#586176' },
            '&.Mui-focused .MuiOutlinedInput-notchedOutline': { borderColor: mode === 'dark' ? '#b397ff' : '#6840dc' },
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
      MuiTableCell: {
        styleOverrides: {
          root: { borderColor: mode === 'dark' ? 'rgba(255,255,255,.07)' : 'rgba(24,20,38,.08)', padding: '14px 16px' },
          head: { color: mode === 'dark' ? '#b0b6c8' : '#586176', fontSize: '.72rem', fontWeight: 750, letterSpacing: '.05em', textTransform: 'uppercase', backgroundColor: mode === 'dark' ? '#1e2431' : '#f1f3f9' },
        },
      },
      MuiTableRow: { styleOverrides: { root: { '&:last-child td': { borderBottom: 0 } } } },
      MuiDialog: { styleOverrides: { paper: { border: `1px solid ${mode === 'dark' ? 'rgba(255,255,255,.1)' : 'rgba(24,20,38,.08)'}`, boxShadow: '0 24px 100px rgba(0,0,0,.28)' } } },
      MuiDialogTitle: { styleOverrides: { root: { fontWeight: 750 } } },
      MuiAlert: { styleOverrides: { root: { borderRadius: 12, alignItems: 'center' } } },
      MuiTabs: { styleOverrides: { indicator: { height: 3, borderRadius: '3px 3px 0 0' } } },
      MuiTab: { styleOverrides: { root: { textTransform: 'none', minHeight: 52, fontWeight: 650 } } },
      MuiTooltip: { defaultProps: { arrow: true } },
    },
  });
  return responsiveFontSizes(theme);
};
export default getTheme;
