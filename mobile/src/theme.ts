import { Platform } from 'react-native';

export const C = {
  bg: '#05070d',
  surface: '#0b1119',
  surface2: '#111926',
  raised: '#16202f',
  line: 'rgba(255,255,255,0.075)',
  lineStrong: 'rgba(255,255,255,0.14)',

  text: '#e9eef8',
  muted: '#78849a',
  dim: '#4d5768',

  up: '#35d6a0',
  down: '#ff5a6e',
  warn: '#ffb03a',
  info: '#57a6ff',
  accent: '#35d6a0',

  upBg: 'rgba(53,214,160,0.12)',
  downBg: 'rgba(255,90,110,0.12)',
  warnBg: 'rgba(255,176,58,0.12)',
  infoBg: 'rgba(87,166,255,0.12)',
  track: 'rgba(255,255,255,0.07)',
} as const;

export const MONO = Platform.select({
  ios: 'Menlo',
  android: 'monospace',
  default: 'monospace',
}) as string;

export const R = { sm: 9, md: 13, lg: 17, xl: 20, pill: 999 } as const;

/** Tone -> colour, used everywhere P&L is shown. */
export const tone = (v: number | null | undefined) =>
  v == null || Number.isNaN(v) ? C.muted : v > 0 ? C.up : v < 0 ? C.down : C.muted;

export const levelColor = (level?: string) => {
  switch (level) {
    case 'error': return C.down;
    case 'warn': return C.warn;
    case 'success': return C.up;
    case 'debug': return C.dim;
    default: return '#c3cddd';
  }
};
