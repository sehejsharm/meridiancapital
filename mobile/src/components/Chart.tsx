import React, { useMemo } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import Svg, {
  Circle, Defs, G, Line, LinearGradient, Path, Rect, Stop, Text as SvgText,
} from 'react-native-svg';

import { C, MONO } from '../theme';
import type { Candle, OptionTrack } from '../types';

const PAD = { top: 10, right: 52, bottom: 18, left: 6 };

interface Scale {
  x: (i: number) => number;
  y: (v: number) => number;
  lo: number;
  hi: number;
}

function makeScale(
  count: number, lo: number, hi: number, w: number, h: number,
): Scale {
  const span = hi - lo || 1;
  const plotW = w - PAD.left - PAD.right;
  const plotH = h - PAD.top - PAD.bottom;
  return {
    x: (i) => PAD.left + (count <= 1 ? plotW / 2 : (i / (count - 1)) * plotW),
    y: (v) => PAD.top + plotH - ((v - lo) / span) * plotH,
    lo,
    hi,
  };
}

const fmtTime = (ms: number) =>
  new Date(ms).toLocaleTimeString('en-GB', {
    hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata',
  });

/** Candlestick chart for the underlying, with VWAP and EMA overlays. */
export function CandleChart({
  candles, vwap, ema9, ema21, width, height = 230,
}: {
  candles: Candle[];
  vwap?: (number | null)[];
  ema9?: (number | null)[];
  ema21?: (number | null)[];
  width: number;
  height?: number;
}) {
  const view = useMemo(() => {
    if (!candles.length) return null;

    const highs = candles.map((c) => c[2]);
    const lows = candles.map((c) => c[3]);
    let lo = Math.min(...lows);
    let hi = Math.max(...highs);
    for (const series of [vwap, ema9, ema21]) {
      if (!series) continue;
      for (const v of series) {
        if (v == null) continue;
        lo = Math.min(lo, v);
        hi = Math.max(hi, v);
      }
    }
    const pad = (hi - lo) * 0.06 || 1;
    const s = makeScale(candles.length, lo - pad, hi + pad, width, height);

    const bodyW = Math.max(
      1,
      Math.min(7, ((width - PAD.left - PAD.right) / candles.length) * 0.68),
    );

    const line = (series?: (number | null)[]) => {
      if (!series) return '';
      let d = '';
      let pen = false;
      series.forEach((v, i) => {
        if (v == null) { pen = false; return; }
        d += `${pen ? 'L' : 'M'}${s.x(i).toFixed(1)} ${s.y(v).toFixed(1)}`;
        pen = true;
      });
      return d;
    };

    // Four gridlines, rounded to something a person would read.
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => s.lo + (s.hi - s.lo) * f);

    return {
      s, bodyW, ticks,
      vwapPath: line(vwap),
      ema9Path: line(ema9),
      ema21Path: line(ema21),
      last: candles[candles.length - 1],
      first: candles[0],
    };
  }, [candles, vwap, ema9, ema21, width, height]);

  if (!view) return null;
  const { s, bodyW, ticks, vwapPath, ema9Path, ema21Path, last, first } = view;

  return (
    <Svg width={width} height={height}>
      {ticks.map((t, i) => (
        <G key={i}>
          <Line
            x1={PAD.left} x2={width - PAD.right} y1={s.y(t)} y2={s.y(t)}
            stroke={C.line} strokeWidth={1}
          />
          <SvgText
            x={width - PAD.right + 5} y={s.y(t) + 3.5}
            fill={C.dim} fontSize={9} fontFamily={MONO}
          >
            {t.toFixed(0)}
          </SvgText>
        </G>
      ))}

      {candles.map((c, i) => {
        const [, o, h, l, cl] = c;
        const rising = cl >= o;
        const col = rising ? C.up : C.down;
        const x = s.x(i);
        const top = s.y(Math.max(o, cl));
        const bot = s.y(Math.min(o, cl));
        return (
          <G key={i}>
            <Line x1={x} x2={x} y1={s.y(h)} y2={s.y(l)} stroke={col} strokeWidth={1} opacity={0.85} />
            <Rect
              x={x - bodyW / 2} y={top}
              width={bodyW} height={Math.max(1, bot - top)}
              fill={col} opacity={0.9}
            />
          </G>
        );
      })}

      {!!vwapPath && (
        <Path d={vwapPath} stroke={C.warn} strokeWidth={1.3} fill="none"
              strokeDasharray="4 3" opacity={0.9} />
      )}
      {!!ema9Path && <Path d={ema9Path} stroke={C.info} strokeWidth={1.2} fill="none" opacity={0.8} />}
      {!!ema21Path && <Path d={ema21Path} stroke="#9a7dff" strokeWidth={1.2} fill="none" opacity={0.7} />}

      <SvgText x={PAD.left + 2} y={height - 5} fill={C.dim} fontSize={9} fontFamily={MONO}>
        {fmtTime(first[0])}
      </SvgText>
      <SvgText
        x={width - PAD.right} y={height - 5} fill={C.dim} fontSize={9}
        fontFamily={MONO} textAnchor="end"
      >
        {fmtTime(last[0])}
      </SvgText>
    </Svg>
  );
}

/**
 * The open contract's premium with the risk ladder drawn on it.
 *
 * This strategy has no fixed take-profit — the lines above entry are the
 * points where the stop ratchets up, and the final stage trails the peak.
 */
export function TradeBoxChart({
  track, current, width, height = 210,
}: {
  track: OptionTrack;
  current: number;
  width: number;
  height?: number;
}) {
  const view = useMemo(() => {
    const series = track.series ?? [];
    const prices = series.map((p) => p[1]);
    const levels = track.levels;

    const candidates = [
      ...prices, current, track.entry, track.sl,
      levels?.breakeven?.price, levels?.lock1?.price,
    ].filter((v): v is number => typeof v === 'number' && Number.isFinite(v));

    if (candidates.length < 2) return null;

    let lo = Math.min(...candidates);
    let hi = Math.max(...candidates);
    const pad = (hi - lo) * 0.1 || 1;
    lo -= pad;
    hi += pad;

    const s = makeScale(Math.max(series.length, 2), lo, hi, width, height);
    const path = prices
      .map((v, i) => `${i ? 'L' : 'M'}${s.x(i).toFixed(1)} ${s.y(v).toFixed(1)}`)
      .join('');

    return { s, path, series, prices };
  }, [track, current, width, height]);

  if (!view) {
    return (
      <View style={{ height, alignItems: 'center', justifyContent: 'center' }}>
        <Text style={{ color: C.dim, fontSize: 12 }}>
          Collecting premium ticks…
        </Text>
      </View>
    );
  }

  const { s, path, prices } = view;
  const levels = track.levels;
  const inProfit = current >= track.entry;

  const marker = (
    price: number | undefined, color: string, label: string,
    dash?: string, key?: string,
  ) => {
    if (price == null || price < s.lo || price > s.hi) return null;
    const y = s.y(price);
    return (
      <G key={key ?? label}>
        <Line
          x1={PAD.left} x2={width - PAD.right} y1={y} y2={y}
          stroke={color} strokeWidth={1} strokeDasharray={dash} opacity={0.85}
        />
        <SvgText
          x={width - PAD.right + 4} y={y + 3.5}
          fill={color} fontSize={8.5} fontFamily={MONO}
        >
          {price.toFixed(1)}
        </SvgText>
        <SvgText x={PAD.left + 3} y={y - 4} fill={color} fontSize={8.5} opacity={0.9}>
          {label}
        </SvgText>
      </G>
    );
  };

  return (
    <Svg width={width} height={height}>
      <Defs>
        <LinearGradient id="premFill" x1="0" y1="0" x2="0" y2="1">
          <Stop offset="0%" stopColor={inProfit ? C.up : C.down} stopOpacity="0.22" />
          <Stop offset="100%" stopColor={inProfit ? C.up : C.down} stopOpacity="0" />
        </LinearGradient>
      </Defs>

      {/* Everything below the stop is money already conceded. */}
      {track.sl >= s.lo && track.sl <= s.hi && (
        <Rect
          x={PAD.left} y={s.y(track.sl)}
          width={width - PAD.right - PAD.left}
          height={Math.max(0, height - PAD.bottom - s.y(track.sl))}
          fill={C.down} opacity={0.07}
        />
      )}

      {prices.length > 1 && (
        <>
          <Path
            d={`${path} L ${s.x(prices.length - 1)} ${height - PAD.bottom} L ${s.x(0)} ${height - PAD.bottom} Z`}
            fill="url(#premFill)"
          />
          <Path d={path} stroke={inProfit ? C.up : C.down} strokeWidth={1.7} fill="none" />
          <Circle
            cx={s.x(prices.length - 1)} cy={s.y(prices[prices.length - 1])}
            r={3} fill={inProfit ? C.up : C.down}
          />
        </>
      )}

      {marker(levels?.lock2?.price, '#9a7dff', 'LOCK 2', '3 3')}
      {marker(levels?.lock1?.price, C.info, 'LOCK 1', '3 3')}
      {marker(levels?.breakeven?.price, C.warn, 'BREAKEVEN', '3 3')}
      {marker(track.entry, C.text, 'ENTRY')}
      {marker(track.sl, C.down, 'STOP')}
      {track.high_prem > track.entry && marker(track.high_prem, C.up, 'PEAK', '1 4')}
    </Svg>
  );
}

export const ChartLegend = () => (
  <View style={s.legend}>
    <Key color={C.info} label="EMA 9" />
    <Key color="#9a7dff" label="EMA 21" />
    <Key color={C.warn} label="VWAP" dashed />
    <Key color={C.up} label="Up" />
    <Key color={C.down} label="Down" />
  </View>
);

const Key = ({ color, label, dashed }: { color: string; label: string; dashed?: boolean }) => (
  <View style={s.key}>
    <View
      style={[
        s.swatch,
        { backgroundColor: dashed ? 'transparent' : color },
        dashed && { borderTopWidth: 2, borderTopColor: color, borderStyle: 'dashed', height: 0 },
      ]}
    />
    <Text style={s.keyText}>{label}</Text>
  </View>
);

const s = StyleSheet.create({
  legend: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, marginTop: 10 },
  key: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  swatch: { width: 11, height: 2.5, borderRadius: 2 },
  keyText: { color: C.dim, fontSize: 10, letterSpacing: 0.3 },
});
