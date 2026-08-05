import React, { useCallback, useEffect, useState } from 'react';
import {
  KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet,
  Text, TextInput, View,
} from 'react-native';

import * as bio from '../biometrics';
import { Button } from '../components/ui';
import { Mark } from '../components/Mark';
import { useStore } from '../store';
import { C, MONO, R } from '../theme';

export default function ConnectScreen() {
  const { login, unlockWithBiometrics, savedServer, savedUser, canUnlock } = useStore();

  const [url, setUrl] = useState(savedServer);
  const [user, setUser] = useState(savedUser || 'Sehej');
  const [pass, setPass] = useState('');
  const [busy, setBusy] = useState(false);
  const [unlocking, setUnlocking] = useState(false);
  const [err, setErr] = useState('');
  const [bioLabel, setBioLabel] = useState('Biometrics');
  const [showToken, setShowToken] = useState(false);
  const [token, setToken] = useState('');

  useEffect(() => {
    bio.capability().then((c) => setBioLabel(c.label));
  }, []);

  useEffect(() => { setUrl(savedServer); }, [savedServer]);

  const onUnlock = useCallback(async () => {
    setUnlocking(true);
    setErr('');
    try {
      const ok = await unlockWithBiometrics();
      if (!ok) setErr('Not recognised. Sign in with your passcode.');
    } catch (e: any) {
      setErr(e?.message ?? 'Could not unlock');
    } finally {
      setUnlocking(false);
    }
  }, [unlockWithBiometrics]);

  // Offer the unlock immediately — the common case is opening the app to check.
  useEffect(() => {
    if (canUnlock) onUnlock();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canUnlock]);

  const onLogin = async () => {
    if (!url.trim()) { setErr('Enter the server address.'); return; }
    if (!user.trim() || !pass) { setErr('Operator and passcode are required.'); return; }
    setBusy(true);
    setErr('');
    try {
      await login(url, user, pass);
      setPass('');
    } catch (e: any) {
      setErr(e?.message ?? 'Could not sign in');
    } finally {
      setBusy(false);
    }
  };

  const onToken = async () => {
    if (!url.trim() || !token.trim()) { setErr('Server and token are required.'); return; }
    setBusy(true);
    setErr('');
    try {
      await login(url, '', '', token);
      setToken('');
    } catch (e: any) {
      setErr(e?.message ?? 'Token rejected');
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: C.bg }}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView contentContainerStyle={s.wrap} keyboardShouldPersistTaps="handled">
        <Mark size={104} />

        <Text style={s.wordmark}>Meridian Capital</Text>
        <Text style={s.tagline}>Autonomous Execution</Text>
        <View style={s.rule} />

        {canUnlock && (
          <>
            <Button
              title={unlocking ? 'Verifying…' : `Unlock with ${bioLabel}`}
              onPress={onUnlock}
              loading={unlocking}
              style={{ width: '100%' }}
            />
            <View style={s.orRow}>
              <View style={s.orLine} />
              <Text style={s.orText}>OR</Text>
              <View style={s.orLine} />
            </View>
          </>
        )}

        <View style={s.form}>
          <Field label="Server" value={url} onChange={setUrl}
                 placeholder="https://xxx.ts.net" keyboardType="url" />
          <Field label="Operator" value={user} onChange={setUser} placeholder="Sehej" />
          <Field label="Passcode" value={pass} onChange={setPass}
                 placeholder="••••••••" secure onSubmit={onLogin} />
          <Button title="Authenticate" onPress={onLogin} loading={busy}
                  style={{ marginTop: 4 }} />
          {!!err && <Text style={s.err}>{err}</Text>}
        </View>

        <Pressable onPress={() => setShowToken((v) => !v)} hitSlop={10}>
          <Text style={s.altLink}>
            {showToken ? 'Hide token sign-in' : 'Use an API token instead'}
          </Text>
        </Pressable>

        {showToken && (
          <View style={[s.form, { marginTop: 12 }]}>
            <Field label="API token" value={token} onChange={setToken}
                   placeholder="token" secure />
            <Button title="Connect with token" variant="ghost" onPress={onToken}
                    loading={busy} />
          </View>
        )}

        <Text style={s.foot}>NIFTY OPTIONS · IST</Text>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function Field({
  label, value, onChange, placeholder, secure, keyboardType, onSubmit,
}: {
  label: string; value: string; onChange: (v: string) => void;
  placeholder?: string; secure?: boolean;
  keyboardType?: 'url' | 'default'; onSubmit?: () => void;
}) {
  return (
    <View>
      <Text style={s.label}>{label}</Text>
      <TextInput
        style={s.field}
        value={value}
        onChangeText={onChange}
        placeholder={placeholder}
        placeholderTextColor={C.faint}
        autoCapitalize="none"
        autoCorrect={false}
        spellCheck={false}
        secureTextEntry={secure}
        keyboardType={keyboardType === 'url' ? 'url' : 'default'}
        returnKeyType={onSubmit ? 'go' : 'next'}
        onSubmitEditing={onSubmit}
      />
    </View>
  );
}

const s = StyleSheet.create({
  wrap: {
    flexGrow: 1, justifyContent: 'center', alignItems: 'center',
    padding: 28, paddingVertical: 48,
  },
  wordmark: {
    color: C.au, fontSize: 14, fontWeight: '600', letterSpacing: 5.5,
    textTransform: 'uppercase', fontFamily: MONO, marginTop: 22,
  },
  tagline: {
    color: C.dim, fontSize: 10.5, letterSpacing: 2.4, textTransform: 'uppercase',
    marginTop: 9, fontFamily: MONO,
  },
  rule: {
    height: 1, backgroundColor: C.lineBright, width: '62%',
    marginVertical: 26, opacity: 0.8,
  },
  form: { width: '100%', maxWidth: 380, gap: 10 },
  label: {
    fontSize: 9, letterSpacing: 1.7, textTransform: 'uppercase',
    color: C.dim, fontFamily: MONO, marginBottom: 6,
  },
  field: {
    backgroundColor: C.surface2, borderWidth: 1, borderColor: C.lineStrong,
    borderRadius: R.md, paddingHorizontal: 14, paddingVertical: 13,
    color: C.text, fontSize: 15, fontFamily: MONO,
  },
  orRow: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    width: '100%', maxWidth: 380, marginVertical: 18,
  },
  orLine: { flex: 1, height: 1, backgroundColor: C.lineStrong },
  orText: { fontSize: 8.5, letterSpacing: 1.6, color: C.faint, fontFamily: MONO },
  err: {
    color: C.down, fontSize: 12, textAlign: 'center', lineHeight: 18,
    fontFamily: MONO, marginTop: 4,
  },
  altLink: {
    color: C.faint, fontSize: 9.5, letterSpacing: 1.4, textTransform: 'uppercase',
    fontFamily: MONO, marginTop: 22,
  },
  foot: {
    color: C.faint, fontSize: 9, letterSpacing: 1.4, textTransform: 'uppercase',
    fontFamily: MONO, marginTop: 30,
  },
});
