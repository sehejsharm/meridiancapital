#!/usr/bin/env bash
#
# Move the backend from one VM to another: the database, every algorithm and
# program file, the engine's state, the credentials, and any extra Python
# packages installed for programs. The code itself comes from git, as always.
#
#   On the OLD VM:  sudo bash backend/deploy/migrate.sh export
#   On the NEW VM:  sudo bash backend/deploy/migrate.sh import ~/meridian-move-<stamp>.tar.gz
#
# export refuses while an engine or program is running, then stops AND disables
# the API on the old VM before it copies anything. Two VMs both armed would both
# start the same algorithms at 09:05 — on real money, every order twice. And
# nothing written on the old VM after the copy would reach the new one.
# To roll back:  sudo systemctl enable --now meridian-api   (on the old VM)
#
# import expects install.sh to have run on the new VM already (it builds the
# service account, the venv and the unit). It sets aside whatever data and env
# file the new VM had, restores the bundle, and starts the API.
#
# The bundle holds the Angel One credentials. It is written mode 0600; delete
# it from both VMs, and anywhere you copied it through, once the move is done.

set -euo pipefail

APP_USER="meridian"
APP_DIR="${MERIDIAN_APP_DIR:-/opt/meridian}"
ENV_FILE="${MERIDIAN_ENV_FILE:-/etc/meridian/meridian.env}"
VENV_PY="${APP_DIR}/backend/.venv/bin/python"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[x] %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run with sudo."

# The data directory the service actually uses: the env file's value if it sets
# one, the install default otherwise.
data_dir() {
    local d=""
    [[ -f "$ENV_FILE" ]] && d="$(grep -m1 -E '^MERIDIAN_DATA_DIR=' "$ENV_FILE" | cut -d= -f2- | tr -d '"'"'" || true)"
    echo "${d:-/var/lib/meridian}"
}

# Engines and programs run detached from the API, so stopping the API does not
# stop them. Anything still running holds the database open and may hold a
# position; copying under it would capture neither correctly.
running_traders() {
    pgrep -u "$APP_USER" -af "engine\.runner|$(data_dir)/programs/" 2>/dev/null || true
}

refuse_if_trading() {
    local procs
    procs="$(running_traders)"
    if [[ -n "$procs" ]]; then
        echo "$procs" | sed 's/^/    /' >&2
        die "An engine or program is still running on this VM (above). Stop every algorithm from the dashboard, outside market hours, and run this again."
    fi
}

# Consistent copy of a live WAL database through SQLite's backup API, then
# verified — the same method backup.sh uses.
sqlite_copy() {
    local py="$VENV_PY"
    [[ -x "$py" ]] || py="$(command -v python3)"
    "$py" - "$1" "$2" <<'PYEOF'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
src.close()
check = dst.execute("PRAGMA integrity_check").fetchone()[0]
trades = dst.execute("SELECT COUNT(*) FROM trades").fetchone()[0] if dst.execute(
    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades'").fetchone() else 0
dst.close()
if check != "ok":
    sys.exit(f"integrity check failed: {check}")
print(f"    database copied and verified ({trades} trades)")
PYEOF
}

public_ip() { curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true; }

# ── export ────────────────────────────────────────────────────────────────────
do_export() {
    local DATA_DIR DB stamp out owner domain origin
    DATA_DIR="$(data_dir)"
    DB="${DATA_DIR}/meridian.db"
    [[ -f "$DB" ]] || die "No database at $DB — is this the VM the backend runs on?"
    [[ -f "$ENV_FILE" ]] || die "No $ENV_FILE on this VM."

    log "Checking nothing is trading"
    refuse_if_trading
    echo "    nothing running"

    log "Stopping and disabling the API on this VM"
    systemctl stop meridian-api 2>/dev/null || true
    systemctl disable meridian-api >/dev/null 2>&1 || true
    systemctl disable --now meridian-backup.timer >/dev/null 2>&1 || true
    refuse_if_trading
    echo "    stopped; it will not start again at boot"

    stamp="$(date +%Y%m%d-%H%M%S)"
    work="$(mktemp -d)"
    trap 'rm -rf "$work"' EXIT
    mkdir -p "$work/data"

    log "Copying the database"
    sqlite_copy "$DB" "$work/data/meridian.db"

    log "Copying algorithms, programs and engine state"
    # Everything in the data directory except the live database files (copied
    # above), the old VM's own backups, and pid files that mean nothing on
    # another machine.
    tar -C "$DATA_DIR" --no-wildcards-match-slash \
        --exclude='./meridian.db' --exclude='./meridian.db-wal' --exclude='./meridian.db-shm' \
        --exclude='./backups' --exclude='./engine*.pid' \
        -cf - . | tar -C "$work/data" -xf -
    du -sh "$work/data" | awk '{print "    " $1 " in total"}'

    log "Copying credentials and settings"
    cp "$ENV_FILE" "$work/meridian.env"
    "$VENV_PY" -m pip freeze --exclude-editable 2>/dev/null > "$work/pip-freeze.txt" || : > "$work/pip-freeze.txt"

    domain="$(grep -m1 -oE '^[A-Za-z0-9.-]+\.[A-Za-z]{2,}' /etc/caddy/Caddyfile 2>/dev/null || true)"
    origin="$(grep -m1 -oE 'not header Origin https://[^[:space:]]+' /etc/caddy/Caddyfile 2>/dev/null | awk '{print $4}' || true)"
    {
        echo "DOMAIN=${domain}"
        echo "ORIGIN=${origin}"
        echo "OLD_IP=$(public_ip)"
        echo "OLD_ARCH=$(uname -m)"
        echo "EXPORTED=${stamp}"
    } > "$work/source.txt"

    owner="${SUDO_USER:-root}"
    out="$(getent passwd "$owner" | cut -d: -f6)/meridian-move-${stamp}.tar.gz"
    tar -C "$work" -czf "$out" .
    chown "$owner": "$out"
    chmod 600 "$out"

    cat <<EOF

────────────────────────────────────────────────────────────────────────
Bundle: $out  ($(du -h "$out" | cut -f1))
It contains your Angel One credentials. Delete it once the move is done.

The API on this VM is stopped and disabled. Nothing here will trade.
Roll back at any time:  sudo systemctl enable --now meridian-api

On the NEW VM:
  git clone https://github.com/sehejsharm/meridiancapital.git
  cd meridiancapital
  sudo bash backend/deploy/install.sh ${domain:-api.your-host.example} ${origin:-https://your-app.vercel.app}
  sudo bash backend/deploy/migrate.sh import ~/$(basename "$out")
────────────────────────────────────────────────────────────────────────
EOF
}

# ── import ────────────────────────────────────────────────────────────────────
do_import() {
    local bundle="${1:-}" DATA_DIR stamp domain origin old_ip new_ip declared
    [[ -n "$bundle" && -f "$bundle" ]] || die "Usage: sudo bash $0 import <meridian-move-....tar.gz>"
    [[ -x "$VENV_PY" ]] || die "Run install.sh on this VM first — it builds the venv and the service."
    id -u "$APP_USER" >/dev/null 2>&1 || die "No '$APP_USER' account — run install.sh on this VM first."

    work="$(mktemp -d)"
    trap 'rm -rf "$work"' EXIT
    tar -C "$work" -xzf "$bundle"
    [[ -f "$work/data/meridian.db" && -f "$work/meridian.env" ]] || die "$bundle is not a migrate.sh bundle."

    log "Checking the bundle"
    sqlite_copy "$work/data/meridian.db" "$work/check.db"
    rm -f "$work/check.db"

    log "Checking nothing is trading on this VM"
    refuse_if_trading
    systemctl stop meridian-api 2>/dev/null || true
    refuse_if_trading
    echo "    nothing running; API stopped"

    stamp="$(date +%Y%m%d-%H%M%S)"

    log "Restoring credentials and settings"
    if [[ -f "$ENV_FILE" ]]; then
        cp -p "$ENV_FILE" "${ENV_FILE}.before-import-${stamp}"
        echo "    this VM's previous env file kept as ${ENV_FILE}.before-import-${stamp}"
    fi
    mkdir -p "$(dirname "$ENV_FILE")"
    install -o root -g "$APP_USER" -m 640 "$work/meridian.env" "$ENV_FILE"

    DATA_DIR="$(data_dir)"
    log "Restoring data into $DATA_DIR"
    if [[ -d "$DATA_DIR" ]] && [[ -n "$(ls -A "$DATA_DIR" 2>/dev/null)" ]]; then
        mv "$DATA_DIR" "${DATA_DIR}.before-import-${stamp}"
        echo "    this VM's previous data kept as ${DATA_DIR}.before-import-${stamp}"
    fi
    mkdir -p "$DATA_DIR"
    cp -a "$work/data/." "$DATA_DIR/"
    chown -R "$APP_USER:$APP_USER" "$DATA_DIR"
    chmod 750 "$DATA_DIR"
    du -sh "$DATA_DIR" | awk '{print "    " $1 " restored"}'

    log "Extra Python packages installed for programs on the old VM"
    # Anything the operator pip-installed into the venv by hand for a program
    # (the dashboard's install hint). requirements.txt is already in place from
    # install.sh, so only the difference is installed. Versions are kept; a
    # package with no ARM build, or one that needs a compiler, is reported
    # rather than stopping the move.
    "$VENV_PY" - "$work/pip-freeze.txt" > "$work/extras.txt" <<'PYEOF'
import re, subprocess, sys
norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()
have = {norm(l.split("==")[0]) for l in subprocess.run(
    [sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True).stdout.splitlines() if "==" in l}
for line in open(sys.argv[1]):
    line = line.strip()
    if "==" in line and not line.startswith("#") and norm(line.split("==")[0]) not in have:
        print(line)
PYEOF
    if [[ -s "$work/extras.txt" ]]; then
        while read -r pkg; do
            if sudo -u "$APP_USER" "$VENV_PY" -m pip install -q "$pkg" >/dev/null 2>&1 \
               || sudo -u "$APP_USER" "$VENV_PY" -m pip install -q "${pkg%%==*}" >/dev/null 2>&1; then
                echo "    installed ${pkg}"
            else
                warn "could not install ${pkg} — a program that imports it will report it on start"
            fi
        done < "$work/extras.txt"
    else
        echo "    none"
    fi

    log "Angel One IP check"
    # shellcheck disable=SC1091
    . "$work/source.txt"
    domain="${DOMAIN:-}"; origin="${ORIGIN:-}"; old_ip="${OLD_IP:-}"
    new_ip="$(public_ip)"
    declared="$(grep -m1 -E '^ANGEL_PUBLIC_IP=' "$ENV_FILE" | cut -d= -f2- | tr -d '"'"'" || true)"
    echo "    old VM ${old_ip:-unknown}  ->  this VM ${new_ip:-unknown}"
    # Programs uploaded through the dashboard may carry the whitelisted IP in
    # their own source and refuse to start anywhere else. List them: they are
    # the reason moving the old VM's reserved IP beats changing it.
    local pinned=""
    if [[ -n "$old_ip" ]]; then
        pinned="$(grep -rlF --include='*.py' "$old_ip" "$DATA_DIR/programs" "$DATA_DIR/algos" 2>/dev/null \
                  | sed "s#^$DATA_DIR/##" || true)"
        if [[ -n "$pinned" ]]; then
            echo "    these algorithms name ${old_ip} in their own code:"
            echo "$pinned" | sed 's/^/      /'
        fi
    fi
    if [[ -n "$old_ip" && -n "$new_ip" && "$old_ip" != "$new_ip" ]]; then
        warn "This VM is not on ${old_ip} yet, so nothing will trade live from here until it is."
        warn "Recommended: move the reserved public IP ${old_ip} from the old VM to this one"
        warn "(DEPLOYMENT.md, 'Moving to a new VM'). Then Angel, DNS, Vercel, ANGEL_PUBLIC_IP"
        warn "and the algorithms above all stay exactly as they are."
        warn "Only if ${old_ip} is not a reserved IP: whitelist ${new_ip} at smartapi.angelone.in,"
        [[ -n "$declared" ]] && warn "set ANGEL_PUBLIC_IP=${new_ip} in ${ENV_FILE},"
        [[ -n "$pinned" ]] && warn "change the IP inside each algorithm listed above and upload it again,"
        warn "and point ${domain:-your hostname} at ${new_ip}."
    fi

    log "Starting the API"
    systemctl enable meridian-api >/dev/null 2>&1
    systemctl enable --now meridian-backup.timer >/dev/null 2>&1 || true
    systemctl restart meridian-api
    local healthy=""
    for _ in $(seq 1 30); do
        if curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1; then healthy=1; break; fi
        sleep 1
    done
    if [[ -n "$healthy" ]]; then
        echo "    API healthy: $(curl -fsS http://127.0.0.1:8080/health)"
    else
        warn "API did not answer within 30s — see: journalctl -u meridian-api -n 50 --no-pager"
    fi

    # Defaults set here, not inside the heredoc: an apostrophe in a ${x:-...}
    # default there is read as a quote and swallows the lines after it.
    local ip="${old_ip:-the old public IP}"
    cat <<EOF

────────────────────────────────────────────────────────────────────────
Restored. To finish:

  1. Move the reserved public IP ${ip} from the old VM to this one
     (OCI console; DEPLOYMENT.md, 'Moving to a new VM'). SSH back in on ${ip}.
  2. sudo systemctl restart caddy        (fetches the certificate on the new VM)
  3. Check from your laptop:  curl -s https://${domain:-your-host}/health
     then open the dashboard. Vercel, DuckDNS and Angel need no change.
  4. Delete the bundle here, on the old VM and anywhere you copied it through.
────────────────────────────────────────────────────────────────────────
EOF
}

case "${1:-}" in
    export) do_export ;;
    import) shift; do_import "${1:-}" ;;
    *) echo "Usage: sudo bash $0 export | import <bundle.tar.gz>" >&2; exit 2 ;;
esac
