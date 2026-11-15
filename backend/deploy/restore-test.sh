#!/usr/bin/env bash
# Monthly restore test: prove that the newest backup can actually be restored.
#
# A backup nobody has restored is a hope, not a backup. This takes the newest
# one — the copy that left the machine when one is set up and can be fetched
# back, else the local copy — unpacks it into a scratch folder, opens it, runs
# SQLite's integrity check, and counts the rows that matter against the live
# database. Nothing live is touched: the restore goes to a temporary folder
# that is removed at the end.
#
#   sudo bash backend/deploy/restore-test.sh            newest backup
#   sudo bash backend/deploy/restore-test.sh --local    never fetch the off-machine copy
#
# The outcome is written to <backup dir>/last-restore-test.json; the Reliability
# page shows it and the API alerts if it failed or has not run for 45 days.
# To restore for real, see docs/RUNBOOK.md ("Restoring the database").

set -euo pipefail

ENV_FILE="${MERIDIAN_ENV_FILE:-/etc/meridian/meridian.env}"

env_value() {
    local key="$1" line
    [[ -r "$ENV_FILE" ]] || return 0
    line="$(grep -m1 -E "^${key}=" "$ENV_FILE" || true)"
    line="${line#*=}"
    line="${line%\"}"; line="${line#\"}"
    line="${line%\'}"; line="${line#\'}"
    printf '%s' "$line"
}

setting() {
    local key="$1" val="${!1:-}"
    [[ -n "$val" ]] || val="$(env_value "$key")"
    printf '%s' "$val"
}

DATA_DIR="$(setting MERIDIAN_DATA_DIR)"; DATA_DIR="${DATA_DIR:-/var/lib/meridian}"
DB="${MERIDIAN_DB_PATH:-$DATA_DIR/meridian.db}"
BACKUP_DIR="${MERIDIAN_BACKUP_DIR:-$DATA_DIR/backups}"
REMOTE="$(setting MERIDIAN_BACKUP_REMOTE)"
URL="$(setting MERIDIAN_BACKUP_URL)"
PASSPHRASE="$(setting MERIDIAN_BACKUP_PASSPHRASE)"
LOCAL_ONLY=0
[[ "${1:-}" == "--local" ]] && LOCAL_ONLY=1

PY_BIN="${MERIDIAN_PYTHON:-/opt/meridian/backend/.venv/bin/python3}"
[[ -x "$PY_BIN" ]] || PY_BIN="$(command -v python3)"

report() {  # ok(true|false) backup source detail [rows-json]
    "$PY_BIN" - "$BACKUP_DIR/last-restore-test.json" "$@" <<'PYEOF'
import json
import os
import sys
import time

path, ok, backup, source, detail = sys.argv[1:6]
rows = json.loads(sys.argv[6]) if len(sys.argv) > 6 and sys.argv[6] else None
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "ok": ok == "true",
               "backup": backup or None, "source": source or None, "detail": detail, "rows": rows}, fh)
os.replace(tmp, path)
os.chmod(path, 0o644)
PYEOF
}

mkdir -p "$BACKUP_DIR"
newest="$(ls -1t "$BACKUP_DIR"/meridian-*.db.gz 2>/dev/null | head -1 || true)"
if [[ -z "$newest" ]]; then
    report false "" "" "no backup found in $BACKUP_DIR — the nightly backup has not run"
    echo "no backup found in $BACKUP_DIR" >&2
    exit 1
fi
name="$(basename "$newest")"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

source_kind="local copy"
candidate="$work/restore.db.gz"
fetched=0
if [[ $LOCAL_ONLY -eq 0 && ( -n "$URL" || -n "$REMOTE" ) ]]; then
    remote_name="$name"
    [[ -n "$PASSPHRASE" ]] && remote_name="$name.enc"
    if [[ -n "$URL" ]] && curl -fsS --max-time 600 -o "$work/fetched" "${URL}${remote_name}" 2>/dev/null; then
        fetched=1
    elif [[ -n "$REMOTE" ]] && scp -q -o BatchMode=yes -o ConnectTimeout=20 "${REMOTE%/}/${remote_name}" "$work/fetched" 2>/dev/null; then
        fetched=1
    fi
    if [[ $fetched -eq 1 ]]; then
        if [[ -n "$PASSPHRASE" ]]; then
            export MERIDIAN_BACKUP_PASSPHRASE="$PASSPHRASE"
            if openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
                    -in "$work/fetched" -out "$candidate" -pass env:MERIDIAN_BACKUP_PASSPHRASE 2>/dev/null; then
                source_kind="off-machine copy"
            else
                report false "$name" "off-machine copy" "the off-machine copy could not be decrypted with MERIDIAN_BACKUP_PASSPHRASE"
                echo "RESTORE TEST FAILED: the off-machine copy could not be decrypted" >&2
                exit 1
            fi
        else
            mv "$work/fetched" "$candidate"
            source_kind="off-machine copy"
        fi
    fi
fi
if [[ "$source_kind" == "local copy" ]]; then
    cp "$newest" "$candidate"
fi

if ! gunzip -f "$candidate" 2>/dev/null; then
    report false "$name" "$source_kind" "the backup file is not a readable gzip archive"
    echo "RESTORE TEST FAILED: $name is not a readable gzip archive" >&2
    exit 1
fi

set +e
result="$("$PY_BIN" - "$work/restore.db" "$DB" <<'PYEOF'
import json
import sqlite3
import sys

restored, live = sys.argv[1], sys.argv[2]
TABLES = ("trades", "events", "equity_samples", "algos", "algo_versions", "engine_runs", "audit_log", "kv")


def counts(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        out = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES if t in have}
        last = con.execute("SELECT MAX(session_date) FROM trades").fetchone()[0] if "trades" in have else None
        return have, out, last
    finally:
        con.close()


try:
    con = sqlite3.connect(f"file:{restored}?mode=ro", uri=True)
    check = con.execute("PRAGMA integrity_check").fetchone()[0]
    con.close()
    have, rows, last = counts(restored)
except sqlite3.Error as e:
    print(json.dumps({"ok": False, "detail": f"the restored file does not open as a database: {e}", "rows": None}))
    sys.exit(0)

problems = []
if check != "ok":
    problems.append(f"integrity check: {check}")
missing = [t for t in TABLES if t not in have]
if missing:
    problems.append(f"tables missing: {', '.join(missing)}")
try:
    _h, live_rows, _l = counts(live)
    for t in ("trades", "algo_versions"):
        if t in rows and t in live_rows and rows[t] > live_rows[t]:
            problems.append(f"the backup has more {t} ({rows[t]}) than the live database ({live_rows[t]})")
    if live_rows.get("trades") and not rows.get("trades"):
        problems.append("the live database has trades and the backup has none")
except sqlite3.Error:
    live_rows = None
rows = {**rows, "last_trade_day": last}
detail = "; ".join(problems) if problems else (
    f"restored and verified: {rows.get('trades', 0)} trades (latest {last or 'none'}), "
    f"{rows.get('algo_versions', 0)} program versions, {rows.get('events', 0)} journal lines")
print(json.dumps({"ok": not problems, "detail": detail, "rows": rows}))
PYEOF
)"
status=$?
set -e
if [[ $status -ne 0 || -z "$result" ]]; then
    report false "$name" "$source_kind" "the restore check itself could not run"
    echo "RESTORE TEST FAILED: the check could not run" >&2
    exit 1
fi

ok="$("$PY_BIN" -c 'import json,sys; print("true" if json.loads(sys.argv[1])["ok"] else "false")' "$result")"
detail="$("$PY_BIN" -c 'import json,sys; print(json.loads(sys.argv[1])["detail"])' "$result")"
rows="$("$PY_BIN" -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["rows"]))' "$result")"
report "$ok" "$name" "$source_kind" "$detail" "$rows"

if [[ "$ok" == "true" ]]; then
    echo "restore test passed ($source_kind, $name): $detail"
else
    echo "RESTORE TEST FAILED ($source_kind, $name): $detail" >&2
    exit 1
fi
