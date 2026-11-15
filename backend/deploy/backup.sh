#!/usr/bin/env bash
# Nightly backup of the trading database and state.
#
# The SQLite file is the entire trade history, the audit log and every
# algorithm's source, and it lives on one VM with no replica. This keeps a
# rolling set of consistent copies on disk and sends each one off the machine,
# because a backup on the same machine does not survive the failure it exists
# for.
#
# Uses SQLite's own online backup API through Python, not cp: the engine writes
# in WAL mode while this runs, and copying the file by hand can capture a torn
# database. Python rather than the sqlite3 CLI because the CLI is not installed
# by install.sh and may not exist on the VM at all.
#
#   sudo bash backend/deploy/backup.sh
#   sudo bash backend/deploy/backup.sh --remote user@host:/path/   (via scp)
#
# Where the copy goes off the machine is read from /etc/meridian/meridian.env,
# so the nightly timer sends it without anyone passing an argument:
#
#   MERIDIAN_BACKUP_REMOTE=user@host:/path/     scp target (root's SSH key)
#   MERIDIAN_BACKUP_URL=https://.../o/          HTTPS upload address ending in "/"
#                                               (an Oracle Object Storage
#                                               pre-authenticated request works)
#   MERIDIAN_BACKUP_PASSPHRASE=...              encrypts the copy that leaves
#
# The database holds every uploaded program's source. A copy sent to an HTTPS
# address is therefore always encrypted: without a passphrase it is not sent.
#
# The outcome is written to <backup dir>/last-backup.json; the dashboard's
# Reliability page reads it, and the API alerts when a night is missed or the
# off-machine copy failed. The address and the passphrase are never written there.

set -euo pipefail

ENV_FILE="${MERIDIAN_ENV_FILE:-/etc/meridian/meridian.env}"

# One value from the env file, without sourcing it (its values are not shell).
env_value() {
    local key="$1" line
    [[ -r "$ENV_FILE" ]] || return 0
    line="$(grep -m1 -E "^${key}=" "$ENV_FILE" || true)"
    line="${line#*=}"
    line="${line%\"}"; line="${line#\"}"
    line="${line%\'}"; line="${line#\'}"
    printf '%s' "$line"
}

setting() {  # the environment wins, then the env file
    local key="$1" val="${!1:-}"
    [[ -n "$val" ]] || val="$(env_value "$key")"
    printf '%s' "$val"
}

DATA_DIR="$(setting MERIDIAN_DATA_DIR)"; DATA_DIR="${DATA_DIR:-/var/lib/meridian}"
DB="${MERIDIAN_DB_PATH:-$DATA_DIR/meridian.db}"
BACKUP_DIR="${MERIDIAN_BACKUP_DIR:-$DATA_DIR/backups}"
KEEP_DAYS="${MERIDIAN_BACKUP_KEEP_DAYS:-30}"
REMOTE="$(setting MERIDIAN_BACKUP_REMOTE)"
URL="$(setting MERIDIAN_BACKUP_URL)"
PASSPHRASE="$(setting MERIDIAN_BACKUP_PASSPHRASE)"

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
find "$BACKUP_DIR" -name 'meridian-*.gz.enc' -mtime "+$KEEP_DAYS" -delete
echo "pruned backups older than $KEEP_DAYS days"

# ── off the machine ──────────────────────────────────────────────────────────
off="not set up"      # ok | failed | not set up
target=""
encrypted=false
send="$out.gz"

if [[ -n "$REMOTE" || -n "$URL" ]]; then
    if [[ -n "$PASSPHRASE" ]]; then
        export MERIDIAN_BACKUP_PASSPHRASE="$PASSPHRASE"
        if openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
                -in "$out.gz" -out "$out.gz.enc" -pass env:MERIDIAN_BACKUP_PASSPHRASE; then
            send="$out.gz.enc"
            encrypted=true
        else
            echo "ENCRYPTION FAILED — the copy was not sent off the machine" >&2
            off="failed"
        fi
    elif [[ -n "$URL" ]]; then
        echo "MERIDIAN_BACKUP_URL is set without MERIDIAN_BACKUP_PASSPHRASE — an unencrypted" >&2
        echo "database is never uploaded. Set the passphrase; this backup stays on this VM only." >&2
        off="failed"
    else
        echo "note: no MERIDIAN_BACKUP_PASSPHRASE — the copy goes over scp unencrypted" >&2
    fi
fi

if [[ "$off" != "failed" && -n "$URL" ]]; then
    target="https"
    case "$URL" in
        https://*) ;;
        *) echo "MERIDIAN_BACKUP_URL must start with https://" >&2; off="failed" ;;
    esac
    if [[ "$off" != "failed" ]]; then
        if curl -fsS --max-time 600 -T "$send" "${URL}$(basename "$send")" >/dev/null; then
            off="ok"
            echo "uploaded off-box (encrypted)"
        else
            echo "OFF-BOX UPLOAD FAILED — this backup exists only on this VM" >&2
            off="failed"
        fi
    fi
fi

if [[ "$off" != "failed" && -n "$REMOTE" ]]; then
    target="${target:+$target+}scp"
    if scp -q -o BatchMode=yes -o ConnectTimeout=20 "$send" "$REMOTE"; then
        off="ok"
        echo "copied off-box by scp"
    else
        # A failed off-box copy must be loud: the local copy alone does not
        # survive losing the VM, which is the case this exists for.
        echo "OFF-BOX COPY FAILED — this backup exists only on this VM" >&2
        off="failed"
    fi
fi

# The encrypted file is only for sending; the local copy stays as the .gz.
[[ "$encrypted" == true ]] && rm -f "$out.gz.enc"

# What happened, for the dashboard and the API's alert. No address, no passphrase.
"$PY_BIN" - "$BACKUP_DIR/last-backup.json" "$(basename "$out.gz")" "$out.gz" "$off" "$target" "$encrypted" <<'PYEOF'
import json
import os
import sys
import time

path, name, file, off, target, encrypted = sys.argv[1:7]
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "file": name,
        "bytes": os.path.getsize(file),
        "verified": True,
        "off_machine": off,
        "target": target or None,
        "encrypted": encrypted == "true",
    }, fh)
os.replace(tmp, path)
os.chmod(path, 0o644)
PYEOF

if [[ "$off" == "failed" ]]; then
    exit 1
fi
if [[ "$off" == "not set up" ]]; then
    echo "note: no off-machine target is set (MERIDIAN_BACKUP_REMOTE or MERIDIAN_BACKUP_URL in $ENV_FILE)."
    echo "      This backup exists only on this VM."
fi
