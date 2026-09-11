# Architecture

Two processes on one VM, a dashboard on Vercel, and a database between them. The design
decisions below all follow from one constraint: **the engine holds real positions, so it
must be the thing least likely to be disturbed.**

## Process layout

```
systemd ──▶ meridian-api (uvicorn)
              │
              ├── Supervisor  ── spawns ──▶ engine.runner  (detached, own session)
              ├── Scheduler   ── reconciles every 15s
              └── Hub         ── one DB reader, fans out to N browsers
```

### Why the engine is not a thread

A crash in the API — a bad request, an OOM, a bug in a route — would take a trading loop
down with it. As a separate process the engine survives anything that happens to the API,
and the API stays up to report that the engine died.

### Why the engine is detached

`subprocess.Popen(..., start_new_session=True)` plus `KillMode=process` in the systemd
unit means `systemctl restart meridian-api` stops only uvicorn. An engine holding an open
position keeps running through an API redeploy.

The cost is that a new API process is no longer the engine's parent and cannot `waitpid`
it. So the supervisor **adopts** instead: it reads `engine.pid`, checks the process is
alive, and verifies `/proc/<pid>/cmdline` actually contains `engine.runner` before
claiming it — a recycled PID belonging to something unrelated is rejected. Liveness after
adoption is polled rather than waited on.

### Why SQLite is the bus

A message broker is another daemon to run out of memory on a free-tier box. SQLite in WAL
mode gives concurrent readers alongside one writer, survives a crash of either process
with the trade record intact, and needs no supervision.

The write split keeps it simple: the engine is the only writer of snapshots, trades,
events and equity samples; the API is the only writer of commands. `claim_commands()`
takes pending rows under `BEGIN IMMEDIATE` so a command is executed exactly once even if
two readers race.

Volume is trivial — a snapshot every 2s, an equity sample every 60s, one trade a day.

## Control flow

Commands travel through the database rather than a socket, so a command survives an API
restart between issue and execution, and the engine picks it up on its next loop
iteration — within 2–20 seconds depending on phase.

```
dashboard ──▶ Next relay ──▶ POST /api/control/halt
                                 │
                                 ▼
                         commands table (pending)
                                 │  engine polls each iteration
                                 ▼
                         claim → execute → finish (done | failed)
```

Commands the engine honours: `halt`, `resume`, `flatten`, `stop`, `reload_scrip`, `ping`.
Process-level `start` / `restart` are the supervisor's job — a stopped engine cannot start
itself.

`stop` is refused while a position is open unless forced, because an engine that exits
mid-trade leaves a position with nothing watching its stop-loss.

## The scheduler is level-triggered

Cron fires at an instant. If the machine is rebooting at 09:05, the trigger is simply
missed and the engine stays down all day.

Instead the scheduler asks "should the engine be up right now?" every 15 seconds and
reconciles toward that answer. A reboot at 11:00 brings the engine back at 11:00. An API
redeploy reconciles on its first tick, which `lifespan` calls immediately at startup.

Two brakes stop it fighting the operator:

- **`manual_override`** — an operator stop during market hours sets it, and the scheduler
  will not restart the engine while it holds. It clears automatically outside the session
  window, so tomorrow starts clean.
- **Crash budget** — after `MERIDIAN_MAX_RESTARTS` (default 5) restarts in one session it
  stops trying and waits for a human, rather than looping into a broken broker connection
  all day.

### The holiday calendar

Operator-maintained in the database, not hardcoded. The NSE list changes yearly and a
stale hardcoded list would silently start the engine on a closed day.

An unconfigured calendar is a cost, not a risk: on a holiday the candle feed goes stale,
and the engine's own `MAX_BAR_AGE_SEC` guard refuses to trade a stale feed. So the
dashboard surfaces an empty calendar as a warning rather than an error.

## Telemetry

The original build painted an ANSI dashboard every 60 seconds. That became a structured
snapshot published to a single database row, overwritten in place with a monotonically
increasing `rev`.

The hub polls that `rev` once a second. A change means one read and a fan-out to every
connected browser — a roomful of open tabs costs one reader, not N. Events stream the
same way via `id > last_seen`.

The snapshot is the contract between the two systems. `engine/runner.py:build_snapshot`
produces it; `frontend/lib/types.ts` mirrors it. Its sections are `engine`, `market`,
`account`, `signal`, `position`, `guards`, `health`.

## Dashboard data path

```
browser ──▶ /api/proxy/[...path]  (Next route handler, attaches the session JWT)
                    │
                    └──▶ https://api.../api/...
browser ◀── wss://api.../ws/live?ticket=...   (direct — Vercel cannot hold a socket)
```

REST goes through the Next relay, so the API origin and the token never reach the
browser. The WebSocket cannot: Vercel's serverless runtime will not hold a long-lived
connection, so the browser connects straight to Oracle, using a short-lived ticket rather
than the session token.

When the socket cannot be held the client falls back to 5-second polling and says so in
the header. A trading dashboard that silently stops updating is worse than one that is
visibly degraded, so connection state is rendered, not hidden.

## What was deliberately not changed

The strategy. Every constant in `engine/config.py` and every function in
`engine/strategy.py` is carried over verbatim, and `tests/test_strategy.py` is the
original build's `selftest()` as pytest. If one of those assertions fails, the deployed
strategy is no longer the strategy that was backtested.

Two behaviours were corrected in the port, both about session-boundary correctness rather
than trading logic: `todays_realised()` and `emit_eod_report()` now key on the engine's
own `session_date` instead of the wall clock, so a query landing either side of a rollover
reads one coherent session.

## Failure modes

| What fails | What happens |
|---|---|
| API crashes | Engine keeps trading. systemd restarts the API, which re-adopts the engine. |
| Engine crashes | API reports it; scheduler restarts it within 15s, up to the crash budget. |
| VM reboots | systemd starts the API; the scheduler starts the engine if inside the window. State file restores an open position. |
| Angel unreachable at exit | Exit retried `ORDER_RETRIES + 2` times. If all fail, the position stays open and a `critical` event says to square off manually. |
| Angel P&L unreadable | Trade booked from a local estimate, flagged `pnl_source=estimate` and marked `~` in the blotter. |
| Clock drift > 30s | New entries halted — skewed time would move the entry and force-close windows. |
| Candle feed stale or diverged | Entry blocked, reason logged and shown on the Signal panel. |
| Dashboard offline | Engine is unaffected. It never depends on the dashboard being reachable. |
