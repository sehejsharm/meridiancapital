import React, { useMemo, useRef, useState } from 'react';
import {
  FlatList, Pressable, ScrollView, StyleSheet, Text, View,
} from 'react-native';

import { Button, Empty } from '../components/ui';
import { clockOf } from '../format';
import { useStore } from '../store';
import { C, levelColor, MONO, R } from '../theme';
import type { FeedEvent } from '../types';

type Filter = 'all' | 'trade' | 'decision' | 'system' | 'alert';

const FILTERS: { value: Filter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'trade', label: 'Trades' },
  { value: 'decision', label: 'Decisions' },
  { value: 'system', label: 'System' },
  { value: 'alert', label: 'Alerts' },
];

const GROUPS: Record<Exclude<Filter, 'all'>, string[]> = {
  trade: ['entry', 'exit', 'ladder', 'order', 'signal', 'entry_blocked', 'entry_failed'],
  decision: ['decision', 'minute', 'chop_block', 'chop_pass', 'cooldown'],
  system: ['boot', 'ready', 'supervisor', 'scheduler', 'connected', 'heartbeat',
           'network', 'shutdown', 'scrip_master', 'equity_book',
           'session_open', 'session_resume', 'position_restored'],
  alert: ['fatal', 'daily_kill', 'order_failed', 'auth_failed', 'safety', 'entry_failed'],
};

export default function FeedScreen() {
  const { feed, clearFeed, connection } = useStore();
  const [filter, setFilter] = useState<Filter>('all');
  const [follow, setFollow] = useState(true);
  const listRef = useRef<FlatList<FeedEvent>>(null);

  const rows = useMemo(() => {
    if (filter === 'all') return feed;
    if (filter === 'alert') {
      return feed.filter((e) => GROUPS.alert.includes(e.kind) || e.level === 'error');
    }
    return feed.filter((e) => GROUPS[filter].includes(e.kind));
  }, [feed, filter]);

  return (
    <View style={{ flex: 1, backgroundColor: C.bg, padding: 16 }}>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={{ gap: 7, paddingBottom: 11 }}
      >
        {FILTERS.map((f) => {
          const on = f.value === filter;
          return (
            <Pressable
              key={f.value}
              onPress={() => setFilter(f.value)}
              style={[s.chip, on && { backgroundColor: C.accent, borderColor: C.accent }]}
            >
              <Text style={[s.chipText, on && { color: '#04120c' }]}>{f.label}</Text>
            </Pressable>
          );
        })}
      </ScrollView>

      <View style={s.terminal}>
        {rows.length === 0 ? (
          <Empty
            text={
              connection === 'live'
                ? 'Connected. Output appears here the moment the bot prints it.'
                : 'Nothing here yet.'
            }
          />
        ) : (
          <FlatList
            ref={listRef}
            data={rows}
            keyExtractor={(item, i) => `${item.ts}-${item.seq ?? i}-${i}`}
            renderItem={({ item }) => <Line e={item} />}
            onContentSizeChange={() => {
              if (follow) listRef.current?.scrollToEnd({ animated: true });
            }}
            initialNumToRender={40}
            maxToRenderPerBatch={40}
            windowSize={12}
            removeClippedSubviews
          />
        )}
      </View>

      <View style={{ flexDirection: 'row', gap: 9, marginTop: 10 }}>
        <Button title="Clear" variant="ghost" style={{ flex: 1 }} onPress={clearFeed} />
        <Button
          title={`Auto-scroll: ${follow ? 'on' : 'off'}`}
          variant="ghost"
          style={{ flex: 1 }}
          onPress={() => setFollow((v) => !v)}
        />
      </View>
    </View>
  );
}

const Line = React.memo(({ e }: { e: FeedEvent }) => {
  const isEvent = e.kind !== 'log';
  return (
    <View style={s.line}>
      <Text style={s.time}>{clockOf(e.ts)}</Text>
      <View style={{ flex: 1, flexDirection: 'row', flexWrap: 'wrap' }}>
        {isEvent && (
          <View style={s.tag}>
            <Text style={s.tagText}>{e.kind}</Text>
          </View>
        )}
        <Text
          style={[
            s.msg,
            { color: isEvent ? C.info : levelColor(e.level) },
          ]}
        >
          {e.message}
        </Text>
      </View>
    </View>
  );
});

const s = StyleSheet.create({
  chip: {
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: R.pill,
    backgroundColor: C.surface2, borderWidth: 1, borderColor: C.line,
  },
  chipText: { fontSize: 12, fontWeight: '700', color: C.muted },
  terminal: {
    flex: 1, backgroundColor: '#04060b', borderWidth: 1, borderColor: C.line,
    borderRadius: 15, padding: 11,
  },
  line: { flexDirection: 'row', gap: 9, paddingVertical: 1.5 },
  time: {
    color: C.dim, fontSize: 10.5, fontFamily: MONO, paddingTop: 1.5,
    fontVariant: ['tabular-nums'],
  },
  msg: { fontSize: 11.5, fontFamily: MONO, lineHeight: 18, flexShrink: 1 },
  tag: {
    backgroundColor: 'rgba(87,166,255,0.16)', borderRadius: 5,
    paddingHorizontal: 5, marginRight: 6, alignSelf: 'flex-start',
  },
  tagText: { color: C.info, fontSize: 9.5, fontWeight: '800', letterSpacing: 0.4, lineHeight: 17 },
});
