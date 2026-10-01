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

from app import programs
from app.program_status import program_snapshot, read_status
from app.program_trades import TradeLogs
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


# A program's periodic status board — framed by rows of "═", printed every
# minute — is what the deck's boxes now show. Logged line by line it buried the
# journal (1,900 of a day's 2,000 rows) and its "N REJECTED by Angel" counter
# was filed as an error every minute. Real messages are printed outside it.
_BORDER = re.compile(r"^═{20,}$")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} IST\b")
MAX_BOARD_LINES = 80  # a "board" longer than this was not one: stop skipping
OPEN_LOOKAHEAD = 3    # lines after a border before its header must have shown


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
        self._board: dict[str, dict] = {}  # algo -> where in a status board it is
        self.trade_logs = TradeLogs(db)
        self._sampled: dict[str, float] = {}
        self._task: asyncio.Task | None = None

    def pump(self) -> int:
        """Move any new complete lines into the event log. Returns lines moved."""
        moved = 0
        self.ingest_trades()
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
                for keep in self._filter(algo_id, line):
                    self.db.add_event(classify(keep), keep[:500], source="program", algo_id=algo_id)
                    moved += 1
        return moved

    def _filter(self, algo_id: str, line: str) -> list[str]:
        """The lines worth logging, with a periodic status board taken out.

        A board is a border, a banner, a timestamped header line, a second
        border, the body, and a closing border. Reading can start part way
        through one (after an API restart), so a line is only treated as part
        of a board once the board's timestamp line confirms it; the few lines
        seen before that are held, and logged if no confirmation comes.
        """
        board = self._board.get(algo_id)
        if _BORDER.match(line):
            if board is None:
                self._board[algo_id] = {"state": "open", "held": [], "n": 0}
                return []
            if board["state"] == "open":
                # No header since the last border: that one opened nothing.
                self._board[algo_id] = {"state": "open", "held": [], "n": 0}
                return board["held"]
            if board["state"] == "header":
                board["state"] = "body"
                return []
            del self._board[algo_id]  # the closing border
            return []
        if board is None:
            if _STAMP.match(line):
                # Started reading inside a board's header: rejoin it there.
                self._board[algo_id] = {"state": "header", "held": [], "n": 0}
                return []
            return [line]
        if board["state"] == "open":
            if _STAMP.match(line):
                board["state"], board["held"] = "header", []
                return []
            board["held"].append(line)
            if len(board["held"]) > OPEN_LOOKAHEAD:
                del self._board[algo_id]
                return board["held"]
            return []
        board["n"] += 1
        if board["n"] > MAX_BOARD_LINES:
            del self._board[algo_id]
            return [line]
        return []

    def ingest_trades(self) -> int:
        """Carry every program's trade log into the trades table, running or not."""
        added = 0
        for algo in self.db.algos():
            if algo.get("kind") == "builtin":
                continue
            folder = programs.program_path(algo["id"]).parent
            if not folder.is_dir():
                continue
            try:
                n = self.trade_logs.ingest(algo["id"], folder)
            except Exception as e:  # a bad file must not stop the follower
                self.db.add_event("warn", f"could not read {algo['id']}'s trade log: {e}",
                                  source="api", algo_id=algo["id"])
                continue
            if n:
                added += n
                self.db.add_event("info", f"{n} trade{'s' if n != 1 else ''} recorded from the program's trade log",
                                  source="api", algo_id=algo["id"])
        return added

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
