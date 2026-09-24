"""Several algorithms running at once, each in its own process.

One Supervisor per algorithm, each with its own pidfile, its own mode, its own
slice of the database. Isolation is the whole point: a crash, a kill switch or
a flatten belongs to one algorithm and must not touch another's position. The
engines share only the SQLite bus, and every row they write carries an algo_id.

Real money and paper run side by side. A live algorithm and a paper one are
the same code path with a different flag, which is what makes paper trading
worth anything as a rehearsal.
"""

from __future__ import annotations

import sys
import threading

from app.algo_store import materialise
from app.supervisor import DEFAULT_ALGO, Supervisor
from app.programs import (
    install_hint,
    materialise_program,
    missing_packages,
    mode_arguments,
    runtime_of,
)
from engine.contract import missing_members
from shared.db import Database


class Fleet:
    """Owns the per-algorithm supervisors and keeps them in step with the registry."""

    def __init__(self, db: Database, supervisor_factory=Supervisor):
        self.db = db
        self._factory = supervisor_factory
        self._sups: dict[str, Supervisor] = {}
        self._lock = threading.RLock()
        self.sync()

    # ── registry <-> supervisors ─────────────────────────────────────────────
    def sync(self) -> None:
        """Create a supervisor for every registered algorithm, drop the rest."""
        with self._lock:
            registered = {a["id"]: a for a in self.db.algos()}
            # The built-in always exists, even before anything is registered.
            registered.setdefault(DEFAULT_ALGO, {"id": DEFAULT_ALGO, "mode": "paper"})

            for algo_id in list(self._sups):
                if algo_id not in registered:
                    sup = self._sups.pop(algo_id)
                    if sup.state.running:
                        sup.stop(reason="algorithm removed from the registry")

            for algo_id in registered:
                if algo_id not in self._sups:
                    self._sups[algo_id] = self._build(algo_id)

    def _build(self, algo_id: str) -> Supervisor:
        return self._factory(
            self.db,
            algo_id=algo_id,
            mode_provider=None if algo_id == DEFAULT_ALGO else self._mode_of(algo_id),
            strategy_path=None,
        )

    def _mode_of(self, algo_id: str):
        def provider() -> str:
            algo = self.db.algo(algo_id)
            return (algo or {}).get("mode", "paper")

        return provider

    def get(self, algo_id: str) -> Supervisor | None:
        with self._lock:
            if algo_id not in self._sups:
                self.sync()
            return self._sups.get(algo_id)

    def all(self) -> dict[str, Supervisor]:
        with self._lock:
            return dict(self._sups)

    # ── control ──────────────────────────────────────────────────────────────
    def is_running(self, algo_id: str) -> bool:
        sup = self.get(algo_id)
        if not sup:
            return False
        sup.refresh()
        return bool(sup.state.running)

    def start(self, algo_id: str, trigger: str = "manual") -> dict:
        sup = self.get(algo_id)
        if not sup:
            return {"ok": False, "detail": "no such algorithm"}

        # One algorithm on real money at a time. Angel reports positions and
        # P&L per account, not per algorithm: two live engines on one account
        # cannot tell their holdings apart, so one's exit or retry could sell
        # the other's lots and each would book the other's P&L. Paper runs
        # alongside freely.
        if sup.desired_mode() == "live":
            for other_id, other in self.all().items():
                if other_id == algo_id:
                    continue
                other.refresh()
                if other.state.running and other.state.mode == "live":
                    name = (self.db.algo(other_id) or {}).get("name") or other_id
                    return {
                        "ok": False,
                        "detail": f"'{name}' is already trading real money on this Angel account. "
                        f"Two live algorithms on one account cannot tell their positions apart, so "
                        f"one could close the other's. Stop it first, or run this one on paper.",
                    }

        algo = self.db.algo(algo_id)
        # An uploaded algorithm runs its own source; the built-in runs the
        # engine's compiled-in strategy.
        if algo and algo.get("kind") != "builtin":
            version = self.db.version(algo.get("active_version")) if algo.get("active_version") else None
            if not version:
                return {
                    "ok": False,
                    "detail": "this algorithm has no active version — open it and activate one, "
                    "or upload it again",
                }
            source = version["source"]
            kind = runtime_of(source)
            if kind == "program":
                problem = self._program_problem(source)
                if problem:
                    return {"ok": False, "detail": problem}
                sup.strategy_path = None
                sup.program_path = materialise_program(algo_id, source)
                sup.program_args = mode_arguments(source) or {}
            elif kind == "strategy":
                sup.program_path = None
                sup.strategy_path = materialise(algo_id, version["version"], source)
            else:
                # Neither a module the engine can call nor a program that runs
                # itself; starting it would only crash-loop. Say why instead.
                missing = missing_members(source)
                return {
                    "ok": False,
                    "detail": f"this file cannot run: it neither defines the strategy functions "
                    f"({', '.join(missing)}) nor runs itself (no `if __name__ == \"__main__\":` block).",
                }

        return sup.start(trigger=trigger)

    def set_mode(self, algo_id: str, mode: str) -> None:
        sup = self.get(algo_id)
        if sup is None:
            raise KeyError(algo_id)
        sup.set_mode(mode)

    @staticmethod
    def _program_problem(source: str) -> str | None:
        """Why a program cannot be started safely, or None."""
        if mode_arguments(source) is None:
            return (
                "this program has no way to be told paper from live — it defines no --paper/--live "
                "flags and does not read MERIDIAN_TRADING_MODE — so starting it on paper could "
                "place real orders. Add either and upload it again."
            )
        missing = missing_packages(source)
        if missing:
            from app.config import settings

            return (
                f"this program imports {', '.join(missing)}, which is not installed on the VM. "
                f"Install it with: {install_hint(missing, settings.python_bin or sys.executable)}"
            )
        return None

    def stop(
        self, algo_id: str, reason: str = "manual", force: bool = False, manual: bool = True
    ) -> dict:
        """Stop one algorithm.

        ``manual`` marks it as the operator's decision, which keeps the
        scheduler from bringing it straight back up. The scheduler's own
        end-of-session stop passes False, or nothing would ever run again.
        """
        sup = self.get(algo_id)
        if not sup:
            return {"ok": False, "detail": "no such algorithm"}
        res = sup.stop(reason=reason, force=force)
        if res.get("ok") and manual:
            sup.state.manual_override = True
        return res

    def stop_all(self, reason: str = "fleet stop") -> list[dict]:
        return [self.stop(a, reason=reason) for a in self.all()]

    def refresh(self) -> None:
        for sup in self.all().values():
            sup.refresh()

    # ── reporting ────────────────────────────────────────────────────────────
    def snapshot(self, algo_id: str) -> dict:
        sup = self.get(algo_id)
        if not sup:
            return {"running": False, "pid": None, "mode": "paper"}
        sup.refresh()
        return sup.snapshot()

    def overview(self) -> dict:
        """One line per algorithm, for the deck."""
        self.sync()
        out = []
        live_running = 0
        for algo_id, sup in sorted(self.all().items()):
            sup.refresh()
            algo = self.db.algo(algo_id) or {}
            snap = sup.snapshot()
            if snap.get("running") and snap.get("mode") == "live":
                live_running += 1
            out.append(
                {
                    "algo_id": algo_id,
                    "name": algo.get("name") or "GANESH KAVACH 50K",
                    "kind": algo.get("kind") or "builtin",
                    **snap,
                }
            )
        return {
            "algos": out,
            "running": sum(1 for a in out if a.get("running")),
            "live_running": live_running,
            "total": len(out),
        }
