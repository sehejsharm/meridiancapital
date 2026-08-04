import React, { useCallback, useEffect, useState } from 'react';
import {
  Modal, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View,
} from 'react-native';

import * as api from '../api';
import {
  Button, Card, Empty, Label, Metric, MetricGrid, Row, Segmented,
} from '../components/ui';
import { istToday, money, pct, signedMoney } from '../format';
import { useStore } from '../store';
import { C, tone } from '../theme';
import type { Period, Summary, Trade } from '../types';

const PERIODS: { value: Period; label: string }[] = [
  { value: 'day', label: 'Today' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'quarter', label: 'Qtr' },
  { value: 'year', label: 'Year' },
];

export default function TradesScreen() {
  const { feed } = useStore();
  const [period, setPeriod] = useState<Period>('day');
  const [rows, setRows] = useState<Trade[]>([]);
  const [sum, setSum] = useState<Summary | null>(null);
  const [label, setLabel] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [detail, setDetail] = useState<Trade | null>(null);

  const anchor = istToday();

  const load = useCallback(async () => {
    setBusy(true);
    setErr('');
    try {
      const [t, s] = await Promise.all([
        api.trades(period, anchor),
        api.summary(period, anchor),
      ]);
      setRows(t.trades);
      setLabel(t.range.label);
      setSum(s.summary);
    } catch (e: any) {
      setErr(e?.message ?? 'Could not load trades');
    } finally {
      setBusy(false);
    }
  }, [period, anchor]);

  useEffect(() => { load(); }, [load]);

  // A close on the live feed means this list is out of date.
  const lastExit = feed.filter((e) => e.kind === 'exit').length;
  useEffect(() => { if (lastExit) load(); }, [lastExit, load]);

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: C.bg }}
      contentContainerStyle={{ padding: 16, paddingBottom: 30 }}
      refreshControl={<RefreshControl refreshing={busy} onRefresh={load} tintColor={C.muted} />}
    >
      <Segmented options={PERIODS} value={period} onChange={setPeriod} />

      {!!err && <Card><Text style={{ color: C.down, fontSize: 13 }}>{err}</Text></Card>}

      {sum && (
        <Card title={label}>
          <MetricGrid>
            <Metric
              k="Net P&L" v={signedMoney(sum.net_pnl)} vColor={tone(sum.net_pnl)}
              sub={`${sum.trades} trades`}
            />
            <Metric
              k="Win rate" v={`${sum.win_rate.toFixed(0)}%`}
              sub={`${sum.wins}W / ${sum.losses}L`}
              barPct={sum.win_rate} barColor={sum.win_rate >= 50 ? C.up : C.warn}
            />
            <Metric
              k="Profit factor"
              v={sum.profit_factor != null ? sum.profit_factor.toFixed(2) : '—'}
              sub={`${money(sum.gross_profit)} / ${money(sum.gross_loss)}`}
            />
            <Metric
              k="Charges" v={money(sum.charges)}
              sub={`avg hold ${sum.avg_hold_min}m`}
            />
          </MetricGrid>
        </Card>
      )}

      {rows.length === 0 && !busy ? (
        <Card>
          <Empty
            text={
              'No trades in this window.\n\nDays the algorithm sat out still record why — see Reports and the Live feed.'
            }
          />
        </Card>
      ) : (
        [...rows].reverse().map((t) => (
          <TradeRow key={t.id ?? `${t.entry_time}-${t.symbol}`} t={t} onPress={() => setDetail(t)} />
        ))
      )}

      <TradeDetail trade={detail} onClose={() => setDetail(null)} />
    </ScrollView>
  );
}

function TradeRow({ t, onPress }: { t: Trade; onPress: () => void }) {
  const move = t.avg_entry ? ((t.exit_fill - t.avg_entry) / t.avg_entry) * 100 : 0;
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [s.trade, pressed && { opacity: 0.75 }]}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 10 }}>
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={s.sym} numberOfLines={1}>{t.symbol}</Text>
          <Text style={s.time}>
            {(t.entry_time ?? '').slice(11)} → {t.exit_time} · {t.hold_min}m · {t.session_date}
          </Text>
        </View>
        <View style={{ alignItems: 'flex-end' }}>
          <Text style={[s.pnl, { color: tone(t.net_pnl) }]}>{signedMoney(t.net_pnl)}</Text>
          <Text style={[s.move, { color: tone(move) }]}>{pct(move, 1)}</Text>
        </View>
      </View>
      <View style={s.tags}>
        <Tag text={t.reason} />
        <Tag text={`stage ${t.stage}`} />
        <Tag text={`${money(t.avg_entry, 2)} → ${money(t.exit_fill, 2)}`} />
        <Tag text={`${t.qty} qty`} />
      </View>
    </Pressable>
  );
}

const Tag = ({ text }: { text: string }) => (
  <View style={s.tag}>
    <Text style={s.tagText}>{text}</Text>
  </View>
);

function TradeDetail({ trade, onClose }: { trade: Trade | null; onClose: () => void }) {
  if (!trade) return null;
  const t = trade;
  const move = t.avg_entry ? ((t.exit_fill - t.avg_entry) / t.avg_entry) * 100 : 0;
  const modelGap =
    t.model_prem ? ((t.avg_entry - t.model_prem) / t.model_prem) * 100 : null;

  return (
    <Modal visible animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <ScrollView style={{ flex: 1, backgroundColor: C.bg }} contentContainerStyle={{ padding: 16 }}>
        <View style={{ marginBottom: 14 }}>
          <Label>{t.session_date}</Label>
          <Text style={s.detailSym}>{t.symbol}</Text>
          <Text style={[s.detailPnl, { color: tone(t.net_pnl) }]}>
            {signedMoney(t.net_pnl, 2)}
            <Text style={{ fontSize: 15, color: tone(move) }}>  {pct(move, 1)}</Text>
          </Text>
        </View>

        <Card title="Execution">
          <Row k="Entry" v={`${money(t.avg_entry, 2)}  ·  ${(t.entry_time ?? '').slice(11)}`} />
          <Row k="Exit" v={`${money(t.exit_fill, 2)}  ·  ${t.exit_time}`} />
          <Row k="Quantity" v={`${t.qty}  (${t.strike}${t.opt_type === 'C' ? 'CE' : 'PE'})`} />
          <Row k="Held" v={`${t.hold_min} minutes`} />
          <Row k="Exit reason" v={t.reason} />
          <Row k="Ladder reached" v={t.stage} />
          <Row k="NIFTY at entry" v={money(t.spot_at_entry, 2)} />
        </Card>

        <Card title="Money">
          <Row k="Gross P&L" v={signedMoney(t.gross_pnl, 2)} vColor={tone(t.gross_pnl)} />
          <Row k="Brokerage" v={money(t.brokerage, 2)} />
          <Row k="STT" v={money(t.stt, 2)} />
          <Row k="Exchange txn" v={money(t.exch_txn, 2)} />
          <Row k="SEBI" v={money(t.sebi, 2)} />
          <Row k="Stamp duty" v={money(t.stamp, 2)} />
          <Row k="GST" v={money(t.gst, 2)} />
          <Row k="Total charges" v={money(t.charges, 2)} vColor={C.warn} />
          <Row k="Net P&L" v={signedMoney(t.net_pnl, 2)} vColor={tone(t.net_pnl)} />
          <Row k="Equity after" v={money(t.equity_after, 2)} />
        </Card>

        <Card title="Risk taken">
          <Row k="Lot cost" v={money(t.lot_cost, 2)} />
          <Row k="Margin required" v={money(t.real_margin, 2)} />
          <Row k="Risk at entry" v={money(t.risk_rs, 2)} />
          <Row k="GARCH at entry" v={t.garch_vol ? `${(t.garch_vol * 100).toFixed(1)}%` : '—'} />
          <Row k="Implied vol" v={t.entry_iv ? `${(t.entry_iv * 100).toFixed(1)}%` : '—'} />
          {t.model_prem != null && (
            <Row
              k="BSM model premium"
              v={`${money(t.model_prem, 2)}  (${pct(modelGap, 1)} vs fill)`}
              vColor={modelGap != null && Math.abs(modelGap) > 10 ? C.warn : C.muted}
            />
          )}
          {t.latency_ms != null && <Row k="Avg API latency" v={`${t.latency_ms.toFixed(0)} ms`} />}
        </Card>

        {!!t.entry_reason && (
          <Card title="Why this trade was taken">
            <Text style={{ color: C.text, fontSize: 13.5, lineHeight: 21 }}>{t.entry_reason}</Text>
          </Card>
        )}

        <Button title="Close" variant="ghost" onPress={onClose} style={{ marginTop: 4 }} />
      </ScrollView>
    </Modal>
  );
}

const s = StyleSheet.create({
  trade: {
    backgroundColor: C.surface, borderWidth: 1, borderColor: C.line,
    borderRadius: 15, padding: 13, marginBottom: 9,
  },
  sym: { color: C.text, fontSize: 14.5, fontWeight: '700' },
  time: { color: C.muted, fontSize: 11, marginTop: 3, fontVariant: ['tabular-nums'] },
  pnl: { fontSize: 17, fontWeight: '800', letterSpacing: -0.3, fontVariant: ['tabular-nums'] },
  move: { fontSize: 11, fontWeight: '700', marginTop: 2, fontVariant: ['tabular-nums'] },
  tags: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 10 },
  tag: {
    backgroundColor: C.surface2, borderWidth: 1, borderColor: C.line,
    borderRadius: 7, paddingHorizontal: 8, paddingVertical: 3,
  },
  tagText: { color: C.muted, fontSize: 10, fontWeight: '700', fontVariant: ['tabular-nums'] },
  detailSym: { color: C.text, fontSize: 20, fontWeight: '700', marginTop: 5 },
  detailPnl: {
    fontSize: 30, fontWeight: '800', letterSpacing: -1, marginTop: 6,
    fontVariant: ['tabular-nums'],
  },
});
