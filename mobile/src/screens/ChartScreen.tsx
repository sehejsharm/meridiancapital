import React, { useCallback, useEffect, useState } from 'react';
import {
  RefreshControl, ScrollView, StyleSheet, Text, useWindowDimensions, View,
} from 'react-native';

import * as api from '../api';
import { CandleChart, ChartLegend, TradeBoxChart } from '../components/Chart';
import { Bar, Card, Empty, Label, Pill, Row } from '../components/ui';
import { money, num, pct, signedMoney } from '../format';
import { useStore } from '../store';
import { C, R, tone } from '../theme';
import type { ChartData } from '../types';

export default function ChartScreen() {
  const { snapshot, status, feed } = useStore();
  const { width } = useWindowDimensions();
  const chartWidth = Math.max(240, width - 62);

  const [data, setData] = useState<ChartData | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setData(await api.chart());
    } catch {
      /* the empty state below already explains a missing feed */
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // The bot pushes a chart every 15s; poll at the same cadence so the screen
  // stays live without waiting on a socket message.
  useEffect(() => {
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  // A fill or a close changes the trade box immediately.
  const tradeEvents = feed.filter((e) => e.kind === 'entry' || e.kind === 'exit').length;
  useEffect(() => { if (tradeEvents) load(); }, [tradeEvents, load]);

  const pos = snapshot.position;
  const market = snapshot.market;
  const candles = data?.candles ?? [];

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: C.bg }}
      contentContainerStyle={{ padding: 16, paddingBottom: 30 }}
      refreshControl={<RefreshControl refreshing={busy} onRefresh={load} tintColor={C.muted} />}
    >
      {/* ---------------- underlying ---------------- */}
      <Card
        title={data?.index ? `${data.index} · 1 min` : 'Underlying · 1 min'}
        right={
          market?.spot ? (
            <View style={{ alignItems: 'flex-end' }}>
              <Text style={s.spot}>{num(market.spot, 2)}</Text>
              <Text style={[s.spotMove, { color: tone(market.day_move) }]}>
                {market.day_move >= 0 ? '+' : ''}{market.day_move.toFixed(1)} pts
              </Text>
            </View>
          ) : undefined
        }
      >
        {candles.length < 2 ? (
          <Empty
            text={
              data?.reason ??
              'Candles arrive once the bot is running — it charts the same feed it trades from.'
            }
          />
        ) : (
          <>
            <CandleChart
              candles={candles}
              vwap={data?.vwap}
              ema9={data?.ema9}
              ema21={data?.ema21}
              width={chartWidth}
            />
            <ChartLegend />
            {!!data?.source && (
              <Text style={s.source}>
                {candles.length} bars · from {data.source}
              </Text>
            )}
          </>
        )}
      </Card>

      {/* ---------------- trade box ---------------- */}
      {pos && data?.option ? (
        <Card>
          <View style={s.tbHead}>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Label>Open position</Label>
              <Text style={s.tbSym} numberOfLines={1}>{pos.symbol}</Text>
            </View>
            <Pill
              text={pos.stage}
              color={pos.stage === 'INIT' ? C.info : C.up}
              bg={pos.stage === 'INIT' ? C.infoBg : C.upBg}
            />
          </View>

          <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 9, marginBottom: 12 }}>
            <Text style={[s.tbPnl, { color: tone(pos.pnl) }]}>{signedMoney(pos.pnl)}</Text>
            <Text style={[s.tbPct, { color: tone(pos.pnl) }]}>{pct(pos.pnl_pct, 1)}</Text>
          </View>

          <TradeBoxChart
            track={data.option}
            current={pos.current}
            width={chartWidth}
          />

          <View style={s.tbGrid}>
            <Box k="Entry" v={money(pos.entry, 2)} color={C.text} />
            <Box k="Live" v={money(pos.current, 2)} color={tone(pos.pnl)} />
            <Box k="Stop" v={money(pos.sl_price, 2)} color={C.down} sub={pct(pos.sl_pct, 0)} />
            <Box k="Peak" v={money(pos.high_prem, 2)} color={C.up}
                 sub={pct(pos.peak_gain_pct, 1)} />
            <Box k="Quantity" v={String(pos.qty)} color={C.text} />
            <Box k="Risk left" v={money(pos.risk_left)} color={C.warn} />
          </View>

          {/* Ladder targets — this strategy trails rather than taking a fixed profit. */}
          {!!pos.levels && (
            <View style={s.ladder}>
              <Label>Ladder targets</Label>
              <Text style={s.ladderNote}>
                No fixed take-profit — each level ratchets the stop up, then it trails
                {' '}{pos.levels.free.giveback_pct}% behind the peak.
              </Text>
              <LadderRow
                label="Breakeven lock" price={pos.levels.breakeven.price}
                floor={pos.levels.breakeven.floor} pctTo={pos.levels.breakeven.pct}
                reached={['BE', 'LOCK1', 'LOCK2', 'FREE'].includes(pos.stage)}
                current={pos.current} entry={pos.entry}
              />
              <LadderRow
                label="Lock 1" price={pos.levels.lock1.price}
                floor={pos.levels.lock1.floor} pctTo={pos.levels.lock1.pct}
                reached={['LOCK1', 'LOCK2', 'FREE'].includes(pos.stage)}
                current={pos.current} entry={pos.entry}
              />
              <LadderRow
                label="Lock 2" price={pos.levels.lock2.price}
                floor={pos.levels.lock2.floor} pctTo={pos.levels.lock2.pct}
                reached={['LOCK2', 'FREE'].includes(pos.stage)}
                current={pos.current} entry={pos.entry}
              />
            </View>
          )}

          {!!pos.next_trigger && (
            <View style={{ marginTop: 13 }}>
              <View style={s.nextHead}>
                <Text style={s.nextLabel}>Next: {pos.next_trigger.label}</Text>
                <Text style={s.nextVal}>
                  {pos.next_trigger.target_pct
                    ? `${pct(pos.next_trigger.gain_pct, 1)} / +${pos.next_trigger.target_pct}%`
                    : pct(pos.next_trigger.gain_pct, 1)}
                </Text>
              </View>
              <Bar pct={(pos.next_trigger.progress ?? 1) * 100} color={C.up} height={7} />
            </View>
          )}

          {!!pos.entry_reason && <Text style={s.why}>{pos.entry_reason}</Text>}
        </Card>
      ) : (
        <Card title="Trade box">
          <Empty
            text={
              status?.running
                ? 'Flat. The trade box appears the moment a position opens, with entry, stop and every ladder level drawn live.'
                : 'The bot is not running.'
            }
          />
        </Card>
      )}

      {/* ---------------- context ---------------- */}
      {!!market && (
        <Card title="Chart context">
          <Row k="VWAP" v={`${num(market.vwap, 1)} · spot ${market.vwap_side}`} />
          <Row k="EMA 9 / 21" v={`${num(market.ema9, 1)} / ${num(market.ema21, 1)}`} />
          <Row k="EMA slope" v={num(market.slope, 2)} vColor={tone(market.slope)} />
          <Row k="ADX" v={`${num(market.adx, 1)} · ${market.trend}`} />
          <Row k="ATR" v={`${num(market.atr, 1)} pts`} />
          <Row k="Day range" v={`${num(market.day_range_pts, 0)} pts · ${num(market.day_range_pct, 2)}%`} />
        </Card>
      )}
    </ScrollView>
  );
}

function Box({ k, v, color, sub }: { k: string; v: string; color: string; sub?: string }) {
  return (
    <View style={s.box}>
      <Text style={s.boxK}>{k.toUpperCase()}</Text>
      <Text style={[s.boxV, { color }]} numberOfLines={1}>{v}</Text>
      {!!sub && <Text style={s.boxSub}>{sub}</Text>}
    </View>
  );
}

function LadderRow({
  label, price, floor, pctTo, reached, current, entry,
}: {
  label: string; price: number; floor?: number; pctTo: number;
  reached: boolean; current: number; entry: number;
}) {
  const away = entry > 0 ? ((price - current) / entry) * 100 : 0;
  return (
    <View style={s.lrow}>
      <View style={[s.lDot, reached && { backgroundColor: C.up }]} />
      <View style={{ width: 96 }}>
        <Text style={[s.lLabel, reached && { color: C.up }]}>{label}</Text>
        <Text style={s.lTarget}>at +{pctTo}%</Text>
      </View>
      <Text style={s.lPrice}>{money(price, 2)}</Text>
      <Text style={[s.lAway, { color: reached ? C.up : C.dim }]}>
        {reached ? 'reached' : `${away > 0 ? '+' : ''}${away.toFixed(1)}% away`}
      </Text>
      {floor != null && <Text style={s.lFloor}>→ SL {money(floor, 0)}</Text>}
    </View>
  );
}

const s = StyleSheet.create({
  spot: { color: C.text, fontSize: 14, fontWeight: '700', fontVariant: ['tabular-nums'] },
  spotMove: { fontSize: 10.5, fontWeight: '600', fontVariant: ['tabular-nums'] },
  source: { color: C.dim, fontSize: 10, marginTop: 8 },

  tbHead: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, marginBottom: 8 },
  tbSym: { color: C.text, fontSize: 16, fontWeight: '700', marginTop: 4 },
  tbPnl: { fontSize: 28, fontWeight: '800', letterSpacing: -1, fontVariant: ['tabular-nums'] },
  tbPct: { fontSize: 14, fontWeight: '700', fontVariant: ['tabular-nums'] },

  tbGrid: {
    flexDirection: 'row', flexWrap: 'wrap', gap: 9, marginTop: 14,
    paddingTop: 13, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: C.line,
  },
  box: {
    flexGrow: 1, flexBasis: '30%', backgroundColor: C.surface2,
    borderWidth: 1, borderColor: C.line, borderRadius: R.sm,
    paddingHorizontal: 10, paddingVertical: 9,
  },
  boxK: { fontSize: 9, letterSpacing: 0.6, color: C.dim, fontWeight: '700' },
  boxV: { fontSize: 14, fontWeight: '700', marginTop: 3, fontVariant: ['tabular-nums'] },
  boxSub: { fontSize: 10, color: C.muted, marginTop: 1, fontVariant: ['tabular-nums'] },

  ladder: {
    marginTop: 15, paddingTop: 13,
    borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: C.line,
  },
  ladderNote: { color: C.dim, fontSize: 10.5, lineHeight: 16, marginTop: 5, marginBottom: 9 },
  lrow: { flexDirection: 'row', alignItems: 'center', gap: 7, paddingVertical: 5 },
  lDot: {
    width: 6, height: 6, borderRadius: 99,
    backgroundColor: 'rgba(255,255,255,0.16)',
  },
  lLabel: { color: C.muted, fontSize: 11.5 },
  lTarget: { color: C.dim, fontSize: 9.5, marginTop: 1, fontVariant: ['tabular-nums'] },
  lPrice: {
    color: C.text, fontSize: 11.5, fontWeight: '700',
    fontVariant: ['tabular-nums'], width: 62,
  },
  lAway: { fontSize: 10.5, fontVariant: ['tabular-nums'], flex: 1 },
  lFloor: { color: C.dim, fontSize: 10, fontVariant: ['tabular-nums'] },

  nextHead: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 6 },
  nextLabel: { color: C.muted, fontSize: 11.5 },
  nextVal: { color: C.text, fontSize: 11.5, fontWeight: '700', fontVariant: ['tabular-nums'] },

  why: {
    color: C.muted, fontSize: 11.5, lineHeight: 18, marginTop: 12,
    paddingTop: 11, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: C.line,
  },
});
