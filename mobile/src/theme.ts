import { Platform } from 'react-native';

/**
 * Gold on black, taken from the compass mark.
 *
 * Colour carries meaning here rather than decoration: gold is the interface,
 * and green/red appear only on numbers that represent money moving. A control
 * deck where everything is coloured tells you nothing.
 */
export const C = {
  bg: '#050506',
  surface: '#0a0a0c',
  surface2: '#101012',
  raised: '#17171a',

  line: 'rgba(212,175,55,0.11)',
  lineStrong: 'rgba(212,175,55,0.20)',
  lineBright: 'rgba(212,175,55,0.34)',

  // the mark's gradient, sampled
  au: '#d4af37',
  auLit: '#f2da92',
  auMid: '#b8912b',
  auDeep: '#8b6914',
  auBg: 'rgba(212,175,55,0.09)',
  auBg2: 'rgba(212,175,55,0.16)',

  text: '#ece5d6',
  muted: '#8e8878',
  dim: '#5a564d',
  faint: '#38352f',

  up: '#3ecf8e',
  down: '#f0505a',
  upBg: 'rgba(62,207,142,0.11)',
  downBg: 'rgba(240,80,90,0.11)',

  track: 'rgba(212,175,55,0.11)',

  // Semantic names used across the screens. In this palette "warn" and
  // "info" are both gold — the deck speaks in one accent, and the only
  // second colour is money moving.
  accent: '#d4af37',
  warn: '#d4af37',
  warnBg: 'rgba(212,175,55,0.09)',
  info: '#b8912b',
  infoBg: 'rgba(212,175,55,0.09)',
} as const;

export const MONO = Platform.select({
  ios: 'Menlo',
  android: 'monospace',
  default: 'monospace',
}) as string;

/** Sharp corners read as instrumentation; rounded reads as consumer app. */
export const R = { sm: 2, md: 3, lg: 3, xl: 4, pill: 2 } as const;

export const tone = (v: number | null | undefined) =>
  v == null || Number.isNaN(v) ? C.muted : v > 0 ? C.up : v < 0 ? C.down : C.muted;

export const levelColor = (level?: string) => {
  switch (level) {
    case 'error': return C.down;
    case 'warn': return C.au;
    case 'success': return C.up;
    case 'debug': return C.faint;
    default: return '#b5af9f';
  }
};

/** Uppercase micro-label styling used across every panel header. */
export const LABEL = {
  fontSize: 9,
  fontWeight: '700' as const,
  letterSpacing: 1.9,
  textTransform: 'uppercase' as const,
  color: C.au,
  fontFamily: MONO,
};
