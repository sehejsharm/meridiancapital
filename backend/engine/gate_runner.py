"""Run the acceptance gate against uploaded source, in a process of its own.

Invoked as a subprocess by the control plane. Source arrives on stdin, a JSON
report leaves on stdout. Running out-of-process is the point: importing a
module executes it, so that must happen somewhere disposable, under resource
limits, and never inside the API process that holds the broker session.
"""

from __future__ import annotations

import json
import sys

CPU_SECONDS = 20
ADDRESS_SPACE_BYTES = 1024 * 1024 * 1024  # 1 GiB


# numpy's BLAS starts worker threads when it is first imported, and threads
# count against RLIMIT_NPROC. With the cap at 0 that start fails and the gate
# dies with exit 130 before it checks anything — for every upload that imports
# pandas, whenever the API runs as an ordinary user (root ignores the cap, so it
# passed on a root shell and failed in CI and on a non-root VM). One thread each,
# set before anything imports numpy, keeps them from starting at all.
_SINGLE_THREAD = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                  "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")


def _apply_limits() -> None:
    """Cap CPU and memory so a runaway upload cannot take the VM down."""
    import os

    for var in _SINGLE_THREAD:
        os.environ[var] = "1"
    try:
        import resource
    except ImportError:  # pragma: no cover - POSIX only
        return
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))  # no forking
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))  # no writing files


def main() -> int:
    _apply_limits()
    source = sys.stdin.read()

    from engine.contract import ContractError, load_and_verify
    from engine.gate import GateReport, run_checks
    from engine.sandbox import scan

    scan_result = scan(source)
    if not scan_result.ok:
        report = GateReport(passed=False, error="static screening rejected this source")
        out = report.as_dict()
        out["scan"] = scan_result.as_dict()
        json.dump(out, sys.stdout)
        return 0

    try:
        strategy = load_and_verify(source, "meridian_candidate")
    except ContractError as exc:
        report = GateReport(passed=False, error=str(exc))
        out = report.as_dict()
        out["scan"] = scan_result.as_dict()
        json.dump(out, sys.stdout)
        return 0

    report = run_checks(strategy)
    out = report.as_dict()
    out["scan"] = scan_result.as_dict()
    out["name"] = strategy.name
    json.dump(out, sys.stdout, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
