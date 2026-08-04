import React, { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import {
  Bar, Card, Empty, Label, Metric, MetricGrid, Pill, Row, Sparkline,
} from '../components/ui';
import { money, pct, signedMoney } from '../format';
import { useStore } from '../store';
import { C, R, tone } from '../theme';
import type { Position } from '../types';

const STAGES: Position['stage'][] = ['INIT', 'BE', 'LOCK1', 'LOCK2', 'FREE'];

export default function DashboardScreen() {
  const { snapshot: s, status, marks, refresh, error } = useStore();
  const [refreshing, setRefreshing] = useState(false);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await refresh();
    setRefreshing(false);
  }, [refresh]);

  const m = s.market;
  const pos = s.position ?? null;
  const dayPnl = s.day_pnl ?? 0;
  const equity = s.equity;
  const openEq = s.open_equity;

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: C.bg }}
      contentContainerStyle={{ padding: 16, paddingBottom: 30 }}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.muted} />
      }
    >
      {!!error && (
        <View style={s2.banner}>
          <Text style={s2.bannerText}>{error}</Text>
        </View>
      )}

      {/* ---------------- equity hero ---------------- */}
      <View style={s2.hero}>
        <Label>Equity</Label>
        <Text style={s2.heroEq}>{equity == null ? '₹—' : money(equity, 2)}</Text>
        <View style={s2.heroSub}>
          <View style={[s2.chip, { backgroundColor: dayPnl >= 0 ? C.upBg : C.downBg }]}>
            <Text style={[s2.chipText, { color: tone(dayPnl) }]}>{signedMoney(dayPnl)}</Text>
          </View>
          <Text style={s2.heroSubText}>
            {openEq ? `${pct((dayPnl / openEq) * 100)} today` : 'today'}
          </Text>
        </View>

        <View style={{ marginTop: 13 }}>
          <Sparkline values={marks.map((k) => k.equity)} />
        </View>

        <View style={s2.heroFoot}>
          <Foot k="Since seed" v={pct(s.total_return_pct ?? 0, 1)}
                color={tone(s.total_return_pct ?? 0)} />
          <Foot k="Peak" v={money(s.peak_equity)} />
          <Foot k="Drawdown" v={pct(s.drawdown_pct ?? 0, 1)}
                color={(s.drawdown_pct ?? 0) < 0 ? C.down : C.muted} />
        </View>
      </View>

      {/* ---------------- position or reason ---------------- */}
      {pos ? <PositionCard p={pos} /> : <ReasonCard />}

      {/* ---------------- market ---------------- */}
      <Card
        title="Market atmosphere"
        right={
          m?.spot ? (
            <Text style={s2.spot}>
              NIFTY {m.spot.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </Text>
          ) : undefined
        }
      >
        {!m?.spot ? (
          <Empty text="No market data yet. The bot pulls candles once it is running." />
        ) : (
          <MetricGrid>
            <Metric
              k="Volatility" v={`${m.garch.toFixed(1)}%`} sub={m.vol_regime}
              barPct={(m.garch / 30) * 100}
              barColor={m.garch >= 9 ? C.up : C.warn}
            />
            <Metric
              k="Trend" v={`ADX ${m.adx.toFixed(1)}`} sub={m.trend}
              barPct={(m.adx / 40) * 100}
              barColor={m.adx >= 20 ? C.up : C.warn}
            />
            <Metric
              k="Direction" v={m.direction}
              sub={`EMA ${m.ema9.toFixed(0)} / ${m.ema21.toFixed(0)}`}
            />
            <Metric
              k="Efficiency" v={m.ser.toFixed(3)} sub={m.efficiency}
              barPct={(m.ser / 0.3) * 100}
              barColor={m.ser > 0.15 ? C.up : C.warn}
            />
            <Metric k="VWAP" v={m.vwap.toFixed(1)} sub={`spot is ${m.vwap_side}`} />
            <Metric
              k="Day range" v={`${m.day_range_pts.toFixed(0)} pts`}
              sub={`${m.day_move >= 0 ? '+' : ''}${m.day_move.toFixed(0)} pts move`}
            />
          </MetricGrid>
        )}
      </Card>

      {/* ---------------- risk ---------------- */}
      <Card title="Risk rails">
        <MetricGrid>
          <Metric
            k="Daily kill" v={money(s.kill_used ?? 0)}
            sub={`of ${money(s.kill_limit ?? 3000)} used`}
            barPct={((s.kill_used ?? 0) / (s.kill_limit ?? 3000)) * 100}
            barColor={(s.kill_used ?? 0) > (s.kill_limit ?? 3000) * 0.6 ? C.down : C.warn}
          />
          <Metric
            k="Trades" v={`${s.trades ?? 0} / ${s.max_trades ?? 3}`}
            sub={s.killed ? 'kill switch hit' : 'today'}
            barPct={((s.trades ?? 0) / (s.max_trades ?? 3)) * 100}
            barColor={C.info}
          />
          <Metric
            k="Unrealised" v={signedMoney(s.unrealised ?? 0)}
            vColor={tone(s.unrealised ?? 0)}
            sub={pos ? 'open position' : 'flat'}
          />
          <Metric
            k="Chop filter" v={s.chop_skip ? 'BLOCKED' : 'CLEAR'}
            vColor={s.chop_skip ? C.warn : C.up}
            sub={s.chop_score != null ? `score ${s.chop_score.toFixed(3)}` : 'not scored'}
          />
        </MetricGrid>
      </Card>

      {/* ---------------- window ---------------- */}
      {!!s.window && (
        <Card title="Session window">
          <Row k="Entries allowed" v={`${s.window.open} — ${s.window.cutoff}`} />
          <Row k="Force close" v={s.window.force_close} />
          <Row k="Report filed" v={s.window.end} />
          <Row k="Supervisor stop" v={status?.schedule.stop ?? '—'} />
        </Card>
      )}

      {/* ---------------- latency ---------------- */}
      <LatencyCard />
    </ScrollView>
  );
}

function Foot({ k, v, color }: { k: string; v: string; color?: string }) {
  return (
    <View style={{ flex: 1 }}>
      <Text style={s2.footK}>{k.toUpperCase()}</Text>
      <Text style={[s2.footV, color ? { color } : null]}>{v}</Text>
    </View>
  );
}

function PositionCard({ p }: { p: Position }) {
  const nt = p.next_trigger;
  const stageIdx = Math.max(0, STAGES.indexOf(p.stage));
  const locked = stageIdx >= 1;

  return (
    <Card>
      <View style={s2.posTop}>
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={s2.posSym} numberOfLines={1}>{p.symbol}</Text>
          <Text style={s2.posMeta}>
            {p.qty} qty · entry {money(p.entry, 2)} · {p.entry_time?.slice(11) ?? ''}
          </Text>
        </View>
        <Pill
          text={p.stage}
          color={locked ? C.up : C.info}
          bg={locked ? C.upBg : C.infoBg}
        />
      </View>

      <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 9 }}>
        <Text style={[s2.pnlBig, { color: tone(p.pnl) }]}>{signedMoney(p.pnl)}</Text>
        <Text style={[s2.pnlPct, { color: tone(p.pnl) }]}>{pct(p.pnl_pct, 1)}</Text>
      </View>

      {/* ladder */}
      <View style={{ marginTop: 15 }}>
        <View style={s2.ladderHead}>
          <Text style={s2.ladderLabel}>{nt?.label ?? 'Trailing behind peak'}</Text>
          <Text style={s2.ladderVal}>
            {nt?.target_pct
              ? `${pct(nt.gain_pct, 1)} / +${nt.target_pct}%`
              : pct(nt?.gain_pct ?? 0, 1)}
          </Text>
        </View>
        <Bar pct={(nt?.progress ?? 1) * 100} color={C.up} height={7} />
        <View style={s2.rungs}>
          {STAGES.map((st, i) => (
            <View key={st} style={{ flex: 1, alignItems: 'center' }}>
              <View style={[s2.rungDot, i <= stageIdx && { backgroundColor: C.up }]} />
              <Text style={[s2.rungText, i <= stageIdx && { color: C.up }]}>{st}</Text>
            </View>
          ))}
        </View>
      </View>

      <View style={s2.posGrid}>
        <Cell k="Last price" v={money(p.current, 2)} />
        <Cell k="Stop loss" v={money(p.sl_price, 2)} sub={pct(p.sl_pct, 0)} />
        <Cell k="Peak seen" v={money(p.high_prem, 2)} />
        <Cell k="Risk left" v={money(p.risk_left)} />
        <Cell k="Lot cost" v={money(p.lot_cost)} />
        <Cell k="Margin" v={money(p.real_margin)} />
      </View>

      {!!p.entry_reason && <Text style={s2.why}>{p.entry_reason}</Text>}
    </Card>
  );
}

function Cell({ k, v, sub }: { k: string; v: string; sub?: string }) {
  return (
    <View style={{ flexBasis: '31%', flexGrow: 1 }}>
      <Text style={s2.cellK}>{k.toUpperCase()}</Text>
      <Text style={s2.cellV} numberOfLines={1}>
        {v}
        {!!sub && <Text style={s2.cellSub}> {sub}</Text>}
      </Text>
    </View>
  );
}

function ReasonCard() {
  const { snapshot: s, status } = useStore();
  const d = s.decision;
  const running = status?.running;

  const code = d?.reason ?? (running ? 'WATCHING' : 'BOT NOT RUNNING');
  const detail =
    d?.detail ??
    (running
      ? 'Evaluating the market.'
      : status?.not_trading_reason ??
        'Start the session from Control, or wait for the 09:15 schedule.');

  return (
    <Card>
      <View style={{ flexDirection: 'row', gap: 11 }}>
        <View style={s2.reasonIcon}>
          <Text style={{ color: C.warn, fontSize: 16 }}>◎</Text>
        </View>
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={s2.reasonCode}>{code}</Text>
          <Text style={s2.reasonText}>{detail}</Text>
        </View>
      </View>
    </Card>
  );
}

function LatencyCard() {
  const { snapshot } = useStore();
  const lat = snapshot.latency ?? {};
  const keys = Object.keys(lat).filter((k) => lat[k]);

  return (
    <Card title="Angel One latency">
      {keys.length === 0 ? (
        <Empty text="No API calls measured yet." />
      ) : (
        keys.slice(0, 6).map((k) => {
          const v = lat[k]!;
          return (
            <Row
              key={k}
              k={k}
              v={`${v.avg.toFixed(0)}ms avg · p95 ${v.p95.toFixed(0)}ms · n=${v.n}`}
            />
          );
        })
      )}
    </Card>
  );
}

const s2 = StyleSheet.create({
  banner: {
    backgroundColor: C.downBg, borderWidth: 1, borderColor: 'rgba(255,90,110,0.3)',
    borderRadius: R.md, padding: 12, marginBottom: 11,
  },
  bannerText: { color: C.down, fontSize: 12.5, lineHeight: 18 },

  hero: {
    backgroundColor: '#0d1622', borderWidth: 1, borderColor: C.lineStrong,
    borderRadius: R.xl, padding: 18, marginBottom: 11,
  },
  heroEq: {
    color: C.text, fontSize: 38, fontWeight: '700', letterSpacing: -1.4,
    marginTop: 5, marginBottom: 3, fontVariant: ['tabular-nums'],
  },
  heroSub: { flexDirection: 'row', alignItems: 'center', gap: 9 },
  chip: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 8 },
  chipText: { fontSize: 11.5, fontWeight: '700', fontVariant: ['tabular-nums'] },
  heroSubText: { color: C.muted, fontSize: 13, fontVariant: ['tabular-nums'] },
  heroFoot: {
    flexDirection: 'row', gap: 18, marginTop: 12, paddingTop: 12,
    borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: C.line,
  },
  footK: { fontSize: 10, letterSpacing: 0.7, color: C.dim, fontWeight: '700' },
  footV: {
    fontSize: 14.5, fontWeight: '700', color: C.text, marginTop: 2,
    fontVariant: ['tabular-nums'],
  },

  spot: { color: C.muted, fontSize: 12, fontVariant: ['tabular-nums'] },

  posTop: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, marginBottom: 13 },
  posSym: { color: C.text, fontSize: 16.5, fontWeight: '700', letterSpacing: -0.2 },
  posMeta: { color: C.muted, fontSize: 11.5, marginTop: 3 },
  pnlBig: { fontSize: 31, fontWeight: '700', letterSpacing: -1.1, fontVariant: ['tabular-nums'] },
  pnlPct: { fontSize: 14, fontWeight: '700', fontVariant: ['tabular-nums'] },

  ladderHead: {
    flexDirection: 'row', justifyContent: 'space-between', marginBottom: 7, gap: 8,
  },
  ladderLabel: { color: C.muted, fontSize: 11.5, flexShrink: 1 },
  ladderVal: { color: C.text, fontSize: 11.5, fontWeight: '700', fontVariant: ['tabular-nums'] },
  rungs: { flexDirection: 'row', marginTop: 8 },
  rungDot: {
    width: 7, height: 7, borderRadius: 99,
    backgroundColor: 'rgba(255,255,255,0.14)', marginBottom: 5,
  },
  rungText: { fontSize: 9, letterSpacing: 0.4, color: C.dim, fontWeight: '700' },

  posGrid: {
    flexDirection: 'row', flexWrap: 'wrap', gap: 11, marginTop: 15,
    paddingTop: 14, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: C.line,
  },
  cellK: { fontSize: 9.5, letterSpacing: 0.6, color: C.dim, fontWeight: '700' },
  cellV: {
    fontSize: 14, fontWeight: '700', color: C.text, marginTop: 3,
    fontVariant: ['tabular-nums'],
  },
  cellSub: { fontSize: 11, color: C.muted, fontWeight: '500' },
  why: {
    color: C.muted, fontSize: 11.5, lineHeight: 18, marginTop: 12, paddingTop: 11,
    borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: C.line,
  },

  reasonIcon: {
    width: 34, height: 34, borderRadius: 11, backgroundColor: C.warnBg,
    alignItems: 'center', justifyContent: 'center',
  },
  reasonCode: {
    fontSize: 11, fontWeight: '800', letterSpacing: 0.7,
    color: C.warn, textTransform: 'uppercase',
  },
  reasonText: { fontSize: 13.5, lineHeight: 20, marginTop: 4, color: C.text },
});
