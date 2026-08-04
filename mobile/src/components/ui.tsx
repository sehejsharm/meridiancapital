import React from 'react';
import {
  ActivityIndicator, Pressable, StyleSheet, Text, TextStyle,
  View, ViewStyle,
} from 'react-native';
import Svg, { Defs, LinearGradient, Path, Stop } from 'react-native-svg';

import { C, MONO, R, tone } from '../theme';

// ---------------------------------------------------------------- text

export const T = ({ style, mono, ...rest }: any) => (
  <Text
    {...rest}
    style={[
      { color: C.text, fontSize: 14 },
      mono && { fontFamily: MONO, fontVariant: ['tabular-nums'] },
      !mono && { fontVariant: ['tabular-nums'] },
      style,
    ]}
  />
);

export const Label = ({ children, style }: { children: React.ReactNode; style?: TextStyle }) => (
  <Text style={[s.label, style]}>{children}</Text>
);

// ---------------------------------------------------------------- card

export function Card({
  title, right, children, style, padded = true,
}: {
  title?: string; right?: React.ReactNode; children?: React.ReactNode;
  style?: ViewStyle; padded?: boolean;
}) {
  return (
    <View style={[s.card, !padded && { padding: 0 }, style]}>
      {(title || right) && (
        <View style={s.cardHead}>
          {title ? <Label>{title}</Label> : <View />}
          {right}
        </View>
      )}
      {children}
    </View>
  );
}

// ---------------------------------------------------------------- pill

export function Pill({
  text, color = C.muted, bg = 'rgba(255,255,255,0.06)', dot = false, pulse = false,
}: {
  text: string; color?: string; bg?: string; dot?: boolean; pulse?: boolean;
}) {
  return (
    <View style={[s.pill, { backgroundColor: bg }]}>
      {dot && (
        <View style={[s.dot, { backgroundColor: color, opacity: pulse ? 1 : 0.55 }]} />
      )}
      <Text style={[s.pillText, { color }]}>{text}</Text>
    </View>
  );
}

// ---------------------------------------------------------------- metric

export function Metric({
  k, v, sub, vColor, barPct, barColor,
}: {
  k: string; v: string; sub?: string; vColor?: string;
  barPct?: number; barColor?: string;
}) {
  return (
    <View style={s.metric}>
      <Text style={s.metricK}>{k.toUpperCase()}</Text>
      <Text style={[s.metricV, vColor ? { color: vColor } : null]} numberOfLines={1}>
        {v}
      </Text>
      {!!sub && <Text style={s.metricS} numberOfLines={1}>{sub}</Text>}
      {barPct !== undefined && (
        <Bar pct={barPct} color={barColor ?? C.info} style={{ marginTop: 9 }} />
      )}
    </View>
  );
}

export const MetricGrid = ({ children }: { children: React.ReactNode }) => (
  <View style={s.grid}>{children}</View>
);

// ---------------------------------------------------------------- bar

export function Bar({ pct, color = C.info, style, height = 5 }: {
  pct: number; color?: string; style?: ViewStyle; height?: number;
}) {
  const w = Math.max(0, Math.min(100, Number.isFinite(pct) ? pct : 0));
  return (
    <View style={[{ height, borderRadius: 99, backgroundColor: C.track, overflow: 'hidden' }, style]}>
      <View style={{ width: `${w}%`, height: '100%', borderRadius: 99, backgroundColor: color }} />
    </View>
  );
}

// ---------------------------------------------------------------- rows

export function Row({ k, v, vColor, mono = true }: {
  k: string; v: string; vColor?: string; mono?: boolean;
}) {
  return (
    <View style={s.row}>
      <Text style={s.rowK}>{k}</Text>
      <Text
        style={[
          s.rowV,
          vColor ? { color: vColor } : null,
          mono ? { fontVariant: ['tabular-nums'] } : null,
        ]}
        numberOfLines={2}
      >
        {v}
      </Text>
    </View>
  );
}

// ---------------------------------------------------------------- buttons

export function Button({
  title, onPress, variant = 'primary', style, loading, disabled,
}: {
  title: string; onPress?: () => void;
  variant?: 'primary' | 'ghost' | 'danger';
  style?: ViewStyle; loading?: boolean; disabled?: boolean;
}) {
  const bg = variant === 'primary' ? C.accent : variant === 'danger' ? C.down : C.surface2;
  const fg = variant === 'ghost' ? C.text : variant === 'danger' ? '#fff' : '#04120c';
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || loading}
      style={({ pressed }) => [
        s.btn,
        { backgroundColor: bg, opacity: disabled || loading ? 0.45 : pressed ? 0.82 : 1 },
        variant === 'ghost' && { borderWidth: 1, borderColor: C.line },
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={fg} size="small" />
      ) : (
        <Text style={[s.btnText, { color: fg }]}>{title}</Text>
      )}
    </Pressable>
  );
}

export function Segmented<V extends string>({
  options, value, onChange,
}: {
  options: { value: V; label: string }[];
  value: V;
  onChange: (v: V) => void;
}) {
  return (
    <View style={s.seg}>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <Pressable
            key={o.value}
            onPress={() => onChange(o.value)}
            style={[s.segBtn, on && { backgroundColor: C.raised }]}
          >
            <Text style={[s.segText, on && { color: C.text }]}>{o.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

export function Toggle({ on, onPress }: { on: boolean; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} style={[s.toggle, on && { backgroundColor: C.up }]}>
      <View style={[s.knob, on && { transform: [{ translateX: 19 }] }]} />
    </Pressable>
  );
}

// ---------------------------------------------------------------- misc

export const Empty = ({ text }: { text: string }) => (
  <View style={s.empty}>
    <Text style={s.emptyText}>{text}</Text>
  </View>
);

/** Filled area sparkline. Renders nothing below two points. */
export function Sparkline({
  values, height = 46, up,
}: { values: number[]; height?: number; up?: boolean }) {
  if (!values || values.length < 2) return <View style={{ height }} />;

  const W = 300;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const pts = values.map((v, i) => [
    (i / (values.length - 1)) * W,
    height - 3 - ((v - lo) / span) * (height - 8),
  ]);
  const line = pts
    .map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`)
    .join(' ');
  const rising = up ?? values[values.length - 1] >= values[0];
  const col = rising ? C.up : C.down;

  return (
    <Svg width="100%" height={height} viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none">
      <Defs>
        <LinearGradient id="sparkFill" x1="0" y1="0" x2="0" y2="1">
          <Stop offset="0%" stopColor={col} stopOpacity="0.28" />
          <Stop offset="100%" stopColor={col} stopOpacity="0" />
        </LinearGradient>
      </Defs>
      <Path d={`${line} L ${W} ${height} L 0 ${height} Z`} fill="url(#sparkFill)" />
      <Path d={line} fill="none" stroke={col} strokeWidth={1.8} strokeLinejoin="round" />
    </Svg>
  );
}

/** Horizontal daily P&L bars for a range of sessions. */
export function PnlBars({ values, height = 54 }: { values: number[]; height?: number }) {
  if (!values.length) return <View style={{ height }} />;
  const peak = Math.max(...values.map((v) => Math.abs(v)), 1);
  return (
    <View style={{ height, flexDirection: 'row', alignItems: 'center', gap: 3 }}>
      {values.slice(-40).map((v, i) => {
        const h = Math.max(2, (Math.abs(v) / peak) * (height / 2 - 2));
        return (
          <View key={i} style={{ flex: 1, height, justifyContent: 'center' }}>
            <View
              style={{
                height: h,
                backgroundColor: tone(v),
                borderRadius: 2,
                marginTop: v >= 0 ? height / 2 - h : height / 2,
                position: 'absolute',
                left: 0, right: 0,
                opacity: 0.85,
              }}
            />
          </View>
        );
      })}
    </View>
  );
}

const s = StyleSheet.create({
  label: {
    fontSize: 10.5, fontWeight: '700', letterSpacing: 1.1,
    textTransform: 'uppercase', color: C.dim,
  },
  card: {
    backgroundColor: C.surface, borderWidth: 1, borderColor: C.line,
    borderRadius: R.lg, padding: 15, marginBottom: 11,
  },
  cardHead: {
    flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', marginBottom: 11, gap: 8,
  },
  pill: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 9, paddingVertical: 4, borderRadius: R.pill,
  },
  pillText: { fontSize: 10.5, fontWeight: '700', letterSpacing: 0.5, textTransform: 'uppercase' },
  dot: { width: 6, height: 6, borderRadius: 99 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 9 },
  metric: {
    backgroundColor: C.surface2, borderWidth: 1, borderColor: C.line,
    borderRadius: R.md, paddingHorizontal: 12, paddingVertical: 11,
    flexGrow: 1, flexBasis: '46%',
  },
  metricK: { fontSize: 9.5, letterSpacing: 0.7, color: C.dim, fontWeight: '700' },
  metricV: {
    fontSize: 17, fontWeight: '700', color: C.text, marginTop: 4,
    letterSpacing: -0.3, fontVariant: ['tabular-nums'],
  },
  metricS: { fontSize: 11, color: C.muted, marginTop: 2 },
  row: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingVertical: 9, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: C.line,
    gap: 12,
  },
  rowK: { color: C.muted, fontSize: 13.5, flexShrink: 0 },
  rowV: { color: C.text, fontSize: 13.5, fontWeight: '600', textAlign: 'right', flexShrink: 1 },
  btn: {
    borderRadius: R.md, paddingVertical: 14, paddingHorizontal: 16,
    alignItems: 'center', justifyContent: 'center',
  },
  btnText: { fontSize: 15, fontWeight: '700', letterSpacing: 0.1 },
  seg: {
    flexDirection: 'row', backgroundColor: C.surface2, borderWidth: 1,
    borderColor: C.line, borderRadius: 12, padding: 3, gap: 2, marginBottom: 11,
  },
  segBtn: { flex: 1, paddingVertical: 9, borderRadius: R.sm, alignItems: 'center' },
  segText: { fontSize: 12, fontWeight: '700', color: C.muted },
  toggle: {
    width: 46, height: 27, borderRadius: 99,
    backgroundColor: 'rgba(255,255,255,0.12)', justifyContent: 'center', padding: 3,
  },
  knob: { width: 21, height: 21, borderRadius: 99, backgroundColor: '#fff' },
  empty: { paddingVertical: 34, paddingHorizontal: 18, alignItems: 'center' },
  emptyText: { color: C.dim, fontSize: 13, textAlign: 'center', lineHeight: 20 },
});
