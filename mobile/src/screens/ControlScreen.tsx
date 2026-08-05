import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Modal, Pressable, RefreshControl, SafeAreaView, ScrollView,
  StyleSheet, Text, View,
} from 'react-native';

import * as api from '../api';
import * as bio from '../biometrics';
import { Button, Card, Row, Toggle } from '../components/ui';
import { duration, prettyDateTime } from '../format';
import StrategyScreen from './StrategyScreen';
import { useStore } from '../store';
import { C, R } from '../theme';

/** Steppable HH:MM field — avoids a native picker dependency. */
function TimeField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [h, m] = value.split(':').map((x) => parseInt(x, 10) || 0);

  const bump = (unit: 'h' | 'm', dir: 1 | -1) => {
    let nh = h;
    let nm = m;
    if (unit === 'h') nh = (h + dir + 24) % 24;
    else nm = (m + dir * 5 + 60) % 60;
    onChange(`${String(nh).padStart(2, '0')}:${String(nm).padStart(2, '0')}`);
  };

  return (
    <View style={s.timeField}>
      <Stepper onPress={() => bump('h', -1)} label="−" />
      <Text style={s.timeText}>{String(h).padStart(2, '0')}</Text>
      <Stepper onPress={() => bump('h', 1)} label="+" />
      <Text style={s.timeColon}>:</Text>
      <Stepper onPress={() => bump('m', -1)} label="−" />
      <Text style={s.timeText}>{String(m).padStart(2, '0')}</Text>
      <Stepper onPress={() => bump('m', 1)} label="+" />
    </View>
  );
}

const Stepper = ({ onPress, label }: { onPress: () => void; label: string }) => (
  <Pressable onPress={onPress} style={({ pressed }) => [s.stepper, pressed && { opacity: 0.5 }]}>
    <Text style={s.stepperText}>{label}</Text>
  </Pressable>
);

export default function ControlScreen() {
  const {
    status, refresh, disconnect, baseUrl, connection, user,
    canUnlock, enableBiometrics, disableBiometrics,
  } = useStore();
  const [bioCap, setBioCap] = useState<{ available: boolean; enrolled: boolean; label: string }>(
    { available: false, enrolled: false, label: 'Biometrics' },
  );

  useEffect(() => { bio.capability().then(setBioCap); }, []);
  const [sched, setSched] = useState<api.ScheduleInfo | null>(null);
  const [start, setStart] = useState('09:15');
  const [stop, setStop] = useState('15:45');
  const [busy, setBusy] = useState<string | null>(null);
  const [showStrategy, setShowStrategy] = useState(false);

  const loadSchedule = useCallback(async () => {
    try {
      const sc = await api.getSchedule();
      setSched(sc);
      setStart(sc.start);
      setStop(sc.stop);
    } catch {
      /* the status card already reports connectivity trouble */
    }
  }, []);

  useEffect(() => { loadSchedule(); }, [loadSchedule]);

  const onStart = async () => {
    setBusy('start');
    try {
      const r = await api.startBot(false);
      if (!r.ok) {
        Alert.alert('Not started', r.reason ?? 'The server refused the request.', [
          { text: 'Cancel', style: 'cancel' },
          {
            text: 'Start anyway',
            style: 'destructive',
            onPress: async () => {
              try {
                await api.startBot(true);
                refresh();
              } catch (e: any) {
                Alert.alert('Failed', e?.message ?? '');
              }
            },
          },
        ]);
      }
    } catch (e: any) {
      Alert.alert('Failed to start', e?.message ?? '');
    } finally {
      setBusy(null);
      refresh();
    }
  };

  const onStop = () => {
    Alert.alert(
      'Stop the bot?',
      'Any open position is flattened at market first, then the end-of-day report is filed.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Stop',
          style: 'destructive',
          onPress: async () => {
            setBusy('stop');
            try {
              await api.stopBot('manual stop from app');
            } catch (e: any) {
              Alert.alert('Failed to stop', e?.message ?? '');
            } finally {
              setBusy(null);
              refresh();
            }
          },
        },
      ],
    );
  };

  const onSaveSchedule = async () => {
    setBusy('save');
    try {
      const sc = await api.setSchedule({ start, stop });
      setSched(sc);
      Alert.alert('Saved', `The bot now runs ${sc.start} to ${sc.stop} ${sc.timezone}.`);
    } catch (e: any) {
      Alert.alert('Could not save', e?.message ?? '');
    } finally {
      setBusy(null);
    }
  };

  const toggleAuto = async () => {
    if (!sched) return;
    try {
      const sc = await api.setSchedule({ enabled: !sched.auto });
      setSched(sc);
    } catch (e: any) {
      Alert.alert('Failed', e?.message ?? '');
    }
  };

  const st = status;
  const cfg = st?.config;

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: C.bg }}
      contentContainerStyle={{ padding: 16, paddingBottom: 30 }}
      refreshControl={
        <RefreshControl refreshing={false} onRefresh={() => { refresh(); loadSchedule(); }}
          tintColor={C.muted} />
      }
    >
      {cfg && !cfg.paper_mode && (
        <View style={s.liveWarn}>
          <Text style={s.liveWarnText}>
            LIVE MONEY — orders placed by this bot hit the exchange for real.
          </Text>
        </View>
      )}

      <Card title="Session control">
        <View style={{ flexDirection: 'row', gap: 9 }}>
          <Button
            title="Start now" style={{ flex: 1 }} onPress={onStart}
            loading={busy === 'start'} disabled={st?.running}
          />
          <Button
            title="Stop now" variant="danger" style={{ flex: 1 }} onPress={onStop}
            loading={busy === 'stop'} disabled={!st?.running}
          />
        </View>
        <Text style={s.hint}>
          Stopping flattens any open position, files the end-of-day report, then exits.
          The algorithm also closes itself at 15:00 and reports at 15:30 regardless.
        </Text>
      </Card>

      <Card title="Automatic schedule">
        <Pressable style={s.switchRow} onPress={toggleAuto}>
          <View style={{ flex: 1 }}>
            <Text style={{ color: C.text, fontSize: 13.5 }}>Run every trading day</Text>
            <Text style={{ color: C.dim, fontSize: 11.5, marginTop: 2 }}>
              Weekdays only, NSE holidays skipped
            </Text>
          </View>
          <Toggle on={!!sched?.auto} onPress={toggleAuto} />
        </Pressable>

        <View style={s.timeRow}>
          <Text style={s.timeLabel}>Start</Text>
          <TimeField value={start} onChange={setStart} />
        </View>
        <View style={s.timeRow}>
          <Text style={s.timeLabel}>Stop</Text>
          <TimeField value={stop} onChange={setStop} />
        </View>

        {(start !== sched?.start || stop !== sched?.stop) && (
          <Button
            title="Save schedule" variant="ghost" onPress={onSaveSchedule}
            loading={busy === 'save'} style={{ marginTop: 11 }}
          />
        )}

        <View style={{ marginTop: 4 }}>
          <Row k="Next start" v={prettyDateTime(sched?.next_start)} />
          <Row k="Next stop" v={prettyDateTime(sched?.next_stop)} />
          <Row k="Timezone" v={sched?.timezone ?? '—'} />
        </View>
      </Card>

      <Card title="Strategy">
        <Text style={s.hint}>
          Stop loss, the risk ladder, volatility gates, filters, session times and
          the instrument itself — all editable, all validated. Changes apply the
          next time the bot starts, never mid-position.
        </Text>
        <Button
          title="Edit strategy parameters"
          variant="ghost"
          style={{ marginTop: 12 }}
          onPress={() => setShowStrategy(true)}
        />
      </Card>

      <Modal
        visible={showStrategy}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setShowStrategy(false)}
      >
        <SafeAreaView style={{ flex: 1, backgroundColor: C.bg }}>
          <View style={s.sheetHead}>
            <Text style={s.sheetTitle}>Strategy</Text>
            <Pressable onPress={() => setShowStrategy(false)} hitSlop={12}>
              <Text style={s.sheetClose}>Done</Text>
            </Pressable>
          </View>
          <StrategyScreen />
        </SafeAreaView>
      </Modal>

      <Card title="System">
        <Row k="Mode" v={cfg?.paper_mode ? 'Paper trading' : 'LIVE — real money'}
             vColor={cfg?.paper_mode ? C.info : C.down} />
        <Row k="Broker account" v={cfg?.client_id ?? '—'} />
        <Row k="Credentials" v={cfg?.credentials_present ? 'Configured' : 'MISSING'}
             vColor={cfg?.credentials_present ? C.up : C.down} />
        <Row k="Connection" v={connection} vColor={connection === 'live' ? C.up : C.warn} />
        <Row k="Server" v={baseUrl.replace(/^https?:\/\//, '')} />
        <Row k="Server time" v={(st?.server_time ?? '').replace('T', '  ')} />
        <Row k="Trading day"
             v={st?.is_trading_day ? 'Yes' : `No — ${st?.not_trading_reason ?? ''}`}
             vColor={st?.is_trading_day ? C.up : C.muted} />
        <Row
          k="Process"
          v={st?.running
            ? `pid ${st.pid} · up ${duration(st.uptime_seconds)}`
            : (st?.state ?? '—')}
          vColor={st?.running ? C.up : C.muted}
        />
        <Row k="Restarts today" v={String(st?.restarts ?? 0)}
             vColor={(st?.restarts ?? 0) > 0 ? C.warn : C.muted} />
        {!!st?.last_error && <Row k="Last error" v={st.last_error} vColor={C.down} />}
        <Row k="Seed capital" v={cfg ? `₹${cfg.seed_capital.toLocaleString('en-IN')}` : '—'} />
        <Row k="Slippage model"
             v={cfg ? `${(cfg.paper_slippage_pct * 100).toFixed(2)}% per side` : '—'} />
      </Card>

      <Card title="Notifications">
        <Row k="Push configured" v={cfg?.push_enabled ? 'Yes' : 'No device registered'}
             vColor={cfg?.push_enabled ? C.up : C.muted} />
        <Button
          title="Send a test notification"
          variant="ghost"
          style={{ marginTop: 11 }}
          loading={busy === 'push'}
          onPress={async () => {
            setBusy('push');
            try {
              const r = await api.testPush();
              Alert.alert(
                'Sent',
                r.tokens > 0
                  ? `Pushed to ${r.tokens} device${r.tokens > 1 ? 's' : ''}.`
                  : 'No devices are registered yet. Notifications need a real device, not a simulator.',
              );
            } catch (e: any) {
              Alert.alert('Failed', e?.message ?? '');
            } finally {
              setBusy(null);
            }
          }}
        />
      </Card>

      <Card title="Security">
        <Row k="Operator" v={user || '—'} />
        <Row k="Server" v={baseUrl.replace(/^https?:\/\//, '')} />
        <Row
          k={`${bioCap.label} unlock`}
          v={
            !bioCap.available ? 'No sensor on this device'
              : !bioCap.enrolled ? `Set up ${bioCap.label} in system settings first`
              : canUnlock ? 'Enabled' : 'Off'
          }
          vColor={canUnlock ? C.up : C.muted}
        />
        {bioCap.available && bioCap.enrolled && (
          <Pressable
            style={s.switchRow}
            onPress={async () => {
              if (canUnlock) {
                await disableBiometrics();
              } else if (!(await enableBiometrics())) {
                Alert.alert('Not enabled', `${bioCap.label} did not verify.`);
              }
            }}
          >
            <View style={{ flex: 1 }}>
              <Text style={{ color: C.text, fontSize: 13.5 }}>
                Require {bioCap.label} to open
              </Text>
              <Text style={{ color: C.dim, fontSize: 11.5, marginTop: 2, lineHeight: 17 }}>
                Locks this device. Your passcode is what the server verifies.
              </Text>
            </View>
            <Toggle
              on={canUnlock}
              onPress={async () => {
                if (canUnlock) await disableBiometrics();
                else await enableBiometrics();
              }}
            />
          </Pressable>
        )}
        <Button
          title="Sign out everywhere"
          variant="ghost"
          style={{ marginTop: 12 }}
          onPress={() =>
            Alert.alert(
              'Sign out everywhere?',
              'Every device signed in with this account stops working immediately.',
              [
                { text: 'Cancel', style: 'cancel' },
                {
                  text: 'Sign out all',
                  style: 'destructive',
                  onPress: async () => {
                    try { await api.logoutEverywhere(); } catch { /* going anyway */ }
                    disconnect();
                  },
                },
              ],
            )
          }
        />
      </Card>

      <Button
        title="Sign out"
        variant="danger"
        onPress={() =>
          Alert.alert('Sign out?', 'The saved session is removed from this device.', [
            { text: 'Cancel', style: 'cancel' },
            { text: 'Sign out', style: 'destructive', onPress: () => disconnect() },
          ])
        }
      />
    </ScrollView>
  );
}

const s = StyleSheet.create({
  liveWarn: {
    backgroundColor: C.downBg, borderWidth: 1, borderColor: 'rgba(255,90,110,0.35)',
    borderRadius: R.md, padding: 13, marginBottom: 11,
  },
  liveWarnText: { color: C.down, fontSize: 12.5, fontWeight: '700', lineHeight: 18 },
  hint: { color: C.dim, fontSize: 11.5, lineHeight: 18, marginTop: 12 },
  sheetHead: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 13,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: C.line,
  },
  sheetTitle: { color: C.text, fontSize: 17, fontWeight: '700', letterSpacing: -0.2 },
  sheetClose: { color: C.accent, fontSize: 15, fontWeight: '700' },
  switchRow: {
    flexDirection: 'row', alignItems: 'center', gap: 12, paddingBottom: 13,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: C.line,
  },
  timeRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingVertical: 11,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: C.line,
  },
  timeLabel: { color: C.muted, fontSize: 13.5 },
  timeField: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  timeText: {
    color: C.text, fontSize: 17, fontWeight: '700', width: 30, textAlign: 'center',
    fontVariant: ['tabular-nums'],
  },
  timeColon: { color: C.dim, fontSize: 17, marginHorizontal: 2 },
  stepper: {
    width: 27, height: 27, borderRadius: 8, backgroundColor: C.surface2,
    borderWidth: 1, borderColor: C.line, alignItems: 'center', justifyContent: 'center',
  },
  stepperText: { color: C.text, fontSize: 15, lineHeight: 18, fontWeight: '700' },
});
