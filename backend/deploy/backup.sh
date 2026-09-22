#!/usr/bin/env bash
# Nightly backup of the trading database and state.
#
# The SQLite file is the entire trade history, the audit log and every
# algorithm's source, and it lives on one VM with no replica. This keeps a
# rolling set of consistent copies on disk, and optionally pushes them
# off-box, because a backup on the same machine does not survive the failure
# it exists for.
#
# Uses SQLite's own online backup API through Python, not cp: the engine writes
# in WAL mode while this runs, and copying the file by hand can capture a torn
# database. Python rather than the sqlite3 CLI because the CLI is not installed
# by install.sh and may not exist on the VM at all.
#
#   sudo bash backend/deploy/backup.sh
#   sudo bash backend/deploy/backup.sh --remote user@host:/path/   (via scp)

set -euo pipefail

DATA_DIR="${MERIDIAN_DATA_DIR:-/var/lib/meridian}"
DB="${MERIDIAN_DB_PATH:-$DATA_DIR/meridian.db}"
BACKUP_DIR="${MERIDIAN_BACKUP_DIR:-$DATA_DIR/backups}"
KEEP_DAYS="${MERIDIAN_BACKUP_KEEP_DAYS:-30}"
REMOTE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --remote) REMOTE="${2:-}"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

stamp="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

if [[ ! -f "$DB" ]]; then
    echo "no database at $DB — nothing to back up" >&2
    exit 1
fi

out="$BACKUP_DIR/meridian-$stamp.db"

# A consistent snapshot of a live, actively-written database, then verified
# before anything old is pruned — an unverified backup is not a backup.
PY_BIN="${MERIDIAN_PYTHON:-/opt/meridian/backend/.venv/bin/python3}"
[[ -x "$PY_BIN" ]] || PY_BIN="$(command -v python3)"

"$PY_BIN" - "$DB" "$out" <<'PYEOF'
import sqlite3
import sys

src_path, dst_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
dst = sqlite3.connect(dst_path)
with dst:
    src.backup(dst)
src.close()

check = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close()
if check != "ok":
    sys.exit(f"integrity check failed: {check}")
PYEOF

# The engine's crash-recovery files are small and matter on a restore.
tar -czf "$BACKUP_DIR/meridian-state-$stamp.tar.gz" -C "$DATA_DIR" \
    $(cd "$DATA_DIR" && ls gk50k_state*.json engine*.pid 2>/dev/null || true) 2>/dev/null || true

gzip -f "$out"
echo "backup written: $out.gz ($(du -h "$out.gz" | cut -f1))"

find "$BACKUP_DIR" -name 'meridian-*.gz' -mtime "+$KEEP_DAYS" -delete
echo "pruned backups older than $KEEP_DAYS days"

if [[ -n "$REMOTE" ]]; then
    if scp -q "$out.gz" "$REMOTE"; then
        echo "copied off-box to $REMOTE"
    else
        # A failed off-box copy must be loud: the local copy alone does not
        # survive losing the VM, which is the case this exists for.
        echo "OFF-BOX COPY FAILED to $REMOTE — this backup exists only on this VM" >&2
        exit 1
    fi
fi
