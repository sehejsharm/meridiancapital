import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View,
} from 'react-native';

import * as api from '../api';
import {
  Button, Card, Empty, Label, Metric, MetricGrid, PnlBars,
  Segmented, Toggle,
} from '../components/ui';
import { istToday, money, pct, signedMoney } from '../format';
import { C, R, tone } from '../theme';
import type { DateRange, Format, Period, SessionRow, Summary } from '../types';

const PERIODS: { value: Period; label: string }[] = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'quarter', label: 'Qtr' },
  { value: 'year', label: 'Year' },
  { value: 'all', label: 'All' },
];

const FORMATS: { value: Format; label: string; sub: string }[] = [
  { value: 'csv', label: 'CSV', sub: 'SPREADSHEET' },
  { value: 'xlsx', label: 'XLSX', sub: 'WORKBOOK' },
  { value: 'json', label: 'JSON', sub: 'RAW DATA' },
];

/** Step the anchor date backwards or forwards by one whole period. */
function shift(anchor: string, period: Period, dir: -1 | 1): string {
  const d = new Date(anchor + 'T12:00:00');
  switch (period) {
    case 'day': d.setDate(d.getDate() + dir); break;
    case 'week': d.setDate(d.getDate() + 7 * dir); break;
    case 'month': d.setMonth(d.getMonth() + dir); break;
    case 'quarter': d.setMonth(d.getMonth() + 3 * dir); break;
    case 'year': d.setFullYear(d.getFullYear() + dir); break;
    default: return anchor;
  }
  return d.toISOString().slice(0, 10);
}

export default function ReportsScreen() {
  const [period, setPeriod] = useState<Period>('month');
  const [anchor, setAnchor] = useState(istToday());
  const [includeEvents, setIncludeEvents] = useState(false);

  const [range, setRange] = useState<DateRange | null>(null);
  const [sum, setSum] = useState<Summary | null>(null);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [downloading, setDownloading] = useState<Format | null>(null);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    setBusy(true);
    setErr('');
    try {
      const s = await api.summary(period, anchor);
      setRange(s.range);
      setSum(s.summary);
      setSessions(s.sessions);
    } catch (e: any) {
      setErr(e?.message ?? 'Could not load the report');
    } finally {
      setBusy(false);
    }
  }, [period, anchor]);

  useEffect(() => { load(); }, [load]);

  const download = async (fmt: Format) => {
    setDownloading(fmt);
    try {
      await api.downloadExport(period, anchor, fmt, {
        includeEvents,
        label: range?.label,
      });
    } catch (e: any) {
      Alert.alert('Download failed', e?.message ?? 'Unknown error');
    } finally {
      setDownloading(null);
    }
  };

  const atToday = anchor === istToday();
  const canStep = period !== 'all';

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: C.bg }}
      contentContainerStyle={{ padding: 16, paddingBottom: 30 }}
      refreshControl={<RefreshControl refreshing={busy} onRefresh={load} tintColor={C.muted} />}
    >
      <Segmented options={PERIODS} value={period} onChange={setPeriod} />

      {/* ---------------- window picker ---------------- */}
      <Card>
        <View style={s.picker}>
          <Pressable
            onPress={() => canStep && setAnchor(shift(anchor, period, -1))}
            disabled={!canStep}
            style={({ pressed }) => [s.step, (!canStep || pressed) && { opacity: 0.4 }]}
          >
            <Text style={s.stepText}>‹</Text>
          </Pressable>

          <View style={{ flex: 1, alignItems: 'center' }}>
            <Text style={s.rangeLabel}>{range?.label ?? '—'}</Text>
            <Text style={s.rangeDates}>
              {range ? `${range.start}  →  ${range.end}` : ''}
            </Text>
          </View>

          <Pressable
            onPress={() => canStep && setAnchor(shift(anchor, period, 1))}
            disabled={!canStep}
            style={({ pressed }) => [s.step, (!canStep || pressed) && { opacity: 0.4 }]}
          >
            <Text style={s.stepText}>›</Text>
          </Pressable>
        </View>
        {!atToday && canStep && (
          <Button
            title="Jump to today"
            variant="ghost"
            style={{ marginTop: 11, paddingVertical: 10 }}
            onPress={() => setAnchor(istToday())}
          />
        )}
      </Card>

      {!!err && <Card><Text style={{ color: C.down, fontSize: 13 }}>{err}</Text></Card>}

      {/* ---------------- performance ---------------- */}
      {sum && (
        <Card title="Performance">
          <MetricGrid>
            <Metric
              k="Net P&L" v={signedMoney(sum.net_pnl)} vColor={tone(sum.net_pnl)}
              sub={`${sum.trades} trades · ${sum.sessions} sessions`}
            />
            <Metric
              k="Return" v={pct(sum.return_pct)} vColor={tone(sum.return_pct)}
              sub={`${money(sum.open_equity)} → ${money(sum.close_equity)}`}
            />
            <Metric
              k="Win rate" v={`${sum.win_rate.toFixed(0)}%`}
              sub={`${sum.wins}W / ${sum.losses}L`}
              barPct={sum.win_rate} barColor={sum.win_rate >= 50 ? C.up : C.warn}
            />
            <Metric
              k="Profit factor"
              v={sum.profit_factor != null ? sum.profit_factor.toFixed(2) : '—'}
              sub="gross win / gross loss"
            />
            <Metric
              k="Max drawdown" v={pct(sum.max_drawdown_pct)}
              vColor={sum.max_drawdown_pct < -10 ? C.down : C.muted}
              sub={`peak ${money(sum.peak_equity)}`}
            />
            <Metric
              k="Charges" v={money(sum.charges)}
              sub={`avg trade ${signedMoney(sum.avg_trade)}`}
            />
            <Metric
              k="Best trade" v={signedMoney(sum.best_trade)} vColor={C.up}
              sub={`worst ${signedMoney(sum.worst_trade)}`}
            />
            <Metric
              k="Sat out" v={`${sum.days_blocked_chop} days`}
              sub={`${sum.days_killed} kill switch`}
            />
          </MetricGrid>

          {sessions.length > 1 && (
            <View style={{ marginTop: 14 }}>
              <Label>Daily P&amp;L</Label>
              <View style={{ marginTop: 8 }}>
                <PnlBars values={sessions.map((x) => x.day_pnl ?? 0)} />
              </View>
            </View>
          )}
        </Card>
      )}

      {/* ---------------- downloads ---------------- */}
      <Card title="Download trade results">
        <View style={{ flexDirection: 'row', gap: 9 }}>
          {FORMATS.map((f) => (
            <Pressable
              key={f.value}
              onPress={() => download(f.value)}
              disabled={downloading !== null}
              style={({ pressed }) => [
                s.dl,
                pressed && { backgroundColor: C.raised },
                downloading === f.value && { borderColor: C.accent },
                downloading !== null && downloading !== f.value && { opacity: 0.4 },
              ]}
            >
              <Text style={s.dlExt}>{downloading === f.value ? '···' : f.label}</Text>
              <Text style={s.dlSub}>{f.sub}</Text>
            </Pressable>
          ))}
        </View>

        <Pressable style={s.switchRow} onPress={() => setIncludeEvents((v) => !v)}>
          <View style={{ flex: 1 }}>
            <Text style={{ color: C.text, fontSize: 13.5 }}>Include full event log</Text>
            <Text style={{ color: C.dim, fontSize: 11.5, marginTop: 2 }}>
              Every decision and state change, not just the trades
            </Text>
          </View>
          <Toggle on={includeEvents} onPress={() => setIncludeEvents((v) => !v)} />
        </Pressable>

        <Text style={s.dlHint}>
          Files open in the system share sheet — save to Files, mail them, or send to Drive.
        </Text>
      </Card>

      {/* ---------------- sessions ---------------- */}
      <Card title="Sessions in range">
        {sessions.length === 0 ? (
          <Empty text="No sessions recorded in this window." />
        ) : (
          [...sessions].reverse().map((d) => (
            <View key={d.session_date} style={s.day}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={s.dayDate}>{d.session_date}</Text>
                <Text style={s.dayMeta} numberOfLines={1}>
                  {d.trades} trades
                  {d.chop_blocked ? ' · chop blocked' : ''}
                  {d.killed ? ' · kill switch' : ''}
                  {d.vol_regime ? ` · ${d.vol_regime}` : ''}
                </Text>
              </View>
              <Text style={[s.dayPnl, { color: tone(d.day_pnl) }]}>
                {signedMoney(d.day_pnl)}
              </Text>
            </View>
          ))
        )}
      </Card>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  picker: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  step: {
    width: 40, height: 40, borderRadius: R.md, backgroundColor: C.surface2,
    borderWidth: 1, borderColor: C.line, alignItems: 'center', justifyContent: 'center',
  },
  stepText: { color: C.text, fontSize: 22, lineHeight: 26, marginTop: -3 },
  rangeLabel: { color: C.text, fontSize: 16, fontWeight: '700', letterSpacing: -0.2 },
  rangeDates: {
    color: C.muted, fontSize: 11.5, marginTop: 3, fontVariant: ['tabular-nums'],
  },
  dl: {
    flex: 1, alignItems: 'center', paddingVertical: 14, borderRadius: R.md,
    backgroundColor: C.surface2, borderWidth: 1, borderColor: C.line,
  },
  dlExt: { color: C.text, fontSize: 13.5, fontWeight: '800', letterSpacing: 0.3 },
  dlSub: { color: C.dim, fontSize: 9.5, marginTop: 3, letterSpacing: 0.4 },
  switchRow: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    marginTop: 14, paddingTop: 13,
    borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: C.line,
  },
  dlHint: { color: C.dim, fontSize: 11, lineHeight: 17, marginTop: 11 },
  day: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    gap: 10, paddingVertical: 11,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: C.line,
  },
  dayDate: { color: C.text, fontSize: 13.5, fontWeight: '600', fontVariant: ['tabular-nums'] },
  dayMeta: { color: C.muted, fontSize: 11, marginTop: 2 },
  dayPnl: { fontSize: 14.5, fontWeight: '700', fontVariant: ['tabular-nums'] },
});
