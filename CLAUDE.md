# Working on Meridian Capital

This file is read by every Claude session. The repository is **public**: nothing private
goes in here (no credentials, no P&L, no account details, no open security to-dos). The
owner keeps a private hand-off with the current state and numbers; ask for it if it has
not been pasted into the session.

## Ground rules (from the owner; they override everything else)

1. **This is a real-money trading system.** Treat every change near the trading engine,
   kill switches or P&L maths with a brokerage vendor's care.
2. **Never change a live risk parameter** (stop %, sizing, kill-switch thresholds, exit
   rules, contract-expiry window) as a side effect of a UI or backend fix. If a fix would
   touch trading logic rather than display logic: **stop and ask the owner first, with
   the options laid out.** Do not decide for them.
3. **Never print, paste, commit or expose Angel One credentials** (API key, client code,
   PIN, TOTP seed). Treat uploaded program sources, the VM's database and
   `/etc/meridian/meridian.env` as credential-bearing. Blank them in any test copy.
4. **Real market data only** in backtests. No simulated data. Only backtested changes go
   into the algorithm.
5. **After each bug fix**, tell the owner exactly what changed and why the old behaviour
   was wrong. Write or update tests for anything touching P&L, reconciliation or
   strategy-config display.
6. The owner's email address is private: never send it to an outside service.
7. **Report honestly**: what is verified, what is not, what you could not do. The owner
   is blunt and dislikes long delays; be brief and commit early.
8. No pull request unless asked. Commit messages end with the attribution lines the
   harness gives in its system reminder; model names go nowhere else in the repo.

## Release routine

Work on the branch the session's instructions assign. Then:
commit → push the branch → fast-forward `main` (Vercel auto-deploys the dashboard from
`main`) → check CI (`.github/workflows/ci.yml`: backend ruff + pytest, frontend tsc +
`npm test` + build, acceptance gate).

The container is ephemeral and can restart mid-task: run `git status` first, commit and
push early, never leave work only in the working tree, and do not `git stash` while long
jobs run.

The VM is updated **by the owner, by hand, outside market hours**:
`cd ~/meridiancapital && git pull && sudo bash backend/deploy/install.sh` (no arguments:
it keeps the existing domain and dashboard origin). Moving to another VM:
`backend/deploy/migrate.sh` and `DEPLOYMENT.md` → "Moving to a new VM".

## The system in one paragraph

`frontend/` is a Next.js dashboard on Vercel. `backend/` is a FastAPI control plane on an
Oracle Always Free VM that supervises trading processes and talks to them through SQLite
(see `ARCHITECTURE.md`). The real-money trader is an operator-uploaded Python *program*,
stored in the VM database (`algo_versions.source`), **not in this repo**; programs read no
command queue, and SIGINT makes them sell and exit. The built-in engine (gk50k) is retired
by default (`db.builtin_retired()`, env `MERIDIAN_BUILTIN_RETIRED`; tests set it to 0 in
`tests/conftest.py`). Any change to the uploaded program is delivered as a file for the
owner to upload, needs an explicit go-ahead, and must pass the program's self-test, its
money harness, and backtest equivalence with the version it replaces.

## Checks

```bash
# backend (create a venv with requirements-dev.txt if none exists)
cd backend && python -m ruff check . && python -m pytest -q
# frontend
cd frontend && npm test && node_modules/.bin/tsc --noEmit && node_modules/.bin/next build
```

Frontend rules the tests enforce: Tailwind classes must be literal strings;
`tests/noSideScroll.test.ts` forbids `overflow-x-auto/scroll`, `overflow-auto/scroll`,
`<table … min-w-[` and bare `whitespace-pre`; lib files imported by tests may use the `@/`
alias only for `import type`.

Playwright against a local build needs `locale: "en-IN"`, a `meridian_session` cookie and
`bypassCSP: true`. Shell commands like `sleep 240; …` are rejected: run long jobs in the
background or poll with `until …; do sleep N; done`.
