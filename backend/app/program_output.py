"""A standalone program's own output, followed into the event log.

A strategy module reports through the engine's telemetry; a program only
prints. The supervisor already captures its stdout and stderr to a file, and
this follows that file so every line the program prints — orders, fills,
rejections, its own warnings — appears in the journal and the live tape.
"""

from __future__ import annotations

import asyncio
import re

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
POLL_SECONDS = 2.0
MAX_LINES_PER_POLL = 200
MAX_BYTES_PER_POLL = 256 * 1024

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
        self._task: asyncio.Task | None = None

    def pump(self) -> int:
        """Move any new complete lines into the event log. Returns lines moved."""
        moved = 0
        for algo_id, sup in self.fleet.all().items():
            if not (sup.state.running and sup.is_program(sup.state.pid)):
                continue
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
                line = ANSI.sub("", raw).strip()
                if not line:
                    continue
                self.db.add_event(classify(line), line[:500], source="program", algo_id=algo_id)
                moved += 1
        return moved

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
