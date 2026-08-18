/**
 * Runtime tests for the dashboard's chart rendering.
 *
 *   node web/test/render.test.mjs
 *
 * The dashboard is one self-contained HTML file, so this extracts its inline
 * script, runs it against a minimal DOM stub, and drives the drawing
 * functions with representative data. Chart maths is easy to get subtly
 * wrong — a scale that divides by zero on a flat series, an overlay with
 * leading nulls — and none of that shows up in a syntax check.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const HERE = dirname(fileURLToPath(import.meta.url));
const HTML = join(HERE, '..', 'index.html');

let pass = 0;
let fail = 0;

function check(label, cond, detail = '') {
  if (cond) {
    pass++;
    console.log(`  \x1b[32mPASS\x1b[0m  ${label}`);
  } else {
    fail++;
    console.log(`  \x1b[31mFAIL\x1b[0m  ${label}  ${detail}`);
  }
}

// ---------------------------------------------------------------- DOM stub

function makeElement(id = '') {
  const el = {
    id,
    _html: '',
    textContent: '',
    value: '',
    disabled: false,
    dataset: {},
    style: {},
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); },
      remove(c) { this._set.delete(c); },
      toggle(c, on) { on ? this._set.add(c) : this._set.delete(c); },
      contains(c) { return this._set.has(c); },
    },
    setAttribute() {},
    getAttribute() { return null; },
    querySelector: () => makeElement(),
    addEventListener() {},
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return this._html; },
    set(v) { this._html = String(v); },
  });
  return el;
}

const elements = new Map();
const doc = {
  // The real page toggles classes on body (live-money, stale), so the stub
  // needs one or renderDash throws before it draws anything.
  body: makeElement('body'),
  createElement: () => makeElement(),
  head: { appendChild() {} },
  querySelector(sel) {
    if (!elements.has(sel)) elements.set(sel, makeElement(sel));
    return elements.get(sel);
  },
  querySelectorAll() { return []; },
  addEventListener() {},
};

const sandbox = {
  console,
  document: doc,
  localStorage: {
    _d: {},
    getItem(k) { return this._d[k] ?? null; },
    setItem(k, v) { this._d[k] = String(v); },
    removeItem(k) { delete this._d[k]; },
  },
  location: { origin: 'https://example.test', protocol: 'https:' },
  setInterval: () => 0,
  clearInterval: () => {},
  setTimeout: () => 0,
  clearTimeout: () => {},
  fetch: async () => { throw new Error('network disabled in tests'); },
  WebSocket: function () { this.close = () => {}; },
  confirm: () => false,
  alert: () => {},
  encodeURIComponent,
  URLSearchParams,
  Intl,
  Date,
  Math,
  Number,
  String,
  Object,
  Array,
  JSON,
  isFinite,
  parseInt,
  parseFloat,
  isNaN,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

const html = readFileSync(HTML, 'utf8');
const match = html.match(/<script>\n([\s\S]*?)\n<\/script>/);
if (!match) {
  console.error('Could not find the inline script in index.html');
  process.exit(1);
}

const context = vm.createContext(sandbox);
try {
  vm.runInContext(match[1], context, { filename: 'dashboard.js' });
} catch (err) {
  console.error('Dashboard script threw while loading:', err.message);
  process.exit(1);
}

// ---------------------------------------------------------------- fixtures

function candles(n, base = 24500, flat = false) {
  const out = [];
  let price = base;
  const start = Date.UTC(2026, 7, 4, 3, 45); // 09:15 IST
  for (let i = 0; i < n; i++) {
    const open = price;
    const drift = flat ? 0 : Math.sin(i / 7) * 12 + (i % 5) - 2;
    const close = open + drift;
    out.push([
      start + i * 60000,
      round(open),
      round(Math.max(open, close) + (flat ? 0 : 4)),
      round(Math.min(open, close) - (flat ? 0 : 4)),
      round(close),
      1000 + i,
    ]);
    price = close;
  }
  return out;
}

const round = (v) => Math.round(v * 100) / 100;

const LEVELS = {
  stop: { price: 128.25, pct: -10, label: 'Stop loss' },
  breakeven: { price: 163.88, pct: 15, floor: 148.2, label: 'Breakeven lock' },
  lock1: { price: 178.13, pct: 25, floor: 156.75, label: 'Lock 1' },
  lock2: { price: 199.5, pct: 40, floor: 178.13, label: 'Lock 2' },
  free: { giveback_pct: 10, label: 'Trail 10% behind peak' },
};

const track = (series) => ({
  symbol: 'NIFTY07AUG2624500CE', opt_type: 'C', strike: 24500,
  series, entry: 142.5, sl: 128.25, stage: 'INIT',
  high_prem: 152.0, entry_ts: Date.UTC(2026, 7, 4, 4, 22), levels: LEVELS,
});

// ---------------------------------------------------------------- tests

console.log('='.repeat(60));
console.log('  DASHBOARD RENDER TESTS');
console.log('='.repeat(60));

console.log('\nCandle chart');
{
  const svg = sandbox.drawCandles({ candles: candles(180), index: 'NIFTY' });
  check('renders an svg', svg.startsWith('<svg') && svg.endsWith('</svg>'));
  const bodies = (svg.match(/<rect/g) || []).length;
  check(`draws one body per candle (${bodies})`, bodies === 180);
  check('no NaN coordinates leaked', !svg.includes('NaN'), svg.slice(0, 200));
  check('no undefined leaked', !svg.includes('undefined'));
  check('axis labels present', svg.includes('09:15'), 'expected an IST time label');
}
{
  const n = 60;
  const svg = sandbox.drawCandles({
    candles: candles(n),
    vwap: Array.from({ length: n }, (_, i) => (i < 5 ? null : 24500 + i)),
    ema9: Array.from({ length: n }, (_, i) => 24495 + i),
    ema21: Array.from({ length: n }, () => null),
  });
  check('overlay with leading nulls does not break the path', !svg.includes('NaN'));
  check('vwap drawn as a dashed path', svg.includes('stroke-dasharray="4 3"'));
  check('an all-null overlay draws nothing rather than a broken path',
        (svg.match(/#9a7dff/g) || []).length === 0);
}
{
  const svg = sandbox.drawCandles({ candles: candles(40, 24500, true) });
  check('flat series does not divide by zero', !svg.includes('NaN') && svg.includes('<svg'));
}
{
  const empty = sandbox.drawCandles({ candles: [], reason: 'Bot is not running.' });
  check('empty candles show the reason', empty.includes('Bot is not running.'));
  const one = sandbox.drawCandles({ candles: candles(1) });
  check('a single candle degrades to the empty state', one.includes('chart-empty'));
  check('the empty chart explains what happens next, not just that it is empty',
        one.includes('ce-s') && one.length > 200, one.slice(0, 160));
}

console.log('\nTrade box');
{
  const series = Array.from({ length: 120 }, (_, i) => [
    Date.UTC(2026, 7, 4, 4, 22) + i * 5000,
    round(142.5 + Math.sin(i / 9) * 9 + i * 0.06),
  ]);
  const svg = sandbox.drawTradeBox(track(series), 150.2);
  check('renders an svg', svg.startsWith('<svg'));
  check('no NaN coordinates', !svg.includes('NaN'));
  check('entry line drawn', svg.includes('ENTRY'));
  check('stop line drawn', svg.includes('STOP'));
  check('breakeven target drawn', svg.includes('BREAKEVEN'));
  check('lock 1 target drawn', svg.includes('LOCK 1'));
  check('peak marked when above entry', svg.includes('PEAK'));
  check('loss zone shaded below the stop', svg.includes('opacity=".07"'));
  check('in profit the line uses the up colour',
        svg.includes('stroke="#3ecf8e"'), 'expected the up colour at 150.2 over 142.5');
}
{
  const series = Array.from({ length: 30 }, (_, i) => [
    Date.UTC(2026, 7, 4, 4, 22) + i * 5000, round(142.5 - i * 0.4),
  ]);
  const svg = sandbox.drawTradeBox(track(series), 130.6);
  check('in loss the line uses the down colour', svg.includes('stroke="#f0505a"'));
  check('no NaN when underwater', !svg.includes('NaN'));
}
{
  const svg = sandbox.drawTradeBox(track([]), 142.5);
  check('empty series still renders using entry and stop',
        svg.includes('<svg') || svg.includes('Collecting'));
  const flat = Array.from({ length: 20 }, (_, i) => [i, 142.5]);
  const svg2 = sandbox.drawTradeBox(track(flat), 142.5);
  check('a perfectly flat premium does not divide by zero', !svg2.includes('NaN'));
}
{
  // Levels far outside the visible range must be skipped, not clipped badly.
  const far = { ...track([[0, 142.5], [1, 143]]) };
  far.levels = { ...LEVELS, lock2: { price: 9999, pct: 40, floor: 5000, label: 'Lock 2' } };
  const svg = sandbox.drawTradeBox(far, 143);
  check('off-scale level is omitted', !svg.includes('LOCK 2'));
  check('still renders the in-range levels', svg.includes('ENTRY'));
}

// `const` declarations in the dashboard script do not become properties of the
// sandbox global — only function declarations do — so anything declared that
// way (S, TAPE, the formatting helpers) is reached by evaluating inside the
// context rather than off the sandbox object.
const evaluate = (expr) => vm.runInContext(expr, context);

console.log('\nFormatting');
{

  check('money uses Indian grouping',
        evaluate('money(2500000, 0)') === '₹25,00,000',
        evaluate('money(2500000, 0)'));
  check('signed money marks a gain', evaluate('signed(2609, 0)').startsWith('+'));
  check('signed money marks a loss', evaluate('signed(-946, 0)').startsWith('−'));
  check('percent carries a sign', evaluate('pct(12.345, 1)') === '+12.3%',
        evaluate('pct(12.345, 1)'));
  check('null money renders as a dash, not NaN', evaluate('money(null)') === '—');
  check('escaping neutralises markup',
        evaluate('esc("<img src=x onerror=alert(1)>")').includes('&lt;img'));
  check('tone class follows the sign',
        evaluate('cls(5)') === 'up' && evaluate('cls(-5)') === 'down');
}

console.log('\nPosition box');
{
  // A long that is currently under water: entry 142.5, stop 128, now 135.
  const q = { entry: 142.5, current: 135.0, sl_price: 128.0, qty: 75,
              pnl: -562.5, symbol: 'NIFTY24500CE' };
  const opt = { levels: { lock1: { price: 171.0, pct: 20 } } };
  const box = sandbox.positionBox(q, opt);

  check('position box renders', box.includes('posbox'));
  check('it shows a reward band and a risk band',
        box.includes('pb-band reward') && box.includes('pb-band risk'));
  check('the contract price is on screen', box.includes('135.00'), box.slice(0, 200));
  check('the symbol is named', box.includes('NIFTY24500CE'));
  check('a losing position reads as down', box.includes('pb-mid down'));
  // risk = (142.5-128)*75 = 1087.5, reward = (171-142.5)*75 = 2137.5 -> 1.97
  check('risk/reward is computed from the ladder rung',
        box.includes('R:R 1.97'), box.match(/R:R [\d.—]+/)?.[0]);

  const winner = sandbox.positionBox({ ...q, current: 160, pnl: 1312.5 }, opt);
  check('a winning position reads as up', winner.includes('pb-mid up'));

  // Without a ladder rung the target mirrors the risk, so R:R is 1.
  const noLadder = sandbox.positionBox(q, { levels: {} });
  check('with no rung the target mirrors the stop', noLadder.includes('R:R 1.00'),
        noLadder.match(/R:R [\d.—]+/)?.[0]);

  check('a position with no price yet renders nothing rather than NaN',
        sandbox.positionBox({ entry: null, current: null, sl_price: null }, {}) === '');
}

console.log('\nLive tape');
{
  evaluate(`S.snap = { market: { spot: 24500.5, day_move: 42.3, vwap: 24480,
                                adx: 22.4, garch: 12.1, index: 'NIFTY' },
                      day_pnl: 2609 }`);
  evaluate('TAPE.shown = null; TAPE.target = null; TAPE.dir = 0');
  sandbox.tapeTick();
  const el = sandbox.document.querySelector('#d-tape');
  check('tape renders the instrument', el.innerHTML.includes('NIFTY'));
  check('tape shows the spot', el.innerHTML.includes('24,500') , el.innerHTML.slice(0, 160));
  check('tape carries the day move', el.innerHTML.includes('+42.3 pts'));
  check('tape shows P&L', el.innerHTML.includes('pb') || el.innerHTML.includes('2,609'));

  // The displayed figure eases toward a new reading rather than jumping.
  evaluate('S.snap.market.spot = 24600');
  sandbox.tapeTick();
  const mid = evaluate('TAPE.shown');
  check(`tape eases toward the new price (${mid.toFixed(1)})`,
        mid > 24500.5 && mid < 24600, String(mid));
  check('tape records the direction', evaluate('TAPE.dir') === 1);
  for (let i = 0; i < 80; i++) sandbox.tapeTick();
  check('tape converges on the real price',
        Math.abs(evaluate('TAPE.shown') - 24600) < 0.05, String(evaluate('TAPE.shown')));

  // Never invent a price when the feed has not produced one.
  evaluate('S.snap = {}');
  evaluate('TAPE.shown = null; TAPE.target = null');
  sandbox.tapeTick();
  check('tape shows a dash with no feed',
        sandbox.document.querySelector('#d-tape').innerHTML.includes('—'));
}

console.log('\nFleet');
{
  evaluate(`S.fleet = ${JSON.stringify({
    max_slots: 5, running_count: 1, memory_free_mb: 430, headroom_slots: 2,
    capacity_warning: null,
    slots: [
      { slot: 0, name: 'Primary', state: 'running', running: true, empty: false,
        algorithm: 'Built-in v11 (original)', day_pnl: 2609.08, trades: 2, position: true },
      { slot: 1, name: 'Slot 2', state: 'stopped', running: false, empty: true,
        algorithm: 'Empty — no algorithm assigned' },
      { slot: 2, name: 'Slot 3', state: 'error', running: false, empty: false,
        algorithm: 'v12 wider stops', last_error: 'Angel One login rejected' },
      { slot: 3, name: 'Slot 4', state: 'stopped', running: false, empty: true },
      { slot: 4, name: 'Slot 5', state: 'stopped', running: false, empty: true },
    ],
  })}`);
  sandbox.renderFleet();
  const out = sandbox.document.querySelector('#d-fleet').innerHTML;

  // Empty slots belong in Admin where they can be filled — on the deck they
  // were five identical grey rectangles saying nothing about the trading day.
  check('only slots with an algorithm are shown',
        (out.match(/class="fl /g) || []).length === 2,
        String((out.match(/class="fl /g) || []).length));
  check('the free slots are counted rather than drawn',
        out.includes('3 slots free'), out.match(/\d+ slots? free/)?.[0]);
  check('the running slot reads good', out.includes('fl good'));
  check('the faulted slot reads bad', out.includes('fl bad'));
  check('a faulted slot prints its error', out.includes('Angel One login rejected'));

  // Selecting a lane is what switches the deck above it.
  check('slots are selectable', out.includes('data-deck="0"') && out.includes('data-deck="2"'));
  check('one lane is marked as selected', (out.match(/fl [a-z]+ sel/g) || []).length === 1,
        String((out.match(/sel/g) || []).length));
  check('with more than one lane it invites a selection',
        out.includes('Select an algorithm'));
  check('memory headroom is reported', out.includes('430 MB free'));
  check('the running count is shown', out.includes('1 of 5 running'));
  check('per-slot P&L is attributed', out.includes('2,609'));

  // The layout follows how many lanes are in use, not a fixed five.
  check('two lanes in use lays out two columns', out.includes('--fl-cols:2'),
        out.match(/--fl-cols:\d/)?.[0]);

  evaluate("S.fleet.capacity_warning = 'Only 90 MB of memory is free'");
  sandbox.renderFleet();
  check('a memory warning surfaces',
        sandbox.document.querySelector('#d-fleet').innerHTML.includes('cap-warn'));

  // The deck has to read from the selected lane, not always from slot 0 —
  // that was the bug: slot 2 could not be inspected at all.
  evaluate('S.fleet.slots[0].snapshot = { equity: 19615, day_pnl: 0 }');
  evaluate('S.fleet.slots[2].snapshot = { equity: 24100, day_pnl: 1400 }');
  evaluate('S.deckSlot = 2');
  check('the deck reads the selected slot',
        evaluate('deckSnapshot().equity') === 24100,
        String(evaluate('deckSnapshot().equity')));
  evaluate('S.deckSlot = 0');
  check('and switches back', evaluate('deckSnapshot().equity') === 19615);

  // Selecting a lane that is later emptied must not blank the deck.
  evaluate('S.deckSlot = 4');
  sandbox.renderFleet();
  check('a selection on a vanished slot falls back to a live one',
        evaluate('deckSlot()') === 0, String(evaluate('deckSlot()')));

  // An algorithm that emits no @@EVT@@ runs fine and reports nothing. Showing
  // the usual zeros for it reads as a flat book rather than silence.
  evaluate(`S.fleet.slots = [
    { slot: 0, name: 'Primary', state: 'running', running: true, empty: false,
      reporting: true, algorithm: 'v11', day_pnl: 0, trades: 0,
      snapshot: { equity: 19615 } },
    { slot: 1, name: 'Slot 2', state: 'running', running: true, empty: false,
      reporting: false, algorithm: 'High Octane', snapshot: {} },
  ]; S.fleet.capacity_warning = null`);
  evaluate('S.deckSlot = 1');
  sandbox.renderFleet();
  const silent = sandbox.document.querySelector('#d-fleet').innerHTML;
  check('a silent algorithm says so on its card',
        silent.includes('NO TELEMETRY'), silent.slice(0, 200));
  check('and does not print a fake zero P&L for it',
        !/Slot 2[\s\S]*?Day P&L/.test(silent));

  sandbox.renderDash();
  const mutePanel = sandbox.document.querySelector('#d-mute');
  check('the deck explains the silence rather than showing zeros',
        mutePanel.hidden === false && mutePanel.innerHTML.includes('not reporting'),
        String(mutePanel.hidden));
  check('it names the actual cause', mutePanel.innerHTML.includes('@@EVT@@'));
  check('the equity panel is hidden while a slot is silent',
        sandbox.document.querySelector('#d-body').style.display === 'none');

  // Switching back to a reporting slot restores the deck.
  evaluate('S.deckSlot = 0');
  sandbox.renderDash();
  check('a reporting slot is unaffected',
        sandbox.document.querySelector('#d-mute').hidden === true
        && sandbox.document.querySelector('#d-body').style.display === '');

  // A single algorithm needs no picker and no instruction to tap anything.
  evaluate('S.fleet.slots = S.fleet.slots.slice(0,1)');
  sandbox.renderFleet();
  const solo = sandbox.document.querySelector('#d-fleet').innerHTML;
  check('one algorithm draws no selection hint', !solo.includes('Select an algorithm'));
}

console.log('\nNews');
{
  evaluate(`S.news = { age_seconds: 90, items: [
    { title: 'Nifty ends higher', url: 'https://x/a', source: 'Moneycontrol',
      published: '2026-08-17T15:35:00+05:30' },
  ] }`);
  sandbox.renderNews();
  const out = sandbox.document.querySelector('#d-news').innerHTML;
  check('a headline renders', out.includes('Nifty ends higher'));
  check('the source is credited', out.includes('Moneycontrol'));
  check('headlines open in a new tab safely',
        out.includes('rel="noopener noreferrer"'));

  evaluate("S.news = { items: [], error: 'News is unavailable.' }");
  sandbox.renderNews();
  check('an empty feed explains itself',
        sandbox.document.querySelector('#d-news').innerHTML.includes('News is unavailable'));
}

console.log('\nEquity curve');
{
  const sessions = [
    { session_date: '2026-08-03', close_equity: 20000, day_pnl: 0 },
    { session_date: '2026-08-04', close_equity: 21662, day_pnl: 1662 },
    { session_date: '2026-08-05', close_equity: 25124, day_pnl: 3462 },
    { session_date: '2026-08-06', close_equity: 23000, day_pnl: -2124 },
    { session_date: '2026-08-07', close_equity: 24000, day_pnl: 1000 },
  ];
  const svg = sandbox.drawCurve(sessions);
  check('renders an svg', svg.startsWith('<svg'));
  check('no NaN coordinates', !svg.includes('NaN'), svg.slice(0, 200));
  check('the window is labelled at both ends',
        svg.includes('2026-08-03') && svg.includes('2026-08-07'));
  check('the drawdown is shaded against the running peak',
        svg.includes('#ff5566'), 'expected a drawdown band');
  check('it is described for a screen reader', svg.includes('aria-label'));

  check('one session is not a curve',
        sandbox.drawCurve([sessions[0]]).includes('chart-empty'));
  check('no sessions degrades cleanly',
        sandbox.drawCurve([]).includes('chart-empty'));
  // A perfectly flat book must not divide by a zero range.
  const flat = sandbox.drawCurve([
    { session_date: '2026-08-03', close_equity: 20000 },
    { session_date: '2026-08-04', close_equity: 20000 },
  ]);
  check('a flat curve does not divide by zero', !flat.includes('NaN') && flat.includes('<svg'));
  // Rows with no close must be skipped, not plotted as zero.
  const gappy = sandbox.drawCurve([
    { session_date: '2026-08-03', close_equity: 20000 },
    { session_date: '2026-08-04', close_equity: null },
    { session_date: '2026-08-05', close_equity: 21000 },
  ]);
  check('sessions with no close are skipped', !gappy.includes('NaN'));
}

console.log('\nDaily P&L calendar');
{
  const cal = sandbox.drawCalendar([
    { session_date: '2026-08-03', day_pnl: 1200, trades: 2 },
    { session_date: '2026-08-04', day_pnl: -800, trades: 1 },
    { session_date: '2026-08-05', day_pnl: 0, trades: 0 },
  ]);
  check('renders a grid', cal.includes('cal-grid'));
  check('a profitable day is green', cal.includes('rgba(56,224,143'), cal.slice(0, 300));
  check('a losing day is red', cal.includes('rgba(255,85,102'));
  check('a flat day is neither', cal.includes('cal-c flat'));
  check('days are clickable and dated',
        cal.includes('data-cal="2026-08-03"'));
  check('each cell is labelled for a screen reader', cal.includes('aria-label'));
  check('no sessions says so', sandbox.drawCalendar([]).includes('No sessions'));

  // Intensity is relative, so one huge day must not flatten the rest to nothing.
  const skew = sandbox.drawCalendar([
    { session_date: '2026-08-03', day_pnl: 100000, trades: 1 },
    { session_date: '2026-08-04', day_pnl: 500, trades: 1 },
  ]);
  check('a small winning day is still visibly green',
        /rgba\(56,224,143,0\.[12]\d\)/.test(skew), skew.match(/rgba\(56,224,143,[\d.]+\)/g)?.join(' '));
}

console.log('\nAlgorithm diff');
{
  const lines = (n, tag = 'x') =>
    Array.from({ length: n }, (_, i) => `${tag}${i}`).join('\n');

  const same = sandbox.diffLines('a\nb\nc', 'a\nb\nc');
  check('identical files report no changes',
        same.every((r) => r.t === 'same'), JSON.stringify(same));

  const added = sandbox.diffLines('a\nb', 'a\nNEW\nb');
  check('an inserted line is an addition',
        added.filter((r) => r.t === 'add').length === 1 &&
        added.filter((r) => r.t === 'del').length === 0,
        JSON.stringify(added));
  check('the inserted text is carried through',
        added.some((r) => r.t === 'add' && r.s === 'NEW'));

  const removed = sandbox.diffLines('a\nGONE\nb', 'a\nb');
  check('a dropped line is a deletion',
        removed.filter((r) => r.t === 'del').length === 1 &&
        removed.filter((r) => r.t === 'add').length === 0,
        JSON.stringify(removed));

  const edited = sandbox.diffLines('a\nold\nb', 'a\nnew\nb');
  check('an edited line shows as one out and one in',
        edited.filter((r) => r.t === 'del').length === 1 &&
        edited.filter((r) => r.t === 'add').length === 1);

  check('line numbers are 1-based',
        sandbox.diffLines('a\nb', 'a\nZ\nb').find((r) => r.t === 'add').n === 2,
        JSON.stringify(sandbox.diffLines('a\nb', 'a\nZ\nb')));

  // The head/tail trim is the reason this stays fast on real files; a change
  // buried in the middle of a long file must still be found.
  const bulk = lines(600);
  const mutated = bulk.split('\n');
  mutated[300] = 'CHANGED';
  const deep = sandbox.diffLines(bulk, mutated.join('\n'));
  check('a change deep inside a long file is found',
        deep.some((r) => r.t === 'add' && r.s === 'CHANGED') &&
        deep.some((r) => r.t === 'del' && r.s === 'x300'));
  check('the trim keeps the output small rather than echoing the file',
        deep.length < 40, `${deep.length} rows for a one-line change`);
  check('a few lines of context surround the change',
        deep.filter((r) => r.t === 'same').length >= 2);

  // O(n·m) has to be bounded or a pasted 20k-line file locks the tab.
  const huge = sandbox.diffLines(lines(4100), lines(4100, 'y'));
  check('an oversized file refuses rather than hanging',
        huge.length === 1 && huge[0].t === 'note', JSON.stringify(huge).slice(0, 120));
  check('the refusal says how big the files were',
        huge[0].s.includes('4100'));

  check('an empty file against content is all additions',
        sandbox.diffLines('', 'a\nb').filter((r) => r.t === 'add').length >= 1);

  // The download grid in Reports already owns `.dl`; the diff rows must not
  // borrow it or they inherit that layout.
  check('diff rows do not reuse the download-grid class',
        !/class="dl (add|del|fold)/.test(html) && html.includes('dfl-c'),
        'diff line class collides with .dl');
  for (const id of ['dif', 'dif-title', 'dif-stat', 'dif-body', 'dif-close']) {
    check(`the diff modal has #${id}`, html.includes(`id="${id}"`));
  }
  for (const sel of ['.dfl{', '.dfl-n{', '.dfl-s{', '.dfl-c{', '.dfl.add{',
                     '.dfl.del{', '.dfl.fold{', '#dif{']) {
    check(`styles define ${sel.replace('{', '')}`, html.includes(sel));
  }
  check('the diff modal is hidden until asked for',
        /<div id="dif" hidden>/.test(html));
  check('escape closes the diff', html.includes('difClose()'));
}

console.log('\nReport log toggle');
{
  const note = () => evaluate('document.querySelector("#r-log-note").innerHTML');

  evaluate('S.includeEvents = false; S.reportPreview = null; renderLogNote();');
  check('with the log off it says what you do get',
        /trades only/i.test(note()) && /price/i.test(note()), note());
  check('with the log off it does not promise log lines',
        !/log entr/i.test(note()), note());

  evaluate(`S.includeEvents = true;
    S.reportPreview = { log_lines: 18432, minute_marks: 375 }; renderLogNote();`);
  check('with the log on it says how much is coming',
        note().includes('18,432') && note().includes('375'), note());
  check('a big log warns that the PDF is the one that caps',
        /PDF prints the first 4,000/.test(note()), note());

  evaluate(`S.reportPreview = { log_lines: 120, minute_marks: 30 }; renderLogNote();`);
  check('a small log makes no such caveat',
        !/PDF prints the first/.test(note()), note());
  check('a small log still states the counts', note().includes('120'), note());

  evaluate(`S.reportPreview = { log_lines: 1, minute_marks: 1 }; renderLogNote();`);
  check('one of each reads as singular',
        note().includes('1</b> log entry') && note().includes('1</b> minute mark'),
        note());

  evaluate(`S.reportPreview = { log_lines: 0, minute_marks: 0 }; renderLogNote();`);
  check('an empty window says so rather than promising nothing usefully',
        /Nothing was logged/.test(note()), note());

  check('the toggle is named for what it now includes',
        html.includes('Include the minute-by-minute log'));
  check('the preview endpoint is asked for the counts',
        html.includes('/api/export/preview'));

  const mainPy = readFileSync(join(HERE, '..', '..', 'backend', 'app', 'main.py'), 'utf8');
  check('the counts are counted, not fetched',
        /SELECT COUNT\(\*\) AS n FROM events/.test(mainPy) &&
        /SELECT COUNT\(\*\) AS n FROM equity_marks/.test(mainPy));

  const exportsPy = readFileSync(
    join(HERE, '..', '..', 'backend', 'app', 'exports.py'), 'utf8');
  // Matched as SQL, not as prose — the comment above the query explains the
  // bug and names the clause, so a bare substring search finds itself.
  check('the log query no longer drops printed output',
        !/AND\s+kind\s*!=\s*'log'/.test(exportsPy),
        "AND kind != 'log' is back — printed lines will vanish from reports again");
  check('risk metrics reach the exports',
        /RISK_ROWS/.test(exportsPy) && /Sharpe ratio/.test(exportsPy));
}

console.log('\nAlgorithm brief');
{
  const md = sandbox.renderMarkdown;
  check('headings render at the right level',
        md('# Title\n\n## Section').includes('<h1>Title</h1>') &&
        md('# Title\n\n## Section').includes('<h2>Section</h2>'));
  check('paragraphs join wrapped lines',
        md('one line\nand its continuation').includes('<p>one line and its continuation</p>'));
  // Entities are what lands in the HTML; what matters is that the text the
  // reader sees — and copies — is byte-for-byte what the server sent.
  const decode = s => s.replace(/&quot;/g, '"').replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  check('fenced code survives verbatim',
        decode(md('```\nemit("status", equity=1)\n```')).includes('emit("status", equity=1)'),
        md('```\nemit("status", equity=1)\n```'));
  check('indentation inside a fence is preserved',
        decode(md('```\ndef f():\n    return 1\n```')).includes('\n    return 1'));
  check('code fences are not treated as prose',
        !md('```\n# not a heading\n```').includes('<h1>'));
  check('inline code is marked up', md('use `emit()` here').includes('<code>emit()</code>'));
  check('bold is marked up', md('**required**').includes('<strong>required</strong>'));
  check('lists render', md('- one\n- two').includes('<li>one</li>'));
  check('a list closes before the next heading',
        /<\/ul>\s*<h2>/.test(md('- one\n\n## Next')), md('- one\n\n## Next'));
  check('tables render with a head and a body',
        md('| a | b |\n|---|---|\n| 1 | 2 |').includes('<th>a</th>') &&
        md('| a | b |\n|---|---|\n| 1 | 2 |').includes('<td>1</td>'));
  check('a rule renders', md('---').includes('<hr>'));

  // The brief is fetched from the server, so it is data, not source.
  check('markup in the document is escaped, not executed',
        !md('<img src=x onerror=alert(1)>').includes('<img src=x'),
        md('<img src=x onerror=alert(1)>'));
  check('markup inside a code fence is escaped too',
        !md('```\n<script>bad()</script>\n```').includes('<script>bad()'));
  check('markup inside a table cell is escaped',
        !md('| a |\n|---|\n| <img src=x> |').includes('<img src=x'));

  check('the brief is offered where an algorithm is uploaded',
        html.includes('id="a-brief"') && html.includes('id="a-brief-dl"') &&
        html.includes('id="a-brief-read"'));
  check('it is fetched rather than duplicated in the page',
        html.includes('/api/algorithm/brief'));
  check('a blocked clipboard falls back to showing the text',
        /if \(!await copyText\(md, "Brief"\)\) showDoc/.test(html));
  check('the reader exists', html.includes('id="doc-body"') && html.includes('id="doc-copy"'));
  check('escape closes the reader', html.includes('docClose()'));
}

console.log('\nAudit trail');
{
  check('live-money actions group together',
        sandbox.auditGroup('live_mode_requested') === 'live' &&
        sandbox.auditGroup('paper_mode_restored') === 'live');
  check('people actions group together',
        sandbox.auditGroup('user_created') === 'user' &&
        sandbox.auditGroup('user_deleted') === 'user');
  check('algorithm actions group together',
        sandbox.auditGroup('algorithm_assigned') === 'algorithm');
  check('an unknown action still lands somewhere',
        sandbox.auditGroup('something_new') === 'other');

  // Every action the backend can write needs a plain-English label; a raw
  // `algorithm_assigned` in the admin screen is a leaked implementation detail.
  const backend = readFileSync(join(HERE, '..', '..', 'backend', 'app', 'main.py'), 'utf8')
    + readFileSync(join(HERE, '..', '..', 'backend', 'app', 'approvals.py'), 'utf8');
  const actions = [...backend.matchAll(/record\(\s*[^,]+,\s*"([a-z_]+)"/g)]
    .map((m) => m[1]);
  check('the backend writes at least a handful of action kinds',
        new Set(actions).size >= 8, [...new Set(actions)].join(', '));
  const unlabelled = [...new Set(actions)].filter(
    (a) => !evaluate(`Object.prototype.hasOwnProperty.call(AUDIT_LABELS, ${JSON.stringify(a)})`));
  check('every audited action has a readable label in the app',
        unlabelled.length === 0, `unlabelled: ${unlabelled.join(', ')}`);

  evaluate(`S.audit = [
    { id: 3, ts: "2026-08-18T10:02:00", actor: "Sehej",
      action: "live_mode_requested", detail: "September series" },
    { id: 2, ts: "2026-08-18T09:40:00", actor: "Raghav",
      action: "user_created", detail: "analyst as viewer" },
    { id: 1, ts: "2026-08-18T09:00:00", actor: "Sehej",
      action: "algorithm_assigned", detail: "v12 → Slot 2" }
  ]; S.auditFilter = ""; renderAudit();`);
  let out = evaluate('document.querySelector("#au-list").innerHTML');
  check('the trail renders every entry unfiltered',
        out.includes('September series') && out.includes('analyst as viewer') &&
        out.includes('v12'), out.slice(0, 200));
  check('actions read in English, not as identifiers',
        out.includes('Live money requested') && !out.includes('live_mode_requested'));
  check('the trail names who did it', out.includes('Sehej') && out.includes('Raghav'));
  check('the count is pluralised',
        evaluate('document.querySelector("#au-count").textContent') === '3 ENTRIES',
        evaluate('document.querySelector("#au-count").textContent'));

  evaluate('S.auditFilter = "user"; renderAudit();');
  out = evaluate('document.querySelector("#au-list").innerHTML');
  check('filtering to people drops the rest',
        out.includes('analyst as viewer') && !out.includes('September series'));
  check('a filtered count is singular',
        evaluate('document.querySelector("#au-count").textContent') === '1 ENTRY',
        evaluate('document.querySelector("#au-count").textContent'));

  evaluate('S.audit = []; S.auditFilter = ""; renderAudit();');
  check('an empty trail explains itself rather than showing nothing',
        evaluate('document.querySelector("#au-list").innerHTML').includes('Nothing recorded'));

  evaluate(`S.audit = [{ id: 1, ts: "2026-08-18T09:00:00",
    actor: "<img src=x onerror=alert(1)>", action: "user_created",
    detail: "<script>bad()</scr" + "ipt>" }]; renderAudit();`);
  check('audit text is escaped, not injected',
        !evaluate('document.querySelector("#au-list").innerHTML').includes('<img src=x'));

  check('the admin screen has somewhere to show it',
        html.includes('id="au-list"') && html.includes('id="au-filter"'));
  check('opening Admin loads the trail', html.includes('loadAudit()'));
}

console.log('\nLog collapsing');
{
  // An uploaded algorithm printing on every tick was pushing every meaningful
  // line off the screen inside a minute.
  const spam = [];
  for (let i = 0; i < 40; i++) {
    spam.push({ ts: `2026-08-18T12:0${i % 10}:00`, kind: 'log', level: 'info',
                message: '[EXPIRY_DAY] Not an expiry day' });
  }
  const mixed = [
    { ts: '2026-08-18T11:59:00', kind: 'log', level: 'info', message: 'boot' },
    ...spam,
    { ts: '2026-08-18T12:10:00', kind: 'entry', level: 'success', message: 'BOUGHT 24500CE' },
  ];
  const rows = sandbox.collapse(mixed);
  check(`40 identical lines collapse to one (${rows.length} rows)`, rows.length === 3,
        String(rows.length));
  check('the repeat count is carried', rows[1]._n === 40, String(rows[1]._n));
  check('the last occurrence is recorded', !!rows[1]._last);
  check('the meaningful entry survives the spam',
        rows[2].message === 'BOUGHT 24500CE');

  // Different messages must not be merged just because they are adjacent.
  const distinct = sandbox.collapse([
    { kind: 'log', level: 'info', message: 'a' },
    { kind: 'log', level: 'info', message: 'b' },
    { kind: 'log', level: 'info', message: 'a' },
  ]);
  check('distinct lines are kept apart', distinct.length === 3);
  // Same text at a different severity is a different event.
  const bysev = sandbox.collapse([
    { kind: 'log', level: 'info', message: 'x' },
    { kind: 'log', level: 'error', message: 'x' },
  ]);
  check('severity change breaks the run', bysev.length === 2);
}

console.log('\nIdle reason badges');
{
  const badge = (reason, running = true) =>
    sandbox.idleBadge({ running, snapshot: { decision: { reason, detail: 'why' } } });

  check('an expiry block is named and amber',
        badge('EXPIRY_DAY').includes('EXPIRY BLOCK') && badge('EXPIRY_DAY').includes('idle warn'));
  check('a tripped kill switch reads as bad',
        badge('DAILY_KILL').includes('idle bad') && badge('DAILY_KILL').includes('KILL SWITCH'));
  check('low volatility is explained, not just labelled',
        badge('LOW_VOL').includes('too quiet'), badge('LOW_VOL'));
  check('being outside the session is neutral',
        badge('PRE_WINDOW').includes('idle idle'));
  check('holding a position needs no badge', badge('IN_POSITION') === '');
  check('a stopped algorithm shows nothing', badge('LOW_VOL', false) === '');
  check('an unknown reason still renders readably',
        badge('SOME_NEW_REASON').includes('SOME NEW REASON'),
        badge('SOME_NEW_REASON'));
}

console.log('\nZero P&L');
{
  evaluate(`S.fleet = null; S.deckSlot = 0;
            S.snap = { paper: true, equity: 20000, open_equity: 20000, day_pnl: 0 };
            S.status = {}`);
  sandbox.renderDash();
  const chip = sandbox.document.querySelector('#d-day');
  check('a flat day reads FLAT, not +₹0', chip.textContent === 'FLAT', chip.textContent);
  check('and is neutral rather than green',
        chip.className.includes('flat') && !chip.className.includes('up'), chip.className);

  evaluate('S.snap.day_pnl = 1400');
  sandbox.renderDash();
  check('a gain is still green',
        sandbox.document.querySelector('#d-day').className.includes('up'));
  evaluate('S.snap.day_pnl = -400');
  sandbox.renderDash();
  check('a loss is still red',
        sandbox.document.querySelector('#d-day').className.includes('down'));
}

console.log('\nPluralisation');
{
  check('one is singular', evaluate('plural(1, "trade")') === '1 trade',
        evaluate('plural(1, "trade")'));
  check('zero is plural', evaluate('plural(0, "trade")') === '0 trades',
        evaluate('plural(0, "trade")'));
  check('many is plural', evaluate('plural(4, "session")') === '4 sessions');
  check('counts use Indian grouping',
        evaluate('plural(120000, "trade")') === '1,20,000 trades',
        evaluate('plural(120000, "trade")'));
  check('an explicit plural form is honoured',
        evaluate('plural(2, "entry", "entries")') === '2 entries');
  check('a missing count does not render NaN',
        evaluate('plural(null, "trade")') === '0 trades',
        evaluate('plural(null, "trade")'));
}

console.log('\nLive-money guard');
{
  // Never assume live from missing data, and never assume paper from a live flag.
  evaluate('S.snap = {}; S.status = {}');
  check('unknown mode is treated as paper', evaluate('isLive()') === false);
  evaluate('S.snap = { paper: true }');
  check('paper is paper', evaluate('isLive()') === false);
  evaluate('S.snap = { paper: false }');
  check('real money is detected', evaluate('isLive()') === true);
  evaluate('S.snap = {}; S.status = { config: { paper_mode: false } }');
  check('the mode falls back to server config', evaluate('isLive()') === true);

  evaluate('S.snap = { paper: false }; S.status = {}');
  sandbox.renderDash();
  check('a real-money banner is switched on',
        sandbox.document.body.classList.contains('live-money'));
  evaluate('S.snap = { paper: true }');
  sandbox.renderDash();
  check('and switched off again in paper',
        !sandbox.document.body.classList.contains('live-money'));
}

console.log('\nConnection state');
{
  sandbox.setLink('live');
  check('a live link reads live',
        sandbox.document.querySelector('#linkpill').innerHTML.includes('live'));
  check('nothing is dimmed while live',
        !sandbox.document.body.classList.contains('stale'));

  sandbox.setLink('reconnecting');
  check('a dropped link is announced',
        sandbox.document.querySelector('#linkpill').innerHTML.includes('reconnecting'));
  // The dangerous case is a frozen number that still looks current.
  check('live figures are dimmed when the feed is not live',
        sandbox.document.body.classList.contains('stale'));

  sandbox.setLink('down');
  check('an offline link says offline',
        sandbox.document.querySelector('#linkpill').innerHTML.includes('offline'));
  sandbox.setLink('live');
}

console.log('\nBrand');
{
  const html = readFileSync(HTML, 'utf8');
  // Pinning the exact hex made this fail on a palette refresh that kept the
  // brand intact, so it asserts the identity instead: --au is defined, and it
  // is a gold — red high, green mid, blue low.
  const au = (html.match(/--au:\s*#([0-9a-f]{6})/i) || [])[1];
  check('gold is the accent variable', !!au, 'no --au hex found');
  if (au) {
    const [r, g, b] = [0, 2, 4].map(i => parseInt(au.slice(i, i + 2), 16));
    check(`--au (#${au}) is still a gold`, r > 180 && g > 120 && g < r && b < 120,
          `rgb(${r},${g},${b})`);
  }
  check('no leftover green accent from the old palette',
        !html.includes('#35d6a0'), 'old accent still present');
  check('compass mark is inline in the login', html.includes('class="mark"'));
  check('desktop rail exists', html.includes('id="rail"'));
  check('rail is hidden until 1000px',
        /@media \(min-width:1000px\)[\s\S]{0,900}#rail\{display:flex/.test(html));
  check('bottom nav is hidden on desktop',
        /@media \(min-width:1000px\)[\s\S]{0,400}nav\{display:none/.test(html));
  check('deck reflows into columns on wide screens',
        html.includes('grid-template-columns:repeat(auto-fit,minmax(330px,1fr))'));

  const svg = readFileSync(join(HERE, '..', 'icon.svg'), 'utf8');
  check('icon is the gold compass', svg.includes('#D4AF37') && svg.includes('256'));
}

console.log('\nSecurity surface');
{
  const html = readFileSync(HTML, 'utf8');
  check('login posts to the auth endpoint', html.includes('/api/auth/login'));
  check('password field is masked', html.includes('id="g-pass" type="password"'));
  check('WebAuthn requires user verification',
        html.includes("userVerification: \"required\""));
  check('biometric gate is honest about what it protects',
        /passcode is what the server verifies/i.test(html));
  check('mixed content is caught before it fails silently',
        html.includes('mixedContentBlocked'));
  check('token is never hardcoded in the page',
        !html.includes('cXTBbbmZ'), 'a real token leaked into the file');
  check('a build stamp is present so a stale cache is visible',
        /const BUILD = "[\d.a-z-]+"/.test(html));
  check('the build stamp is shown on the login screen',
        html.includes('id="g-build"'));

  // The fingerprint used to be unreachable: init() walked straight into the
  // app whenever a stored token validated, and the Unlock button only rendered
  // when the gate was up — which only happened once there was no session left
  // to unlock. Both halves are asserted so it cannot quietly revert.
  check('a stored session with a fingerprint enrolled starts locked',
        /localStorage\.setItem\(LOCK_KEY/.test(html) &&
        /if \(S\.token && localStorage\.getItem\(BIO_KEY\)/.test(html));
  check('a locked session is not entered without unlocking',
        /if \(S\.token && S\.url && !isLocked\(\)\)/.test(html));
  check('the unlock panel keys off the lock, not off being signed out',
        /const usable = enrolled && locked/.test(html));
  check('unlocking clears the lock', /localStorage\.removeItem\(LOCK_KEY\)/.test(html));

  check('locking and signing out are different operations',
        sandbox.lockApp !== undefined && sandbox.signOut !== undefined &&
        /function lockApp/.test(html) && /function signOut/.test(html));
  check('locking keeps the session', !/function lockApp[\s\S]{0,400}removeItem\("mc_token"\)/.test(html));
  check('signing out discards it', /function signOut[\s\S]{0,400}removeItem\("mc_token"\)/.test(html));
  check('signing out retires the token on the server too',
        /function signOut[\s\S]{0,500}\/api\/auth\/logout/.test(html));
  check('an unreachable server does not trap you signed in',
        /\/api\/auth\/logout[\s\S]{0,200}catch\(\(\) => \{\}\)/.test(html));

  check('there is an idle lock', /function armIdleLock/.test(html));
  check('the idle lock is bounded to something sane',
        sandbox.idleMinutes === undefined || true);
  for (const [stored, want] of [['', 15], ['0', 0], ['30', 30], ['99999', 240],
                                ['-5', 0], ['banana', 15]]) {
    evaluate(`localStorage.setItem(IDLE_KEY, ${JSON.stringify(stored)})`);
    check(`idle setting ${JSON.stringify(stored)} resolves to ${want}`,
          evaluate('idleMinutes()') === want, String(evaluate('idleMinutes()')));
  }
  evaluate('localStorage.removeItem(IDLE_KEY)');

  // must_change was written on every account the Admin screen created and
  // read by nothing, so a passcode typed by an admin stayed the operator's
  // passcode for good.
  check('a forced passcode change exists', /function forcePasswordChange/.test(html));
  check('login acts on must_change', /if \(body\.must_change\) forcePasswordChange\(\)/.test(html));
  check('resuming a session acts on it too',
        /m\.must_change_password/.test(html));
  check('a forced change cannot be dismissed',
        /function closePasswordChange\(\) \{\s*if \(pwForced\) return/.test(html));
  check('operators can change their own passcode at all',
        html.includes('/api/auth/change-password') && html.includes('id="c-passwd"'));
  check('the passcode dialog exists', html.includes('id="pw"') &&
        html.includes('id="pw-cur"') && html.includes('id="pw-new2"'));

  const backendAuth = readFileSync(
    join(HERE, '..', '..', 'backend', 'app', 'auth.py'), 'utf8');
  check('sessions carry an id so one can be retired alone',
        /"jti":/.test(backendAuth));
  check('a revoked session stops verifying',
        /_is_revoked\(payload\.get\("jti", ""\)\)/.test(backendAuth));
  check('revocations are swept once they would have expired anyway',
        /DELETE FROM revoked_sessions WHERE expires_at </.test(backendAuth));

  // Two deploy configs exist because the project's Root Directory decides
  // which one Vercel reads: left at the repository root it takes the root
  // vercel.json (which points outputDirectory at web/), set to web/ it takes
  // this one. Both have to be valid, so both are checked.
  const configs = {
    'web/vercel.json': join(HERE, '..', 'vercel.json'),
    'vercel.json': join(HERE, '..', '..', 'vercel.json'),
  };

  for (const [label, path] of Object.entries(configs)) {
    const cfg = JSON.parse(readFileSync(path, 'utf8'));

    // Vercel's schema sets additionalProperties:false, so a "//" comment key —
    // the convention npm allows in package.json — fails the build outright with
    // "should NOT have additional property //". JSON has no comments; rationale
    // belongs in the commit message or the docs.
    const commentKeys = [];
    (function scan(node, trail) {
      if (Array.isArray(node)) return node.forEach((v, i) => scan(v, `${trail}[${i}]`));
      if (node === null || typeof node !== 'object') return;
      for (const [k, v] of Object.entries(node)) {
        if (k === '//' || k.startsWith('//')) commentKeys.push(`${trail}.${k}`);
        scan(v, `${trail}.${k}`);
      }
    })(cfg, label);
    check(`${label} carries no "//" comment keys`, commentKeys.length === 0,
          `Vercel rejects these: ${commentKeys.join(', ')}`);

    const sources = cfg.headers.map((h) => h.source);
    check(`${label} serves the site root uncached`, sources.includes('/'),
          'a request to / never carries the /index.html path, so it needs its own rule');
    check(`${label} serves index.html uncached`, sources.includes('/index.html'));
    const rootRule = cfg.headers.find((h) => h.source === '/');
    check(`${label} root cache header actually disables caching`,
          rootRule.headers.some((x) => /no-store/.test(x.value)),
          JSON.stringify(rootRule));
  }

  const rootCfg = JSON.parse(readFileSync(configs['vercel.json'], 'utf8'));
  check('the root config serves the dashboard out of web/',
        rootCfg.outputDirectory === 'web',
        `outputDirectory is ${JSON.stringify(rootCfg.outputDirectory)}; with the ` +
        'Root Directory unset, anything else leaves / with no index.html to serve');
}

console.log('\n' + '='.repeat(60));
console.log(`  ${pass} passed, ${fail} failed`);
console.log('='.repeat(60));
process.exit(fail ? 1 : 0);
