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

console.log('\nFormatting');
{
  // The helpers are `const` arrow functions, which do not become properties
  // of the sandbox global, so they are evaluated inside the context.
  const evaluate = (expr) => vm.runInContext(expr, context);

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
