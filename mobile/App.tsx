import React, { useEffect, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { NavigationContainer, DefaultTheme } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { SafeAreaProvider, useSafeAreaInsets } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import { Ionicons } from '@expo/vector-icons';

import { Pill } from './src/components/ui';
import { istClock } from './src/format';
import ConnectScreen from './src/screens/ConnectScreen';
import ControlScreen from './src/screens/ControlScreen';
import DashboardScreen from './src/screens/DashboardScreen';
import FeedScreen from './src/screens/FeedScreen';
import ReportsScreen from './src/screens/ReportsScreen';
import TradesScreen from './src/screens/TradesScreen';
import { StoreProvider, useStore } from './src/store';
import { C } from './src/theme';

const Tab = createBottomTabNavigator();

const navTheme = {
  ...DefaultTheme,
  dark: true,
  colors: {
    ...DefaultTheme.colors,
    primary: C.accent,
    background: C.bg,
    card: C.bg,
    text: C.text,
    border: C.line,
    notification: C.down,
  },
};

/** Status strip: what the bot is doing, what mode, and the exchange clock. */
function Header() {
  const insets = useSafeAreaInsets();
  const { status, connection } = useStore();
  const [clock, setClock] = useState(istClock());

  useEffect(() => {
    const t = setInterval(() => setClock(istClock()), 1000);
    return () => clearInterval(t);
  }, []);

  const running = status?.running;
  const state = status?.state ?? 'offline';
  const paper = status?.config?.paper_mode ?? true;

  const statePill =
    state === 'error'
      ? { text: 'error', color: C.down, bg: C.downBg }
      : running
      ? { text: 'running', color: C.up, bg: C.upBg }
      : { text: state, color: C.muted, bg: 'rgba(255,255,255,0.06)' };

  return (
    <View style={[s.header, { paddingTop: insets.top + 10 }]}>
      <View style={s.headerRow}>
        <View style={s.headerLeft}>
          <Text style={s.wordmark}>Meridian</Text>
          <Pill {...statePill} dot pulse={!!running} />
          {connection !== 'live' && status && (
            <Pill text={connection} color={C.warn} bg={C.warnBg} />
          )}
        </View>
        <View style={s.headerRight}>
          <Pill
            text={paper ? 'paper' : 'live money'}
            color={paper ? C.info : C.down}
            bg={paper ? C.infoBg : C.downBg}
          />
          <Text style={s.clock}>{clock}</Text>
        </View>
      </View>
    </View>
  );
}

const ICONS: Record<string, [keyof typeof Ionicons.glyphMap, keyof typeof Ionicons.glyphMap]> = {
  Dashboard: ['stats-chart', 'stats-chart-outline'],
  Live: ['terminal', 'terminal-outline'],
  Trades: ['trending-up', 'trending-up-outline'],
  Reports: ['document-text', 'document-text-outline'],
  Control: ['settings', 'settings-outline'],
};

function Tabs() {
  return (
    <>
      <Header />
      <Tab.Navigator
        screenOptions={({ route }) => ({
          headerShown: false,
          tabBarActiveTintColor: C.accent,
          tabBarInactiveTintColor: C.dim,
          tabBarStyle: {
            backgroundColor: '#090c13',
            borderTopColor: C.line,
            borderTopWidth: StyleSheet.hairlineWidth,
            height: 62,
            paddingTop: 6,
            paddingBottom: 8,
          },
          tabBarLabelStyle: { fontSize: 9.5, fontWeight: '700', letterSpacing: 0.3 },
          tabBarIcon: ({ focused, color, size }) => {
            const [active, inactive] = ICONS[route.name] ?? ['ellipse', 'ellipse-outline'];
            return (
              <Ionicons name={focused ? active : inactive} size={size - 2} color={color} />
            );
          },
        })}
      >
        <Tab.Screen name="Dashboard" component={DashboardScreen} />
        <Tab.Screen name="Live" component={FeedScreen} />
        <Tab.Screen name="Trades" component={TradesScreen} />
        <Tab.Screen name="Reports" component={ReportsScreen} />
        <Tab.Screen name="Control" component={ControlScreen} />
      </Tab.Navigator>
    </>
  );
}

function Root() {
  const { ready, authed } = useStore();

  if (!ready) {
    return (
      <View style={s.splash}>
        <ActivityIndicator color={C.accent} />
      </View>
    );
  }

  if (!authed) return <ConnectScreen />;

  return (
    <NavigationContainer theme={navTheme}>
      <Tabs />
    </NavigationContainer>
  );
}

export default function App() {
  return (
    <SafeAreaProvider>
      <StatusBar style="light" backgroundColor={C.bg} />
      <StoreProvider>
        <Root />
      </StoreProvider>
    </SafeAreaProvider>
  );
}

const s = StyleSheet.create({
  splash: { flex: 1, backgroundColor: C.bg, alignItems: 'center', justifyContent: 'center' },
  header: {
    backgroundColor: C.bg,
    paddingHorizontal: 16,
    paddingBottom: 11,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: C.line,
  },
  headerRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10,
  },
  headerLeft: { flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 1 },
  headerRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  wordmark: { color: C.text, fontSize: 14.5, fontWeight: '700', letterSpacing: -0.2 },
  clock: { color: C.muted, fontSize: 12, fontVariant: ['tabular-nums'] },
});
