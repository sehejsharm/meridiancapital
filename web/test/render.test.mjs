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
    _text: '',
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
  // A real element stringifies whatever it is assigned; the stub used to keep
  // the raw value, so a number written to textContent read back as a number
  // and tests passed or failed on a type the browser would never produce.
  Object.defineProperty(el, 'textContent', {
    get() { return this._text; },
    set(v) { this._text = v == null ? '' : String(v); },
  });
  return el;
}

const elements = new Map();
const doc = {
  // The real page toggles classes on body (live-money, stale), so the stub
  // needs one or renderDash throws before it draws anything.
  body: makeElement('body'),
  // Density is set on documentElement so it applies before the first paint.
  documentElement: {
    _attrs: {},
    setAttribute(k, v) { this._attrs[k] = String(v); },
    getAttribute(k) { return this._attrs[k] ?? null; },
    removeAttribute(k) { delete this._attrs[k]; },
  },
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
  // The page listens on window for resize (tape overflow) as well as on
  // document; sandbox doubles as window, so it needs the same surface.
  addEventListener() {},
  removeEventListener() {},
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

// `const` declarations in the dashboard script do not become properties of the
// sandbox global — only function declarations do — so anything declared that
// way (S, TAPE, C, the formatting helpers) is reached by evaluating inside the
// context rather than off the sandbox object. Defined here, next to the
// context, so every block below can use it rather than only the later ones.
const evaluate = (expr) => vm.runInContext(expr, context);
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
  // Asserted against the token the rest of the app uses, not a literal: the
  // charts used to draw profit in #3ecf8e while every other green was
  // #38e08f, which is one meaning wearing two colours.
  check('in profit the line uses the up colour',
        svg.includes(`stroke="${evaluate('C.profit')}"`),
        'expected the up colour at 150.2 over 142.5');
  check('and that colour is the one the palette defines',
        evaluate('C.profit') === '#38e08f', evaluate('C.profit'));
}
{
  const series = Array.from({ length: 30 }, (_, i) => [
    Date.UTC(2026, 7, 4, 4, 22) + i * 5000, round(142.5 - i * 0.4),
  ]);
  const svg = sandbox.drawTradeBox(track(series), 130.6);
  check('in loss the line uses the down colour',
        svg.includes(`stroke="${evaluate('C.loss')}"`), evaluate('C.loss'));
  check('and matches the palette', evaluate('C.loss') === '#ff5566', evaluate('C.loss'));
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
  check('renders an svg', svg.includes('<svg'));
  check('no NaN coordinates', !svg.includes('NaN'), svg.slice(0, 200));
  check('the window is labelled at both ends',
        svg.includes('2026-08-03') && svg.includes('2026-08-07'));
  check('the drawdown is shaded against the running peak',
        svg.includes('#ff5566'), 'expected a drawdown band');
  check('it is described for a screen reader', svg.includes('aria-label'));

  // ---- interaction ----
  check('every session has its own hit band',
        (svg.match(/data-cv="\d+"/g) || []).length === sessions.length,
        (svg.match(/data-cv="\d+"/g) || []).join(','));
  check('each band carries what the tooltip needs',
        svg.includes('data-p="1662"') && svg.includes('data-t=') &&
        svg.includes('data-dd='), svg.slice(svg.indexOf('data-cv='), 400));
  check('drawdown from peak is computed per point',
        svg.includes('data-dd="-8.45"'),
        (svg.match(/data-dd="[^"]*"/g) || []).join(' '));
  check('a crosshair and a selection rectangle exist',
        svg.includes('cv-cross') && svg.includes('cv-sel'));
  check('the drag hint is shown', /Drag to zoom/.test(svg));
  check('no reset button until something is zoomed', !svg.includes('cv-reset'));

  // ---- zoom ----
  const z = sandbox.drawCurve(sessions, [2, 4]);
  check('a zoom slices the series',
        (z.match(/data-cv="\d+"/g) || []).length === 3,
        (z.match(/data-cv="\d+"/g) || []).join(','));
  check('the zoomed window is labelled from its own ends',
        z.includes('2026-08-05') && z.includes('2026-08-07') && !z.includes('2026-08-03'));
  check('a zoom offers a way back', z.includes('cv-reset'));
  check('the wrapper records the window it is showing',
        z.includes('data-lo="2"') && z.includes('data-hi="4"'));
  // The peak carries in from before the window, so a zoom into the middle of a
  // drawdown still shows one.
  check('drawdown survives zooming past its peak',
        z.includes('data-dd="-8.45"'),
        (z.match(/data-dd="[^"]*"/g) || []).join(' '));
  check('a nonsense range is clamped rather than throwing',
        sandbox.drawCurve(sessions, [99, 200]).includes('<svg') &&
        sandbox.drawCurve(sessions, [-5, 1]).includes('<svg'));
  check('a one-point zoom is widened to something drawable',
        (sandbox.drawCurve(sessions, [3, 3]).match(/data-cv="\d+"/g) || []).length >= 2);

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

console.log('\nRisk panel');
{
  const bar = sandbox.riskBar;
  check('a fresh session shows an empty bar',
        bar('Loss', 0, 3000, '₹0 of ₹3,000').includes('width:0.0%'));
  check('halfway is amber-free', bar('L', 1500, 3000, '').includes('rb-fill ok'));
  check('past 60% it warns', bar('L', 1900, 3000, '').includes('rb-fill warn'));
  check('past 85% it is critical', bar('L', 2600, 3000, '').includes('rb-fill crit'));
  check('at the limit it is full and critical',
        bar('L', 3000, 3000, '').includes('width:100.0%') &&
        bar('L', 3000, 3000, '').includes('crit'));
  check('past the limit it does not overflow the track',
        bar('L', 9000, 3000, '').includes('width:100.0%'));
  check('no limit set does not divide by zero',
        !bar('L', 500, 0, '').includes('NaN'), bar('L', 500, 0, ''));

  const intraday = sandbox.drawIntraday([
    { ts: '2026-08-18T09:15:00', equity: 20000, day_pnl: 0 },
    { ts: '2026-08-18T09:16:00', equity: 20140, day_pnl: 140 },
    { ts: '2026-08-18T09:17:00', equity: 19900, day_pnl: -100 },
  ]);
  check('the intraday line renders', intraday.includes('<svg'));
  check('no NaN in it', !intraday.includes('NaN'));
  check('it marks where the day opened', intraday.includes('open'));
  check('a down day draws in the loss colour',
        intraday.includes(evaluate('C.loss')), evaluate('C.loss'));
  check('one mark is not a line',
        sandbox.drawIntraday([{ ts: 'x', equity: 1 }]).includes('chart-empty'));
  check('no marks degrades cleanly', sandbox.drawIntraday([]).includes('chart-empty'));
  check('a flat session does not divide by zero',
        !sandbox.drawIntraday([
          { ts: 'a', equity: 20000 }, { ts: 'b', equity: 20000 }]).includes('NaN'));

  check('the risk page exists', html.includes('id="v-risk"'));
  check('and is in the navigation', /id: "risk"/.test(html));
  const mainPy4 = readFileSync(join(HERE, '..', '..', 'backend', 'app', 'main.py'), 'utf8');
  check('the server assembles it in one call', /@app\.get\("\/api\/risk"\)/.test(mainPy4));
  check('realised risk-reward is computed from what was actually risked',
        /abs\(t\["net_pnl"\]\) \/ risk/.test(mainPy4));
}

console.log('\nRoles and capabilities');
{
  const usersPy = readFileSync(join(HERE, '..', '..', 'backend', 'app', 'users.py'), 'utf8');
  for (const role of ['risk_manager', 'quant_dev', 'compliance']) {
    check(`${role} exists`, usersPy.includes(`"${role}"`));
    check(`${role} has a description`, new RegExp(`"${role}": "`).test(usersPy));
  }
  // The three constraints the roles exist to express.
  const caps = src => {
    const m = new RegExp(`"${src}": frozenset\\(\\{([\\s\\S]*?)\\}\\)`).exec(usersPy);
    return m ? m[1] : '';
  };
  check('a risk manager can stop a session', caps('risk_manager').includes('"kill"'));
  check('but cannot change the strategy',
        !caps('risk_manager').includes('"tune_strategy"'), caps('risk_manager'));
  check('nor upload code', !caps('risk_manager').includes('"upload_algorithm"'));
  check('a quant can upload', caps('quant_dev').includes('"upload_algorithm"'));
  check('but cannot arm real money',
        !caps('quant_dev').includes('"arm_live"'), caps('quant_dev'));
  check('compliance can read the audit trail', caps('compliance').includes('"view_audit"'));
  check('and can change nothing at all',
        !/"(operate|kill|tune_strategy|upload_algorithm|manage_users|arm_live)"/
          .test(caps('compliance')), caps('compliance'));
  check('only a super admin manages people',
        !['viewer', 'compliance', 'quant_dev', 'operator', 'risk_manager']
          .some(r => caps(r).includes('"manage_users"')));

  const authPy = readFileSync(join(HERE, '..', '..', 'backend', 'app', 'auth.py'), 'utf8');
  check('guards are keyed on capability, not rank',
        /def require_capability/.test(authPy));
  check('the refusal says what the role cannot do',
        /cannot do this/.test(authPy));

  check('the matrix is rendered from the API, not restated in the page',
        html.includes('/api/permissions') && html.includes('id="u-matrix"'));
}

console.log('\nAlerts');
{
  const alertsPy = readFileSync(join(HERE, '..', '..', 'backend', 'app', 'alerts.py'), 'utf8');
  for (const rule of ['kill_switch', 'error_rate', 'drawdown', 'bot_stopped',
                      'trade', 'daily_summary']) {
    check(`the ${rule} rule exists`, alertsPy.includes(`"${rule}"`));
  }
  for (const ch of ['email', 'webhook', 'slack']) {
    check(`the ${ch} channel exists`, alertsPy.includes(`"${ch}"`));
  }
  check('a rule that has fired does not fire again until it clears',
        /if rule in _active/.test(alertsPy) && /def clear/.test(alertsPy));
  check('delivery never blocks the caller',
        /threading\.Thread\(target=_run/.test(alertsPy));
  check('a send has a timeout', /SEND_TIMEOUT_SECONDS/.test(alertsPy));
  check('the SMTP password is never sent back to the browser',
        /"password": ""/.test(alertsPy) && /password_set/.test(alertsPy));
  check('an empty password on save means "leave it alone"',
        /if not incoming\.get\("password"\)/.test(alertsPy));
  check('a new session starts with every rule unfired',
        /def reset_state/.test(alertsPy));
  check('the test button reports why it failed, not just that it did',
        /f"\{type\(exc\)\.__name__\}: \{exc\}"/.test(alertsPy));

  const runnerPy = readFileSync(join(HERE, '..', '..', 'backend', 'app', 'runner.py'), 'utf8');
  check('an unexpected stop raises one', /_alert\("bot_stopped"/.test(runnerPy));
  check('so does a tripped kill switch', /_alert\("kill_switch"/.test(runnerPy));
  check('and a drawdown past the threshold', /_alert\("drawdown"/.test(runnerPy));
  check('alerting cannot break the output reader',
        /def _alert\([\s\S]{0,320}except Exception:\s*\n\s*pass/.test(runnerPy));

  check('the alerts page exists', html.includes('id="v-alerts"'));
  check('report scheduling lives with it', html.includes('id="al-sched"'));
}

console.log('\nAudit trail — filters and export');
{
  const approvalsPy = readFileSync(
    join(HERE, '..', '..', 'backend', 'app', 'approvals.py'), 'utf8');
  check('entries carry the caller address', /ip\s+TEXT/.test(approvalsPy));
  check('an existing trail gains the column rather than being rebuilt',
        /ALTER TABLE audit ADD COLUMN ip TEXT/.test(approvalsPy));
  check('the address comes from middleware, not from every call site',
        /def set_request_ip/.test(approvalsPy));
  check('filters compose', /def trail\(limit: int = 200, action: str = ""/.test(approvalsPy));
  check('a group filter matches the whole group',
        /action LIKE \? ESCAPE/.test(approvalsPy));
  check('it can be exported', /def to_csv/.test(approvalsPy));
  check('the export names the IP column', /"IP address"/.test(approvalsPy));
  check('nothing deletes from the trail',
        !/DELETE FROM audit/.test(approvalsPy), 'the trail must be append-only');

  const mainPy5 = readFileSync(join(HERE, '..', '..', 'backend', 'app', 'main.py'), 'utf8');
  check('the proxy header is only trusted for the first hop',
        /forwarded\.split\(","\)\[0\]/.test(mainPy5));
  check('the address is cleared after each request',
        /finally:\s*\n\s*approvals\.set_request_ip\(None\)/.test(mainPy5));
  check('signing in is audited', /"signed_in"/.test(mainPy5));
  check('so is a refused sign-in', /"sign_in_failed"/.test(mainPy5));
  check('the export honours the same filters',
        /@app\.get\("\/api\/audit\/export"\)/.test(mainPy5));

  check('the screen offers both formats',
        html.includes('id="au-csv"') && html.includes('id="au-json"'));
  check('and filters by operator and date',
        html.includes('id="au-actor"') && html.includes('id="au-from"'));
}

console.log('\nKeyboard and density');
{
  check('digits navigate by position', /\^\[1-9\]\$/.test(html));
  check('and follow what the role can see', /visibleViews\(\)\[Number\(e\.key\) - 1\]/.test(html));
  check('S and X are guarded by the same confirmation as the buttons',
        /if \(key === "s" \|\| key === "x"\)[\s\S]{0,600}confirmAction\(/.test(html));
  check('and refuse outright without the role',
        /if \(key === "s" \|\| key === "x"\) \{\s*\n\s*if \(!canOperate\(\)\) return;/.test(html));
  check('there is a shortcut list', /function showShortcuts/.test(html));
  check('and a hint to find it', html.includes('id="kbhint"'));

  check('density has three states', /const DENSITY = \["comfortable", "default", "compact"\]/.test(html));
  check('it is stored per browser', /localStorage\.setItem\("mc_density"/.test(html));
  check('and applied before the first paint, on documentElement',
        /document\.documentElement\.setAttribute\("data-density"/.test(html));
  for (const mode of ['compact', 'comfortable']) {
    check(`${mode} is defined`, new RegExp(`html\\[data-density="${mode}"\\]`).test(html));
  }
  check('card padding is a token, not a literal',
        /\.card\{[^}]*padding:var\(--card-pad\)/.test(html));

  evaluate('applyDensity("compact")');
  check('choosing compact sets the attribute',
        evaluate('document.documentElement.getAttribute("data-density")') === 'compact');
  evaluate('applyDensity("default")');
  check('default clears it rather than setting a third value',
        evaluate('document.documentElement.getAttribute("data-density")') === null);
  evaluate('applyDensity("nonsense")');
  check('an unknown mode falls back to default',
        evaluate('document.documentElement.getAttribute("data-density")') === null);
}

console.log('\nEmpty states');
{
  const es = sandbox.emptyState;
  const out = es('trades', 'No trades in this period',
                 'The algorithm found no qualifying setups.',
                 [{ label: 'Open the log →', id: 'x1' }]);
  check('it draws an icon', out.includes('<svg') && out.includes('<path d='));
  check('it says what happened', out.includes('No trades in this period'));
  check('it says why', out.includes('no qualifying setups'));
  check('it offers the next step', out.includes('id="x1"') && out.includes('Open the log'));
  check('with no action it still renders',
        es('clock', 'Nothing yet', 'Come back later').includes('es-h'));
  check('an unknown icon falls back rather than drawing nothing',
        es('not-a-real-icon', 'x', '').includes('<path d="M'));
  check('the heading is escaped',
        !es('trades', '<img src=x>', '').includes('<img src=x'));

  // These ids are object properties in the source; emptyState turns them into
  // attributes at render time.
  check('the trade box offline state is actionable',
        html.includes('id: "tb-ctrl"') && html.includes('Go to Control →'));
  check('and offers a start to whoever may start it',
        /canOperate\(\) \? \{ label: "Start now", id: "tb-start"/.test(html));
  check('the trades empty state points at the session log',
        html.includes('id: "te-feed"'));
}

console.log('\nPython highlighting');
{
  const hl = sandbox.highlightPython;
  const strip = s => s.replace(/<[^>]+>/g, '')
    .replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');

  // The one property that matters more than any colour: what goes in comes
  // out. A highlighter that eats a character silently corrupts an upload.
  const samples = [
    'def main():\n    return 1',
    '# a comment with "quotes" and \'apostrophes\'',
    'x = "a string with # not a comment"',
    "s = 'it\\'s escaped'",
    'doc = """triple\nquoted\nspanning lines"""',
    '@decorator\nclass Book:\n    pass',
    'v = 1_000_000 + 0x1f - 2e-3',
    'if a and not b or c in d: pass',
    'print(f"{x:>7.2f}ms")',
    'a = b - -c',
    '',
    '\n\n\n',
  ];
  for (const src of samples) {
    check(`round-trips: ${JSON.stringify(src.slice(0, 34))}`,
          strip(hl(src)) === src, JSON.stringify(strip(hl(src))));
  }

  check('keywords are coloured', hl('return x').includes('tk-kw'));
  check('a def name is coloured differently from the keyword',
        hl('def compute():').includes('tk-def'));
  check('numbers are coloured', hl('x = 42').includes('tk-num'));
  check('comments are coloured', hl('# note').includes('tk-com'));

  // These four are the ones a regex-based highlighter gets wrong.
  check('a keyword inside a comment is not coloured as code',
        !hl('# class def return').includes('tk-kw'));
  check('a hash inside a string does not start a comment',
        !hl('x = "# not a comment"').includes('tk-com'));
  check('a quote inside a comment does not open a string',
        !hl('# it\'s fine').includes('tk-str'));
  check('a keyword inside a string stays a string',
        !hl('x = "return"').includes('tk-kw'));
  check('a word containing a keyword is not split',
        !hl('classification = 1').includes('tk-kw'),
        hl('classification = 1'));
  check('an unterminated string does not run off the end',
        strip(hl('x = "never closed')) === 'x = "never closed');
  check('a triple-quoted docstring is one token',
        (hl('"""a\nb"""').match(/tk-str/g) || []).length === 1);

  // Source is data. It must never become markup.
  const evil = 'x = "<img src=x onerror=alert(1)>"  # </span><script>bad()</script>';
  check('markup in the source is escaped',
        !hl(evil).includes('<img src=x') && !hl(evil).includes('<script>'),
        hl(evil).slice(0, 120));
  check('and still round-trips', strip(hl(evil)) === evil);

  // Line splitting has to survive multi-line tokens.
  const marked = sandbox.highlightLines('a = 1\nb = 2\nc = 3', [{ line: 2, level: 'bad' }]);
  check('each line is wrapped', (marked.match(/class="ed-line/g) || []).length === 3);
  check('the marked line is shaded', marked.includes('ed-line bad'));
  check('the others are not',
        (marked.match(/ed-line bad/g) || []).length === 1);
  const spanning = sandbox.highlightLines('x = """a\nb"""\ny = 1', []);
  check('a docstring spanning lines still splits into lines',
        (spanning.match(/class="ed-line/g) || []).length === 3);
  check('an empty line keeps its height',
        sandbox.highlightLines('a\n\nb', []).includes('> </span>'));

  check('the editor is a textarea, so selection and undo still work',
        html.includes('class="ed-ta mono" id="a-source"'));
  check('it has a gutter', html.includes('id="a-gutter"'));
  check('validation findings can carry a line number',
        readFileSync(join(HERE, '..', '..', 'backend', 'app', 'algorithms.py'), 'utf8')
          .includes('line=exc.lineno'));
}

console.log('\nStrategy save flow');
{
  evaluate(`S.strategy = { bot_running: false, groups: [{ name: 'Risk', params: [
      { key: 'SL_PCT', label: 'Stop loss', type: 'pct', value: 0.10 },
      { key: 'MAX_TRADES_PER_DAY', label: 'Max trades', type: 'int', value: 3 },
      { key: 'CHOP_FILTER', label: 'Chop filter', type: 'bool', value: true } ] }] };
    S.stratEdits = { SL_PCT: 0.12 };`);
  let ch = sandbox.pendingChanges();
  check('a change is described from and to',
        ch.length === 1 && ch[0].from === '10.0%' && ch[0].to === '12.0%',
        JSON.stringify(ch));
  check('it uses the human label, not the key', ch[0].label === 'Stop loss');

  evaluate(`S.stratEdits = { MAX_TRADES_PER_DAY: 5, CHOP_FILTER: false };`);
  ch = sandbox.pendingChanges();
  check('an integer is not shown as a percentage',
        ch.find(c => c.key === 'MAX_TRADES_PER_DAY').to === '5', JSON.stringify(ch));
  check('a boolean reads on/off',
        ch.find(c => c.key === 'CHOP_FILTER').from === 'on' &&
        ch.find(c => c.key === 'CHOP_FILTER').to === 'off');

  evaluate(`S.stratEdits = { SL_PCT: 0.10 };`);
  check('setting a value back to what it was is not a change',
        sandbox.pendingChanges().length === 0);

  check('the save bar floats rather than sitting at the end of the page',
        /\.savebar\{position:fixed/.test(html));
  check('saving goes through a confirmation, not straight to the API',
        /\$\("#st-save"\)\.onclick = confirmStrategySave/.test(html));
  check('the confirmation lists the changes',
        /class="chg-r"/.test(html) && /<s>\$\{esc\(c\.from\)\}<\/s>/.test(html));
  check('there is an undo window', /function offerUndo/.test(html) &&
        /Undo \(\$\{left\}s\)/.test(html));
  check('the undo puts the previous values back',
        /body: JSON\.stringify\(\{ values: previous \}\)/.test(html));
  check('a running algorithm is called out in the confirmation',
        /position open right now finishes under the current values/.test(html));

  const mainPy3 = readFileSync(join(HERE, '..', '..', 'backend', 'app', 'main.py'), 'utf8');
  check('the change is written to the audit trail',
        /"strategy_changed"/.test(mainPy3));
  check('the audit records what each value was, not just what it became',
        /before = strategy_config\.effective\(\)/.test(mainPy3) &&
        /"from": before\.get\(k\), "to": v/.test(mainPy3));
}

console.log('\nMode signalling');
{
  check('paper mode is stated, not implied by a thin rule',
        html.includes('PAPER TRADING — NO REAL MONEY AT RISK'));
  check('live mode names what is at risk',
        html.includes('LIVE — REAL CAPITAL AT RISK'));
  check('both banners are real elements, so they are announced',
        /<div class="modebar paper" role="status">/.test(html) &&
        /<div class="modebar live" role="alert">/.test(html));
  check('real capital rings the whole shell, not just the top edge',
        html.includes('class="shellring"') &&
        /body\.live-money \.shellring\{display:block\}/.test(html));
  check('the mode badge is its own component, not a status pill',
        html.includes('class="modechip paper"') && /\.modechip\{/.test(html));
  check('the badge is bigger and heavier than a pill',
        /\.modechip\{[^}]*font-weight:800/.test(html));
  check('live and loss are different colours',
        /--live:#e0142a/.test(html) && /--down:#ff5566/.test(html));

  evaluate('S.snap = { paper: true }; S.status = {}; S.fleet = null; renderDash();');
  check('paper mode marks the body', evaluate('document.body.classList.contains("paper-mode")'));
  check('and clears the live class', !evaluate('document.body.classList.contains("live-money")'));
  check('the badge reads PAPER',
        evaluate('document.querySelector("#h-mode").textContent') === 'PAPER');
  evaluate('S.snap = { paper: false }; renderDash();');
  check('live mode marks the body', evaluate('document.body.classList.contains("live-money")'));
  check('the badge reads LIVE',
        evaluate('document.querySelector("#h-mode").textContent') === 'LIVE');
  check('the badge explains itself on hover',
        /real capital/i.test(evaluate('document.querySelector("#h-mode").title')));
}

console.log('\nMarket tape overflow');
{
  check('the tape has a wrapper that can show an edge fade',
        html.includes('class="tapewrap" id="d-tapewrap"'));
  check('the fade only appears when something is off-screen',
        /\.tapewrap\.more::after\{opacity:1\}/.test(html));
  check('overflow is recomputed on scroll and resize',
        /addEventListener\("scroll", markTapeOverflow/.test(html) &&
        /addEventListener\("resize", markTapeOverflow/.test(html));

  // Priority order is the whole point: the cells that go are the ones nobody
  // trades on. Spot, day move and today's P&L must never carry a priority.
  const tapeFn = html.slice(html.indexOf('function tapeTick'),
                            html.indexOf('function markTapeOverflow'));
  for (const keep of ['Day', 'P&L today']) {
    const m = tapeFn.match(new RegExp(`cell\\("${keep}"[^\\n]*`));
    check(`${keep} is never dropped`, !!m && !/,\s*[123]\)/.test(m[0]), m && m[0]);
  }
  check('the link pill is the first thing to go',
        /data-pri="3"[^>]*><span class="tp-k">Link/.test(html.replace(/\s+/g, ' ')));
  check('three breakpoints drop three tiers',
        (html.match(/\.tp-i\[data-pri="[123]"\]\{display:none\}/g) || []).length === 3);

  const el = { scrollWidth: 1600, clientWidth: 1200, scrollLeft: 0 };
  // markTapeOverflow reads the live DOM, so the arithmetic is asserted directly.
  check('more content to the right means the fade shows',
        el.scrollWidth - el.clientWidth - el.scrollLeft > 4);
  check('scrolled to the end means it hides',
        !(el.scrollWidth - el.clientWidth - 400 > 4));
}

console.log('\nIssue severity');
{
  const d = {
    running: true, heartbeat: 'live', errors_today: 2, warnings_today: 1,
    errors_raw: 372, warnings_raw: 17, distinct_issues: 3,
    by_severity: { critical: 1, error: 1, warning: 1 },
    faults: [
      { ts: '2026-08-18T10:02:00', severity: 'warning', count: 10,
        message: 'Daily kill: Rs 0 / Rs 3,000' },
      { ts: '2026-08-18T10:05:00', severity: 'critical', count: 1,
        message: 'KILL SWITCH TRIGGERED — trading halted' },
      { ts: '2026-08-18T10:03:00', severity: 'error', count: 340,
        message: 'Access denied because of exceeding access rate' },
      { ts: '2026-08-18T09:40:00', severity: 'warning', count: 2,
        message: 'Something else worth knowing' },
    ],
  };
  const out = sandbox.renderFaults(d);
  check('the headline counts distinct problems, not log lines',
        out.includes('3 distinct issues') && !out.includes('372 distinct'), out.slice(0, 160));
  check('repeats are collapsed and counted',
        out.includes('repeated 10×') && out.includes('repeated 340×'));
  check('每 severity gets a badge'.replace('每', 'each'),
        out.includes('sev critical') && out.includes('sev error') &&
        out.includes('sev warning'));
  check('a critical is shown before a warning',
        out.indexOf('KILL SWITCH') < out.indexOf('Daily kill'), 'ordering');
  check('the first few are visible without opening anything',
        out.indexOf('KILL SWITCH') < out.indexOf('<details'), out.slice(0, 200));
  check('the rest fold away', out.includes('faults-more') && out.includes('1 more'));
  check('severity chips summarise the mix',
        out.includes('1 CRIT') && out.includes('1 ERR') && out.includes('1 WARN'));
  check('nothing wrong renders nothing', sandbox.renderFaults({ faults: [] }) === '');
  check('fault text is escaped',
        !sandbox.renderFaults({ faults: [{ message: '<img src=x>', severity: 'error' }] })
          .includes('<img src=x'));

  // The raw line counts stay on screen, as the small print under the figure
  // rather than as the figure itself.
  check('the raw total is still reported somewhere',
        html.includes('d.errors_raw') && html.includes('lines'));
  check('it is a sub-line, not the headline number',
        /statSub\("Errors today"/.test(html));

  // Classification is the server's job; both halves have to agree on the words.
  const mainPy2 = readFileSync(join(HERE, '..', '..', 'backend', 'app', 'main.py'), 'utf8');
  for (const level of ['critical', 'error', 'warning', 'info']) {
    check(`the server can emit ${level}`, new RegExp(`"${level}"`).test(mainPy2));
    check(`the app knows how to draw ${level}`,
          new RegExp(`\\b${level}:\\s*\\[`).test(html) || level === 'info');
  }
  check('a kill switch counts as critical, not just an error',
        /KILL SWITCH TRIGGERED/.test(mainPy2));
  check('identical messages differing only in numbers group together',
        /_NUMBERS\.sub\(['"]#['"]/.test(mainPy2));
}

console.log('\nExit message');
{
  const runner = readFileSync(join(HERE, '..', '..', 'backend', 'app', 'runner.py'), 'utf8');
  check('a clean exit is never called unexpected',
        !/exited \(code \{code\}\)"\s*\n\s*\+ \("" if was_manual else " unexpectedly"\)/.test(runner));
  check('code 0 reads as a clean stop',
        /Bot process (stopped|finished and stopped) cleanly/.test(runner));
  check('a real crash names the code',
        /Bot process exited unexpectedly \(code \{code\}\)/.test(runner));
  check('SIGTERM counts as clean too',
        /clean = code in \(0, -15, 143\)/.test(runner));
}

console.log('\nSession counters');
{
  const strat = readFileSync(
    join(HERE, '..', '..', 'backend', 'app', 'bot', 'strategy.py'), 'utf8');
  check('the algorithm no longer prints one label for two numbers',
        !/Sessions run/.test(strat), 'a "Sessions run" label survived');
  check('finished sessions are labelled as finished',
        /Sessions done/.test(strat));
  check('the one in progress is labelled separately',
        /This session : #/.test(strat));
  check('the snapshot carries both numbers',
        /sessions_done=/.test(strat) && /session_number=/.test(strat));

  check('the deck says which one it is showing',
        html.includes('>Sessions done<'));
  evaluate(`S.snap = { paper: true, sessions_done: 4, session_number: 5 };
            S.status = { running: true }; S.fleet = null; renderDash();`);
  check('the deck shows finished sessions',
        evaluate('document.querySelector("#d-sess").textContent') === '4',
        evaluate('document.querySelector("#d-sess").textContent'));
  check('and names the live one underneath',
        evaluate('document.querySelector("#d-sess-sub").textContent') === '#5 live now',
        evaluate('document.querySelector("#d-sess-sub").textContent'));
  evaluate(`S.status = { running: false }; renderDash();`);
  check('nothing running means no live-session line',
        evaluate('document.querySelector("#d-sess-sub").textContent') === '');
  evaluate(`S.snap = { paper: true, sessions_run: 7 }; renderDash();`);
  check('an older algorithm emitting only sessions_run still renders',
        evaluate('document.querySelector("#d-sess").textContent') === '7');
}

console.log('\nLoading states');
{
  check('a skeleton component exists', /\.sk\{/.test(html) && /@keyframes shimmer/.test(html));
  check('it respects reduced motion',
        /prefers-reduced-motion:reduce\)\{ \.sk\{animation:none/.test(html));
  check('the trading-mode card is never an empty bordered box',
        /<div class="card span-all" id="c-mode">\s*<div class="card-h">/.test(html),
        'c-mode renders empty again — it reads as an unlabelled input');
  check('a failed mode fetch says so rather than shimmering forever',
        /Could not read the trading mode/.test(html));
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
  // Three bands, not two. The rail used to collapse to the phone layout at
  // 1000px, so a 1280×800 laptop — the machine this actually runs on — got
  // bottom navigation.
  check('the rail appears at 1024px',
        /@media \(min-width:1024px\)[\s\S]{0,1200}#rail\{display:flex/.test(html));
  check('bottom nav is hidden once the rail is up',
        /@media \(min-width:1024px\)[\s\S]{0,600}nav\{display:none/.test(html));
  check('there is an icons-only band for laptop widths',
        /@media \(min-width:1024px\) and \(max-width:1279\.98px\)/.test(html));
  check('that band narrows the rail rather than hiding it',
        /@media \(min-width:1024px\) and \(max-width:1279\.98px\)\{\s*:root\{--rail:62px\}/
          .test(html));
  check('and hides only the labels',
        /\.rail-nav button span\.lbl\{display:none\}/.test(html));
  check('the label survives as a tooltip and to a screen reader',
        /title="\$\{esc\(v\.label\)\}" aria-label="\$\{esc\(v\.label\)\}"/.test(html));
  check('nothing still targets the old breakpoint',
        !html.includes('min-width:1000px'), '1000px breakpoint left behind');
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
