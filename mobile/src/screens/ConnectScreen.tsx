import React, { useState } from 'react';
import {
  KeyboardAvoidingView, Platform, ScrollView, StyleSheet,
  Text, TextInput, View,
} from 'react-native';
import Svg, { Path, Rect } from 'react-native-svg';

import { Button } from '../components/ui';
import { useStore } from '../store';
import { C, R } from '../theme';

export default function ConnectScreen() {
  const { connect } = useStore();
  const [url, setUrl] = useState('');
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const onConnect = async () => {
    if (!url.trim() || !token.trim()) {
      setErr('Both the server address and the token are required.');
      return;
    }
    setBusy(true);
    setErr('');
    try {
      const normalised = url.trim().match(/^https?:\/\//) ? url.trim() : `https://${url.trim()}`;
      await connect(normalised, token);
    } catch (e: any) {
      setErr(e?.message ?? 'Could not connect');
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
        <Svg width={62} height={62} viewBox="0 0 512 512">
          <Rect width="512" height="512" rx="112" fill={C.surface} />
          <Path
            d="M112 352V160l72 96 72-96 72 96 72-96v192"
            fill="none" stroke={C.accent} strokeWidth={30}
            strokeLinejoin="round" strokeLinecap="round"
          />
        </Svg>

        <View style={{ alignItems: 'center', marginTop: 18 }}>
          <Text style={s.title}>Meridian Capital</Text>
          <Text style={s.sub}>
            NIFTY options algorithm — live control and a full audit trail.
          </Text>
        </View>

        <View style={s.form}>
          <TextInput
            style={s.field}
            placeholder="https://your-server.com"
            placeholderTextColor={C.dim}
            value={url}
            onChangeText={setUrl}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            returnKeyType="next"
          />
          <TextInput
            style={s.field}
            placeholder="API token"
            placeholderTextColor={C.dim}
            value={token}
            onChangeText={setToken}
            autoCapitalize="none"
            autoCorrect={false}
            secureTextEntry
            returnKeyType="go"
            onSubmitEditing={onConnect}
          />
          <Button title="Connect" onPress={onConnect} loading={busy} />
          {!!err && <Text style={s.err}>{err}</Text>}
        </View>

        <Text style={s.hint}>
          The address and token come from the server you deployed — the same{'\n'}
          API_TOKEN you put in its .env file.
        </Text>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const s = StyleSheet.create({
  wrap: { flexGrow: 1, justifyContent: 'center', alignItems: 'center', padding: 30, gap: 4 },
  title: { color: C.text, fontSize: 23, fontWeight: '700', letterSpacing: -0.4 },
  sub: {
    color: C.muted, fontSize: 13.5, textAlign: 'center',
    marginTop: 7, maxWidth: 300, lineHeight: 20,
  },
  form: { width: '100%', maxWidth: 360, gap: 11, marginTop: 26 },
  field: {
    backgroundColor: C.surface2, borderWidth: 1, borderColor: C.line,
    borderRadius: R.md, paddingHorizontal: 15, paddingVertical: 14,
    color: C.text, fontSize: 15,
  },
  err: { color: C.down, fontSize: 13, textAlign: 'center', lineHeight: 19 },
  hint: {
    color: C.dim, fontSize: 11.5, textAlign: 'center',
    marginTop: 26, lineHeight: 18,
  },
});
