# Meridian Capital

Runs a NIFTY options algorithm unattended on a server, every trading day, and
puts the whole of it on a phone: what it is doing right now, why it is or is
not in a trade, and a downloadable record of every session it has ever run.

No laptop stays connected. The server starts the bot at 09:15 IST and stops it
at 15:45, weekdays, skipping NSE holidays.

```
   phone  ── Expo app, or the dashboard added to the home screen
        │  REST + WebSocket, bearer token
        ▼
   dashboard (static, on Vercel)
        │  HTTPS
        ▼
   FastAPI  ──── APScheduler ── 09:15 start / 15:45 stop
        │
        ├─ supervisor ── spawns ── python -m app.bot.strategy
        │                             │  stdout: terminal feed + JSON events
        │◀────────────────────────────┘
        │
        ├─ strategy params ── injected as env on each start
        │
        └─ SQLite ── events · trades · sessions · equity marks
                        │
                        └─ exports: day / week / month / quarter / year
```

**Vercel hosts the dashboard, not the bot.** Serverless functions are capped
at 10–300 seconds, have no persistent disk, and cannot hold a subprocess or a
WebSocket open. The algorithm needs a process alive for six and a half hours
with a disk that still remembers `equity_book.json` tomorrow. Split the two
and both are free: see [`deploy/FREE-HOSTING.md`](deploy/FREE-HOSTING.md) for
Vercel plus an Oracle Cloud Always Free VM in Mumbai, ₹0/month.

---

## Read this before you run it

**The credentials in the original script are burned.** The API key, client PIN
and TOTP secret were pasted into a chat. That TOTP secret is the second factor
on the brokerage account. Rotate all of it in the SmartAPI dashboard before
this touches a server. Nothing in this repository hardcodes a credential —
they are read from `.env`, which is gitignored.

**It ships in paper mode.** `PAPER_MODE=true` places no real orders. It uses
real Angel One quotes, real margin numbers and real latency, and simulates
fills at LTP ± 0.5%. Setting `PAPER_MODE=false` sends real orders to the
exchange with real money. The app shows a red **LIVE MONEY** banner when it is
off — that banner is the only thing standing between simulation and the market.

**The algorithm's logic is unchanged.** Signal generation, the risk ladder,
the GARCH and chop gates, sizing, charge maths and the daily kill switch are
the code as supplied, and every constant still defaults to its v11 value — an
untouched install trades exactly as the original script did. What was added is
everything *around* it: environment-based credentials, structured event
output, a graceful SIGTERM path, paths under `DATA_DIR`, and the ability to
override any parameter from the app.

The one deliberate change inside it is `verify_config()`. The original
asserted the literal v11 constants, which cannot coexist with a strategy you
are allowed to edit. It now asserts the properties that must hold at *any*
values — the ladder steps upward, floors sit below their triggers, session
times run in order and end before the exchange closes — and separately reports
anything differing from v11 rather than refusing to start.

---

## Quick start

Free path start to finish: [`deploy/FREE-HOSTING.md`](deploy/FREE-HOSTING.md).
Paid/self-hosted variants: [`deploy/DEPLOY.md`](deploy/DEPLOY.md).

**Bot server** — any always-on Linux box in India:

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # API_TOKEN
nano .env                                                        # + ANGEL_* values
docker compose -f deploy/docker-compose.yml up -d --build
curl http://localhost:8000/api/health
```

Give it an HTTPS address so the dashboard can reach it — `tailscale funnel
--bg 8000` is free and needs no domain.

**Dashboard on Vercel** — import the repo at [vercel.com/new](https://vercel.com/new)
and set the root directory to `web`. No build step, no secrets stored there;
the token is typed in on your device.

**Phone, nothing to install.** Open the Vercel URL, enter the server address
and token, then Share → *Add to Home Screen*. Full-screen, own icon, free.

**Phone, native app.**

```bash
cd mobile
npm install
npx expo start          # scan the QR with Expo Go
```

For a standalone app that survives without Expo Go:

```bash
npm install -g eas-cli && eas login
eas build:configure                       # writes a real projectId into app.json
eas build --platform android --profile preview   # installable .apk
eas build --platform ios --profile preview       # needs an Apple developer account
```

---

## What it does

### 1. It runs itself, 09:15 to 15:45

APScheduler holds two cron jobs in `Asia/Kolkata`. Weekdays only; NSE holidays
in `backend/app/holidays.py` are skipped. If the server itself reboots at 11:00
on a trading day, the scheduler notices it is inside the window and starts the
bot immediately rather than losing the session. If the bot process dies
unexpectedly mid-session it is restarted with backoff, up to five times.

Both boundaries are editable from the app's Control tab.

The algorithm keeps its own internal clock regardless: no new entries after
13:30, flat at 15:00, EOD report filed at 15:30. The 15:45 supervisor stop is
the outer envelope. A stop — scheduled or from the phone — sends SIGTERM,
which flattens any open position, writes the report, and exits; SIGKILL only
follows if it has not finished in 45 seconds.

### 2. Everything it prints, on the dashboard

The bot's stdout is the interface. Plain lines are the terminal feed verbatim.
Lines prefixed `@@EVT@@` carry structured JSON — entries, exits, ladder steps,
minute reports, decisions, the EOD report. The supervisor reads both, stores
them, and pushes them to the phone over a WebSocket.

- **Dashboard** — equity with an intraday curve, day P&L, the open position
  with its risk-ladder progress (INIT → BE → LOCK1 → LOCK2 → FREE) and what it
  has to do next to step up; or, when flat, the exact reason it is not
  trading. Market atmosphere (GARCH, ADX, VWAP side, efficiency, day range),
  risk rails (kill-switch consumption, trades used, chop verdict), and
  measured Angel One latency.
- **Live** — the raw terminal feed, filterable to trades / decisions / system
  / alerts.
- **Trades** — every round trip; tap one for the full charge breakdown, the
  risk taken, the BSM-vs-real-fill check, and the sentence explaining why it
  was entered.

Push notifications fire on entry, exit, the daily kill switch, and the EOD
report.


### 3. Downloadable results, any window

Reports tab: pick Day / Week / Month / Quarter / Year / All, step the window
back and forward, download as **CSV**, **XLSX** or **JSON**. The file opens in
the system share sheet — save to Files, mail it, send it to Drive.

Every export carries a summary block (net P&L, win rate, profit factor, max
drawdown, charges, days sat out), a per-session table, and every trade with
its full charge breakdown. The XLSX is a three-sheet workbook. Tick *Include
full event log* to append every decision the algorithm made, not just the
trades.

Directly, if you prefer:

```bash
curl -o aug.csv "https://your-server/api/export?period=month&anchor=2026-08-04&format=csv&token=<token>"
curl -o q3.xlsx "https://your-server/api/export?period=quarter&anchor=2026-08-04&format=xlsx&token=<token>"
```

### 4. Chart and trade box

A **Chart** tab draws today's one-minute candles for the underlying with VWAP
and both EMAs overlaid — the same series the algorithm makes its decisions on,
not a second data source that could disagree with it.

When a position is open, the trade box below it plots the option's premium
tick by tick, with horizontal lines for the entry, the live stop, and every
rung of the ladder. The region below the stop is shaded, so what is still at
risk is visible at a glance.

Worth being precise about: **this strategy has no fixed take-profit.** The
lines above the entry are the points at which the stop *ratchets up* — +15%
moves the stop to breakeven, +25% locks +10%, +40% locks +25% — after which it
trails 10% behind the running peak. So the trade box shows those as ladder
targets rather than a TP, because a TP is not what the algorithm has. Each row
shows the price, how far away it is, and where the stop goes when it is hit.

The candles come from the bot's own market feed, so the chart is populated
only while it is running.

### 5. Changing the strategy

Every number the algorithm trades on is editable from the app — stop loss, the
whole risk ladder, GARCH gates, the chop and expiry filters, cooldowns, EMA
periods, session times, the daily kill switch, and the instrument itself (it
will trade BANKNIFTY if you point it there).

Edits are validated before they are stored. Out-of-range values are refused,
and so are internally inconsistent ones — a ladder floor above its own trigger
would place the stop above the market and fire the instant a position opened,
so the API rejects it with that explanation rather than saving it.

Changes apply **on the bot's next start, never mid-position**: a live trade
finishes under the rules it was opened with. Anything differing from the v11
baseline is listed as drift on the Strategy screen and logged at startup, so a
tweak from three weeks ago can't quietly become the thing you forgot. Save
named profiles to switch between parameter sets.

The original script's `assert` block pinned the literal v11 constants, which
cannot survive an editable strategy. It has been replaced with checks on the
properties that actually matter at any set of values — the ladder steps
upward, floors sit under their triggers, session times run in order and finish
before the exchange closes — plus a report of what differs from v11.

To change the *logic* rather than the inputs — a new signal rule, a different
indicator — edit `backend/app/bot/strategy.py` and redeploy. Everything
downstream keeps working as long as it still prints what it prints today.

## Layout

```
backend/
  app/
    bot/strategy.py      the algorithm (logic unchanged) + event emission
    bot/emitter.py       the stdout event protocol
    runner.py            process supervisor, output parser, restart policy
    scheduler.py         09:15 / 15:45 cron, holidays, catch-up
    strategy_config.py   editable parameters: registry, validation, profiles
    db.py                SQLite: events, trades, sessions, equity marks
    exports.py           date-window resolution and CSV/JSON/XLSX writers
    main.py              REST + WebSocket
    auth.py              bearer token, constant-time compare
    push.py              Expo push
    holidays.py          NSE calendar
  tests/                 131 checks, two suites
web/
  index.html             the dashboard — one file, no build step
  vercel.json            static deploy config
  test/render.test.mjs   33 chart-rendering checks
mobile/                  Expo React Native app
deploy/                  Dockerfile, compose, Caddy, systemd, hosting guides
```

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness, unauthenticated |
| GET | `/api/status` | Bot state, live snapshot, schedule, config |
| POST | `/api/bot/start` `/stop` `/restart` | Manual control |
| GET POST | `/api/schedule` | Read or change the session window |
| GET | `/api/events` | Historical feed |
| WS | `/ws?token=` | Live feed |
| GET | `/api/today` | Today's trades, marks, tail |
| GET | `/api/chart` | Candles, overlays, open-position premium track |
| GET PUT | `/api/strategy` | Read or edit strategy parameters |
| POST | `/api/strategy/reset` `/profiles` `/profiles/load` | Baseline and profiles |
| GET | `/api/trades` `/api/sessions` `/api/summary` | History by window |
| GET | `/api/export` | CSV / XLSX / JSON download |
| POST | `/api/push/register` `/api/push/test` | Notifications |

Auth is `X-API-Token` or `Authorization: Bearer`. Downloads also accept
`?token=` because browsers cannot set headers on a plain link.

## Tests

```bash
./test.sh                    # everything: 164 checks + typecheck

# or individually
cd backend && python -m tests.run_all     # 131 checks, two suites
node web/test/render.test.mjs             # 33 chart-rendering checks
cd mobile && npx tsc --noEmit             # strict typecheck, 17 files
```

`test_smoke` covers date-window maths across quarter and leap-year boundaries,
the persistence path, all three export formats, strategy validation, and the
authenticated API surface. `test_supervisor` spawns a real child process and
drives it through output parsing, event persistence, trade and session
extraction, graceful SIGTERM shutdown, startup failure, and the credential
guard on the actual algorithm module. `render.test.mjs` runs the dashboard's
chart code against a DOM stub with flat series, leading nulls, empty data and
off-scale levels — the cases that produce a silently broken SVG.

Neither exercises the trading logic against a live market — that needs an
Angel One session, which is what paper mode is for. Run it for a few days
before considering anything else.

## Known limits

- **Holidays are a hardcoded list.** NSE publishes the next year each
  December; update `backend/app/holidays.py`. A wrong entry costs a skipped
  session, never a bad trade.
- **The bot is single-instance.** One server, one process. Running two against
  the same equity book would corrupt it.
- **Push needs a real device.** Simulators do not receive Expo push tokens.
- **Charge rates are hardcoded** at Angel One's published values, because
  there is no API for them. If SEBI or the exchange changes a rate, edit the
  constants at the top of `strategy.py`.
- **Paper fills are optimistic.** A 0.5% slippage model at LTP assumes you
  always get filled. Real markets do not always oblige, particularly on wide
  ATM spreads near expiry.
- **The chart needs the bot running.** Candles are reused from the feed the
  algorithm trades on rather than fetched separately, which keeps the chart
  honest and costs no extra broker calls — but means there is nothing to draw
  while the bot is stopped.
- **Editing parameters does not backtest them.** The app validates that values
  are internally consistent, not that they are *good*. A change that looks
  sensible can be worse than v11; paper-trade it before trusting it.
- **Switching instrument is not one setting.** Pointing it at BANKNIFTY needs
  the index name, token, quote symbol and strike step changed together, and
  the lot size comes from the scrip master. Check the first entry carefully.
