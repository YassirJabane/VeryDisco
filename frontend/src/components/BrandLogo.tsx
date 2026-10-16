import React from 'react';
import { Box, SxProps, Theme, useTheme } from '@mui/material';

interface BrandLogoProps {
  variant?: 'full' | 'symbol';
  /** CSS width; height follows the original artwork's aspect ratio. */
  width?: number | string;
  decorative?: boolean;
  sx?: SxProps<Theme>;
}

/** Approved artwork, with the correct contrast for the active application theme. */
export default function BrandLogo({ variant = 'full', width = 260, decorative = false, sx }: BrandLogoProps) {
  const theme = useTheme();
  const name = variant === 'full' ? 'logo-horizontal' : 'symbol';
  const suffix = theme.palette.mode === 'dark' ? '-on-dark' : '';
  return <Box component="img" src={`/brand/${name}${suffix}.svg`} alt={decorative ? '' : 'VeryDisco'}
    width={variant === 'full' ? 520 : 120} height={120} draggable={false}
    sx={[{ display: 'block', width, height: 'auto', maxWidth: '100%', flexShrink: 0 }, ...(Array.isArray(sx) ? sx : [sx])]} />;
}
