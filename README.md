# Meridian Capital

Runs a NIFTY options algorithm unattended on a server, every trading day, and
puts the whole of it on a phone: what it is doing right now, why it is or is
not in a trade, and a downloadable record of every session it has ever run.

No laptop stays connected. The server starts the bot at 09:15 IST and stops it
at 15:45, weekdays, skipping NSE holidays.

```
   phone (Expo app or PWA)
        │  REST + WebSocket, bearer token
        ▼
   FastAPI  ──── APScheduler ── 09:15 start / 15:45 stop
        │
        ├─ supervisor ── spawns ── python -m app.bot.strategy
        │                             │  stdout: terminal feed + JSON events
        │◀────────────────────────────┘
        │
        └─ SQLite ── events · trades · sessions · equity marks
                        │
                        └─ exports: day / week / month / quarter / year
```

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

**The algorithm is unchanged.** Signal generation, the risk ladder, the GARCH
and chop gates, sizing, charge maths and the daily kill switch are the code as
supplied. What was added is everything *around* it: environment-based
credentials, structured event output, a graceful SIGTERM path, and paths under
`DATA_DIR`. The `verify_config()` assertions from the original still guard the
v11 constants at every startup.

---

## Quick start

**Server** — see [`deploy/DEPLOY.md`](deploy/DEPLOY.md) for the full walkthrough.

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # API_TOKEN
nano .env                                                        # + ANGEL_* values
docker compose -f deploy/docker-compose.yml up -d --build
curl http://localhost:8000/api/health
```

**Phone, no build required.** Open `https://your-server/` in Safari or Chrome,
enter the token, then Share → *Add to Home Screen*. It runs full-screen and
looks native. This is the fastest path and needs nothing installed.

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

## The three things you asked for

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

---

## Layout

```
backend/
  app/
    bot/strategy.py    the algorithm (logic unchanged) + event emission
    bot/emitter.py     the stdout event protocol
    runner.py          process supervisor, output parser, restart policy
    scheduler.py       09:15 / 15:45 cron, holidays, catch-up
    db.py              SQLite: events, trades, sessions, equity marks
    exports.py         date-window resolution and CSV/JSON/XLSX writers
    main.py            REST + WebSocket
    auth.py            bearer token, constant-time compare
    push.py            Expo push
    holidays.py        NSE calendar
  web/index.html       installable dashboard, zero build step
  tests/test_smoke.py  50 checks
mobile/                Expo React Native app
deploy/                Dockerfile, compose, Caddy, systemd, DEPLOY.md
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
| GET | `/api/trades` `/api/sessions` `/api/summary` | History by window |
| GET | `/api/export` | CSV / XLSX / JSON download |
| POST | `/api/push/register` `/api/push/test` | Notifications |

Auth is `X-API-Token` or `Authorization: Bearer`. Downloads also accept
`?token=` because browsers cannot set headers on a plain link.

## Tests

```bash
cd backend && python -m tests.run_all     # 92 checks across two suites
cd mobile && npx tsc --noEmit             # strict typecheck, 14 files
```

`test_smoke` covers date-window maths across quarter and leap-year boundaries,
the persistence path, all three export formats, and the authenticated API
surface. `test_supervisor` spawns a real child process and drives it through
output parsing, event persistence, trade and session extraction, graceful
SIGTERM shutdown, startup failure, and the credential guard on the actual
algorithm module.

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
