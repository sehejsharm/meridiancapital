import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Modal, Pressable, RefreshControl, ScrollView, StyleSheet,
  Text, TextInput, View,
} from 'react-native';

import * as api from '../api';
import { Button, Card, Empty, Label, Row, Toggle } from '../components/ui';
import { prettyDateTime } from '../format';
import { useStore } from '../store';
import { C, R } from '../theme';
import type { StrategyDescription, StrategyParam } from '../types';

export default function StrategyScreen() {
  const { status, refresh } = useStore();
  const [desc, setDesc] = useState<StrategyDescription | null>(null);
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [profileName, setProfileName] = useState('');
  const [showProfiles, setShowProfiles] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setDesc(await api.getStrategy());
      setEdits({});
    } catch (e: any) {
      Alert.alert('Could not load the strategy', e?.message ?? '');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const dirty = Object.keys(edits).length > 0;

  const valueOf = useCallback(
    (p: StrategyParam) => (p.key in edits ? edits[p.key] : p.value),
    [edits],
  );

  const setValue = (key: string, value: any) =>
    setEdits((prev) => ({ ...prev, [key]: value }));

  const save = async () => {
    setSaving(true);
    try {
      const next = await api.putStrategy(edits);
      setDesc(next);
      setEdits({});
      refresh();
      Alert.alert(
        'Saved',
        status?.running
          ? 'Applies the next time the bot starts. The running bot keeps the rules its open position was taken under.'
          : 'Applies on the next start.',
      );
    } catch (e: any) {
      Alert.alert('Rejected', e?.message ?? 'Invalid parameters');
    } finally {
      setSaving(false);
    }
  };

  const resetAll = () =>
    Alert.alert('Reset to v11?', 'Every parameter returns to the original values.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Reset',
        style: 'destructive',
        onPress: async () => {
          try {
            setDesc(await api.resetStrategy());
            setEdits({});
          } catch (e: any) {
            Alert.alert('Failed', e?.message ?? '');
          }
        },
      },
    ]);

  const driftCount = useMemo(
    () => (desc ? Object.keys(desc.drift).length : 0),
    [desc],
  );

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: C.bg }}
      contentContainerStyle={{ padding: 16, paddingBottom: 40 }}
      refreshControl={<RefreshControl refreshing={busy} onRefresh={load} tintColor={C.muted} />}
    >
      <Card>
        <Text style={s.intro}>
          These are the numbers the algorithm trades on. Edits are validated against
          the rules that must hold — a ladder floor above its own trigger is refused,
          not saved.
        </Text>
        <Text style={[s.intro, { marginTop: 8, color: C.warn }]}>
          {status?.running
            ? 'The bot is running. Changes take effect on its next start, never mid-position.'
            : 'Changes take effect the next time the bot starts.'}
        </Text>
      </Card>

      {driftCount > 0 && (
        <Card title="Changed from v11">
          {Object.entries(desc!.drift).map(([key, d]) => (
            <Row
              key={key}
              k={d.label}
              v={`${fmt(d.v11)} → ${fmt(d.current)}`}
              vColor={d.critical ? C.warn : C.info}
            />
          ))}
          <Button
            title="Reset everything to v11"
            variant="ghost"
            style={{ marginTop: 11 }}
            onPress={resetAll}
          />
        </Card>
      )}

      {!!desc?.problems?.length && (
        <Card>
          {desc.problems.map((p, i) => (
            <Text key={i} style={s.problem}>{p}</Text>
          ))}
        </Card>
      )}

      {desc?.groups.map((g) => (
        <Card key={g.name} title={g.name}>
          {g.params.map((p, i) => (
            <ParamRow
              key={p.key}
              p={p}
              value={valueOf(p)}
              edited={p.key in edits}
              last={i === g.params.length - 1}
              onChange={(v) => setValue(p.key, v)}
            />
          ))}
        </Card>
      ))}

      <Card title="Profiles">
        <Text style={s.intro}>
          Save a set of parameters and switch back to it later.
        </Text>
        <View style={{ flexDirection: 'row', gap: 9, marginTop: 11 }}>
          <TextInput
            style={s.field}
            placeholder="Profile name"
            placeholderTextColor={C.dim}
            value={profileName}
            onChangeText={setProfileName}
            autoCapitalize="none"
          />
          <Button
            title="Save"
            variant="ghost"
            style={{ paddingHorizontal: 22 }}
            disabled={!profileName.trim()}
            onPress={async () => {
              try {
                setDesc(await api.saveProfile(profileName));
                setProfileName('');
              } catch (e: any) {
                Alert.alert('Failed', e?.message ?? '');
              }
            }}
          />
        </View>
        {desc?.profiles?.length ? (
          <Button
            title={`Manage ${desc.profiles.length} saved profile${desc.profiles.length > 1 ? 's' : ''}`}
            variant="ghost"
            style={{ marginTop: 11 }}
            onPress={() => setShowProfiles(true)}
          />
        ) : (
          <Empty text="No profiles saved yet." />
        )}
      </Card>

      <Card title="Changing the algorithm itself">
        <Text style={s.intro}>
          These controls change the algorithm's inputs. To change its logic — a new
          signal rule, a different indicator — edit{' '}
          <Text style={s.code}>backend/app/bot/strategy.py</Text> and redeploy. The
          supervisor, dashboard, reports and exports keep working unchanged as long
          as it still prints what it prints today.
        </Text>
      </Card>

      {dirty && (
        <View style={s.saveBar}>
          <Button
            title={`Discard`}
            variant="ghost"
            style={{ flex: 1 }}
            onPress={() => setEdits({})}
          />
          <Button
            title={`Save ${Object.keys(edits).length} change${Object.keys(edits).length > 1 ? 's' : ''}`}
            style={{ flex: 2 }}
            loading={saving}
            onPress={save}
          />
        </View>
      )}

      <ProfileSheet
        visible={showProfiles}
        desc={desc}
        onClose={() => setShowProfiles(false)}
        onChanged={setDesc}
      />
    </ScrollView>
  );
}

function ParamRow({
  p, value, edited, last, onChange,
}: {
  p: StrategyParam; value: any; edited: boolean; last: boolean;
  onChange: (v: any) => void;
}) {
  return (
    <View style={[s.param, last && { borderBottomWidth: 0 }]}>
      <View style={{ flex: 1, minWidth: 0, paddingRight: 10 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <Text style={s.pLabel}>{p.label}</Text>
          {edited && <View style={s.dot} />}
          {p.critical && <Text style={s.critical}>INSTRUMENT</Text>}
        </View>
        {!!p.help && <Text style={s.pHelp}>{p.help}</Text>}
        {p.modified && !edited && (
          <Text style={s.pDefault}>v11 default: {fmt(p.default)}</Text>
        )}
      </View>
      <Control p={p} value={value} onChange={onChange} />
    </View>
  );
}

function Control({
  p, value, onChange,
}: { p: StrategyParam; value: any; onChange: (v: any) => void }) {
  if (p.type === 'bool') {
    return <Toggle on={!!value} onPress={() => onChange(!value)} />;
  }

  if (p.type === 'text') {
    return (
      <TextInput
        style={s.textField}
        value={String(value ?? '')}
        onChangeText={onChange}
        autoCapitalize="characters"
        autoCorrect={false}
      />
    );
  }

  if (p.type === 'time') {
    const [h, m] = String(value ?? '00:00').split(':').map((x) => parseInt(x, 10) || 0);
    const bump = (unit: 'h' | 'm', dir: 1 | -1) => {
      const nh = unit === 'h' ? (h + dir + 24) % 24 : h;
      const nm = unit === 'm' ? (m + dir * 5 + 60) % 60 : m;
      onChange(`${String(nh).padStart(2, '0')}:${String(nm).padStart(2, '0')}`);
    };
    return (
      <View style={s.stepRow}>
        <Step label="−" onPress={() => bump('h', -1)} />
        <Text style={s.stepVal}>{String(h).padStart(2, '0')}</Text>
        <Step label="+" onPress={() => bump('h', 1)} />
        <Text style={{ color: C.dim }}>:</Text>
        <Step label="−" onPress={() => bump('m', -1)} />
        <Text style={s.stepVal}>{String(m).padStart(2, '0')}</Text>
        <Step label="+" onPress={() => bump('m', 1)} />
      </View>
    );
  }

  // Numeric: percentages are edited in whole percent, which is how the
  // strategy is actually discussed, and stored as the fraction the code uses.
  const isPct = p.type === 'pct';
  const step = p.step ?? (isPct ? 0.01 : 1);
  const shown = isPct ? (Number(value) * 100).toFixed(1) : String(value);
  const suffix = isPct ? '%' : p.type === 'rupees' ? '₹' : '';

  const bump = (dir: 1 | -1) => {
    const next = Number(value) + dir * step;
    const clamped = Math.min(
      p.max ?? Number.MAX_SAFE_INTEGER,
      Math.max(p.min ?? -Number.MAX_SAFE_INTEGER, next),
    );
    onChange(p.type === 'int' ? Math.round(clamped) : Number(clamped.toFixed(6)));
  };

  return (
    <View style={s.stepRow}>
      <Step label="−" onPress={() => bump(-1)} />
      <View style={s.numBox}>
        <Text style={s.numText} numberOfLines={1}>
          {suffix === '₹' ? '₹' : ''}{isPct ? shown : Number(value).toLocaleString('en-IN')}
          {suffix === '%' ? '%' : ''}
        </Text>
      </View>
      <Step label="+" onPress={() => bump(1)} />
    </View>
  );
}

const Step = ({ label, onPress }: { label: string; onPress: () => void }) => (
  <Pressable onPress={onPress} style={({ pressed }) => [s.step, pressed && { opacity: 0.5 }]}>
    <Text style={s.stepText}>{label}</Text>
  </Pressable>
);

function ProfileSheet({
  visible, desc, onClose, onChanged,
}: {
  visible: boolean;
  desc: StrategyDescription | null;
  onClose: () => void;
  onChanged: (d: StrategyDescription) => void;
}) {
  if (!visible || !desc) return null;
  return (
    <Modal visible animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <ScrollView style={{ flex: 1, backgroundColor: C.bg }} contentContainerStyle={{ padding: 16 }}>
        <Label>Saved profiles</Label>
        {desc.profiles.map((p) => (
          <Card key={p.name}>
            <Text style={s.profName}>{p.name}</Text>
            <Text style={s.profMeta}>
              {p.changes} change{p.changes === 1 ? '' : 's'} from v11 · saved{' '}
              {prettyDateTime(p.saved_at)}
            </Text>
            <View style={{ flexDirection: 'row', gap: 9, marginTop: 11 }}>
              <Button
                title="Load" style={{ flex: 1 }}
                onPress={async () => {
                  try {
                    onChanged(await api.loadProfile(p.name));
                    onClose();
                  } catch (e: any) {
                    Alert.alert('Failed', e?.message ?? '');
                  }
                }}
              />
              <Button
                title="Delete" variant="danger" style={{ flex: 1 }}
                onPress={async () => {
                  try {
                    onChanged(await api.deleteProfile(p.name));
                  } catch (e: any) {
                    Alert.alert('Failed', e?.message ?? '');
                  }
                }}
              />
            </View>
          </Card>
        ))}
        <Button title="Close" variant="ghost" onPress={onClose} />
      </ScrollView>
    </Modal>
  );
}

const fmt = (v: any) =>
  typeof v === 'boolean' ? (v ? 'on' : 'off')
    : typeof v === 'number' && v > 0 && v < 1 ? `${(v * 100).toFixed(1)}%`
    : String(v);

const s = StyleSheet.create({
  intro: { color: C.muted, fontSize: 12.5, lineHeight: 19 },
  code: { color: C.text, fontFamily: 'monospace', fontSize: 11.5 },
  problem: { color: C.down, fontSize: 12.5, lineHeight: 19, marginBottom: 6 },
  param: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingVertical: 11, gap: 8,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: C.line,
  },
  pLabel: { color: C.text, fontSize: 13.5, fontWeight: '600' },
  pHelp: { color: C.dim, fontSize: 11, lineHeight: 16, marginTop: 3 },
  pDefault: { color: C.info, fontSize: 10.5, marginTop: 3 },
  dot: { width: 6, height: 6, borderRadius: 99, backgroundColor: C.accent },
  critical: {
    color: C.warn, fontSize: 8.5, fontWeight: '800', letterSpacing: 0.5,
    backgroundColor: C.warnBg, paddingHorizontal: 5, paddingVertical: 1.5,
    borderRadius: 4, overflow: 'hidden',
  },
  stepRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  step: {
    width: 27, height: 27, borderRadius: 8, backgroundColor: C.surface2,
    borderWidth: 1, borderColor: C.line, alignItems: 'center', justifyContent: 'center',
  },
  stepText: { color: C.text, fontSize: 15, lineHeight: 18, fontWeight: '700' },
  stepVal: {
    color: C.text, fontSize: 15, fontWeight: '700', width: 26,
    textAlign: 'center', fontVariant: ['tabular-nums'],
  },
  numBox: { minWidth: 64, alignItems: 'center' },
  numText: {
    color: C.text, fontSize: 14, fontWeight: '700',
    fontVariant: ['tabular-nums'],
  },
  textField: {
    backgroundColor: C.surface2, borderWidth: 1, borderColor: C.line,
    borderRadius: R.sm, paddingHorizontal: 11, paddingVertical: 8,
    color: C.text, fontSize: 13, minWidth: 118, textAlign: 'right',
  },
  field: {
    flex: 1, backgroundColor: C.surface2, borderWidth: 1, borderColor: C.line,
    borderRadius: R.md, paddingHorizontal: 13, paddingVertical: 12,
    color: C.text, fontSize: 14,
  },
  saveBar: { flexDirection: 'row', gap: 9, marginTop: 4 },
  profName: { color: C.text, fontSize: 15, fontWeight: '700' },
  profMeta: { color: C.muted, fontSize: 11.5, marginTop: 3 },
});
