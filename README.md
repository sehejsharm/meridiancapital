# Meridian Capital

The desk for **GANESH KAVACH 50K**, a NIFTY weekly-option breakout trader on Angel One.

Two systems, integrated:

| | What it is | Where it runs |
|---|---|---|
| **`backend/`** | A control-plane API that runs the trading program, starts and stops it around the NSE session, records its output and checks it against Angel's books after the close | Oracle Cloud Always Free VM |
| **`frontend/`** | The desk dashboard — live equity, position, risk guards, blotter, and the operator controls | Vercel |

The real-money trader is a standalone Python **program** that the operator uploads on the
dashboard's Algorithms page. It is stored in the VM's database, **not in this repository**,
and run exactly as written: it logs in to Angel, reads the market and places its own
orders. The desk starts and stops it, restarts it after a crash, refuses a second process
on real money, turns its log into the journal, trades and strategy settings, and
reconciles them with Angel's books after the close. Its version history is in
[docs/PROGRAM_CHANGELOG.md](docs/PROGRAM_CHANGELOG.md).

The built-in engine in `backend/engine/` (gk50k, the original port) is retired by default
(`MERIDIAN_BUILTIN_RETIRED`) and kept for its tests and shared settings.

> **Read [SECURITY.md](SECURITY.md) first.** Uploaded programs and the server's settings
> file hold Angel One credentials; neither belongs in this repository.

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
                                │ SQLite (WAL) ◀──────────▶ the program  │
                                │  snapshots · trades · events · commands│
                                │                              │         │
                                └──────────────────────────────┼─────────┘
                                                               ▼
                                                        Angel One SmartAPI
```

SQLite in WAL mode holds everything the desk shows. The trading program runs as its own
detached process, so the API can be restarted or redeployed mid-session without touching a
process that is holding a live position, and either side can crash without losing a trade
record.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the detail, including why the engine runs
detached and how a restarted API re-adopts it.

## Automatic on/off

The scheduler is level-triggered: every 15 seconds it asks *should the program be up right
now?* and reconciles. It starts at **09:05 IST** (Angel login, scrip master, and 90+
candles need warm-up before the 09:15 open) and stops at **15:25 IST**, after the
strategy's own 15:10 forced square-off.

Because it reconciles rather than fires cron jobs, a reboot, a redeploy, or a crash
mid-session self-heals instead of leaving the program down until tomorrow. Weekends and
NSE holidays are skipped. NSE's published list ships with the code and loads itself at
startup; the Controls page shows it, and a closure NSE declares at short notice can be
added there. The deck warns the session before a closure and on the day itself.

## Quick start

```bash
# Backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                    # about 870 tests

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

Deploying for real: [DEPLOYMENT.md](DEPLOYMENT.md). On the VM, `sudo bash backend/deploy/install.sh`
installs or updates everything; `sudo bash backend/deploy/configure.sh` then asks, step by step,
for the outside monitor, Telegram alerts, off-VM backups and the Angel values for the
after-close check. Day-to-day operation: [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Paper first

`MERIDIAN_TRADING_MODE=paper` is the default and places no orders. The dashboard asks
which money an algorithm will trade every time you start one, and a mode change is
refused while it is running — so it can never flip from paper to live with a position
open.

Run at least one full session in paper and confirm the blotter, the event log, and the
end-of-day report all look right before arming live trading.

## Layout

```
backend/
  engine/      shared settings and the retired built-in trading loop
  app/         FastAPI control plane — auth, supervisor, scheduler, program output, reports
  shared/      SQLite bus and the NSE session calendar
  tests/       the test suite, including the original build's self-test
  deploy/      systemd units, Caddyfile, install.sh, configure.sh, migrate.sh, env template
  scripts/     secret bootstrap, demo seeding, Angel settings
frontend/
  app/         App Router pages and the server-side API relay
  components/  dashboard panels, chart, design primitives
  lib/         types mirroring the engine snapshot, formatting, live feed hook
```
