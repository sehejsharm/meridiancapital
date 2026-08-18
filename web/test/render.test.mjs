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
  check('with more than one lane it says they are tappable',
        out.includes('Tap an algorithm'));
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
        silent.includes('sending no data'), silent.slice(0, 200));
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
  check('one algorithm draws no tap hint', !solo.includes('Tap an algorithm'));
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
        html.includes('Your passcode is what the server verifies'));
  check('mixed content is caught before it fails silently',
        html.includes('mixedContentBlocked'));
  check('token is never hardcoded in the page',
        !html.includes('cXTBbbmZ'), 'a real token leaked into the file');
  check('a build stamp is present so a stale cache is visible',
        /const BUILD = "[\d.a-z-]+"/.test(html));
  check('the build stamp is shown on the login screen',
        html.includes('id="g-build"'));

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
