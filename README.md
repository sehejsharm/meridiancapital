# Meridian Capital

Trading infrastructure for **GANESH KAVACH 50K (Config #5 v3)** — a NIFTY weekly-option
breakout strategy on Angel One.

Two systems, integrated:

| | What it is | Where it runs |
|---|---|---|
| **`backend/`** | The trading engine plus a control-plane API that supervises it and starts and stops it automatically around the NSE session | Oracle Cloud Always Free VM |
| **`frontend/`** | The desk dashboard — live equity, position, risk guards, blotter, and the operator controls | Vercel |

The strategy itself is unchanged from the single-file build it was ported from: same
Donchian-90 breakout, same ITM50 strike, same profit and stop ladder, same kill switches,
same order-verification path. What changed is everything around it — the loop publishes
structured telemetry instead of painting a console, it obeys commands issued from the
dashboard, and it no longer depends on a laptop staying awake.

> **Before anything else, read [SECURITY.md](SECURITY.md).** The source file this was
> ported from carried live Angel One credentials in plain text. Those four values need to
> be rotated.

## How the pieces fit

```
Vercel                          Oracle Cloud Always Free VM
┌──────────────────┐            ┌────────────────────────────────────────┐
│ Next.js dashboard│            │ Caddy  (TLS, Let's Encrypt)            │
│                  │──HTTPS────▶│   │                                    │
│ route handlers   │            │   ▼                                    │
│ hold the session │            │ meridian-api  (FastAPI, systemd)       │
│ cookie; browser  │◀──WSS──────│   ├── supervisor ── spawns/adopts ──┐  │
│ never sees the   │            │   ├── scheduler  ── 09:05 / 15:25   │  │
│ API origin       │            │   └── WebSocket fan-out             │  │
└──────────────────┘            │                                     ▼  │
                                │ SQLite (WAL) ◀──────────▶ engine.runner│
                                │  snapshots · trades · events · commands│
                                │                              │         │
                                └──────────────────────────────┼─────────┘
                                                               ▼
                                                        Angel One SmartAPI
```

SQLite in WAL mode is the bus between the two processes. The engine is the only writer of
market and trade state; the API is the only writer of commands. That split means the API
can be restarted or redeployed mid-session without touching a process that is holding a
live position, and either side can crash without losing a trade record.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the detail, including why the engine runs
detached and how a restarted API re-adopts it.

## Automatic on/off

The scheduler is level-triggered: every 15 seconds it asks *should the engine be up right
now?* and reconciles. It starts at **09:05 IST** (Angel login, scrip master, and 90+
candles need warm-up before the 09:15 open) and stops at **15:25 IST**, after the
strategy's own 15:10 forced square-off.

Because it reconciles rather than fires cron jobs, a reboot, a redeploy, or a crash
mid-session self-heals instead of leaving the engine down until tomorrow. Weekends and
NSE holidays are skipped; holidays are operator-maintained from the dashboard rather than
hardcoded, so a stale list can never silently start the engine on a closed day.

## Quick start

```bash
# Backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                    # 168 tests

# Generate dashboard secrets
.venv/bin/python scripts/bootstrap_secrets.py

# Run locally (paper mode, no broker needed for the API itself)
export MERIDIAN_DATA_DIR=./var MERIDIAN_AUTOSTART=0
export MERIDIAN_JWT_SECRET=... MERIDIAN_PASSWORD_HASH=...
.venv/bin/python scripts/seed_demo.py            # optional: sample data to look at
.venv/bin/uvicorn app.main:app --port 8080
```

```bash
# Frontend
cd frontend
npm install
echo "MERIDIAN_API_URL=http://127.0.0.1:8080" > .env.local
npm run dev
```

Deploying for real: [DEPLOYMENT.md](DEPLOYMENT.md).

## Paper first

`MERIDIAN_TRADING_MODE=paper` is the default and places no orders. Switching to live
requires typing `GO LIVE` in the dashboard, and is refused while the engine is running —
so it can never flip from paper to live with a position open.

Run at least one full session in paper and confirm the blotter, the event log, and the
end-of-day report all look right before arming live trading.

## Layout

```
backend/
  engine/      the trading loop — config, strategy maths, broker, state, runner
  app/         FastAPI control plane — auth, supervisor, scheduler, WebSocket hub
  shared/      SQLite bus and the NSE session calendar
  tests/       168 tests, including the original build's self-test suite
  deploy/      systemd unit, Caddyfile, install.sh, env template
  scripts/     secret bootstrap, demo seeding
frontend/
  app/         App Router pages and the server-side API relay
  components/  dashboard panels, chart, design primitives
  lib/         types mirroring the engine snapshot, formatting, live feed hook
```
