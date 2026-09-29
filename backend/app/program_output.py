"""A standalone program's own output, followed into the event log.

A strategy module reports through the engine's telemetry; a program only
prints. The supervisor already captures its stdout and stderr to a file, and
this follows that file so every line the program prints — orders, fills,
rejections, its own warnings — appears in the journal and the live tape.
"""

from __future__ import annotations

import asyncio
import re
import time

from app.program_status import program_snapshot, read_status
from shared.db import snapshot_key

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
POLL_SECONDS = 2.0
MAX_LINES_PER_POLL = 200
MAX_BYTES_PER_POLL = 256 * 1024
# The built-in engine adds a point to the equity curve once a minute; so does this.
EQUITY_SAMPLE_SEC = 60.0

# Angel's library logs its request headers when a call fails, so a program's
# output can carry the session's bearer token and the API key. Neither belongs
# in the journal, on the live tape, or in a screenshot of either.
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_SECRET_FIELD = re.compile(
    r"""(['"]?(?:X-PrivateKey|privateKey|api_key|jwtToken|refreshToken|feedToken|totp|password)['"]?\s*[:=]\s*['"]?)[^'",\s}]+""",
    re.I,
)


def redact(line: str) -> str:
    line = _BEARER.sub(r"\1[redacted]", line)
    return _SECRET_FIELD.sub(r"\1[redacted]", line)


_ERROR = re.compile(r"REJECT|ERROR|CRITICAL|FAIL|MISMATCH|Traceback|Exception|Refusing", re.I)
_WARN = re.compile(r"WARN|throttl|backing off|stale|retry|NOT CONFIRMED", re.I)
_OK = re.compile(r"FILLED|\bENTER\b|\bEXIT\b|\bOK\b|connected", re.I)


def classify(line: str) -> str:
    if _ERROR.search(line):
        return "error"
    if _WARN.search(line):
        return "warn"
    if _OK.search(line):
        return "ok"
    return "info"


class ProgramOutput:
    def __init__(self, db, fleet):
        self.db = db
        self.fleet = fleet
        self._offsets: dict[str, int] = {}
        self._partial: dict[str, str] = {}
        self._status_ts: dict[str, str] = {}
        self._sampled: dict[str, float] = {}
        self._task: asyncio.Task | None = None

    def pump(self) -> int:
        """Move any new complete lines into the event log. Returns lines moved."""
        moved = 0
        for algo_id, sup in self.fleet.all().items():
            if not (sup.state.running and sup.is_program(sup.state.pid)):
                continue
            self.publish_status(algo_id, sup)
            try:
                size = sup.outfile.stat().st_size
            except OSError:
                continue
            if algo_id not in self._offsets:
                # A program still running from before an API restart had its
                # earlier output ingested then; skip it. One started since is
                # read from the top, so its first lines — login, IP check — land.
                self._offsets[algo_id] = size if sup.state.adopted else 0
            offset = self._offsets[algo_id]
            if size < offset:  # a fresh start truncates the file
                offset, self._partial[algo_id] = 0, ""
            if size == offset:
                continue
            with sup.outfile.open("rb") as fh:
                fh.seek(offset)
                chunk = fh.read(min(size - offset, MAX_BYTES_PER_POLL))
            self._offsets[algo_id] = offset + len(chunk)
            text = self._partial.get(algo_id, "") + chunk.decode("utf-8", "replace")
            *lines, self._partial[algo_id] = text.split("\n")
            for raw in lines[-MAX_LINES_PER_POLL:]:
                line = redact(ANSI.sub("", raw).strip())
                if not line:
                    continue
                self.db.add_event(classify(line), line[:500], source="program", algo_id=algo_id)
                moved += 1
        return moved

    def publish_status(self, algo_id: str, sup) -> bool:
        """Turn the program's own status file into the desk's snapshot.

        Returns True when a new snapshot was published. Never raises: the deck
        showing nothing is better than the follower dying mid-session.
        """
        if sup.program_path is None:
            return False
        try:
            raw = read_status(sup.program_path.parent, sup.state.pid)
            if raw is None or raw.get("ts") == self._status_ts.get(algo_id):
                return False
            algo = self.db.algo(algo_id) or {}
            started = sup.state.started_ts or time.time()
            snap = program_snapshot(
                raw, name=algo.get("name") or algo_id, mode=sup.state.mode,
                pid=sup.state.pid, uptime_sec=time.time() - started,
            )
            self.db.kv_set(snapshot_key(algo_id), snap)
            self._status_ts[algo_id] = raw.get("ts")
            acct = snap["account"]
            if raw.get("equity") is not None and time.time() - self._sampled.get(algo_id, 0.0) >= EQUITY_SAMPLE_SEC:
                self.db.add_equity_sample(
                    snap["market"]["session_date"], acct["equity"], acct["realised_today"],
                    acct["peak_equity"], acct["day_pl"], algo_id=algo_id,
                )
                self._sampled[algo_id] = time.time()
            return True
        except Exception as e:
            self.db.add_event("warn", f"could not read {algo_id}'s status file: {e}",
                              source="api", algo_id=algo_id)
            return False

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="meridian-program-output")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.pump)
            except Exception as e:  # following output must never take the API down
                self.db.add_event("warn", f"program output follower: {e}", source="api")
            await asyncio.sleep(POLL_SECONDS)
