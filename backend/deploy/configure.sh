#!/usr/bin/env bash
# One-time setup of the settings only the owner can provide, without editing files by hand:
#
#   1. outside monitor   MERIDIAN_HEARTBEAT_URL (a healthchecks.io ping address)
#   2. alerts            MERIDIAN_TELEGRAM_BOT_TOKEN + MERIDIAN_TELEGRAM_CHAT_ID (a test message is sent)
#   3. off-VM backups    MERIDIAN_BACKUP_URL + MERIDIAN_BACKUP_PASSPHRASE (a backup is run and uploaded)
#   4. after-close check ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD (PIN), ANGEL_TOTP_SECRET
#
#   sudo bash ~/meridiancapital/backend/deploy/configure.sh
#
# Each step can be skipped with Enter. Secrets are typed hidden and never printed, logged or put on a command line
# (other users cannot see them in the process list). /etc/meridian/meridian.env is backed up first and keeps its
# permissions (root:meridian 640). Run it outside market hours: it restarts the dashboard's API at the end (a
# running trading program is left running and picked up again).
set -euo pipefail
umask 077

ENV_FILE="${MERIDIAN_ENV_FILE:-/etc/meridian/meridian.env}"
APP_USER="meridian"
[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo bash $0" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "$ENV_FILE not found: run install.sh first." >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is needed" >&2; exit 1; }

cp -p "$ENV_FILE" "$ENV_FILE.bak-$(date +%Y%m%d-%H%M%S)"
changed=0

# set_kv KEY: the value comes from the environment variable VAL (never argv), replacing KEY= or appending it.
set_kv() {
    KEY="$1" ENV_FILE="$ENV_FILE" python3 - <<'PY'
import os, re
path, key, val = os.environ["ENV_FILE"], os.environ["KEY"], os.environ["VAL"]
if "\n" in val or "\r" in val:
    raise SystemExit("a value may not contain a line break")
lines = open(path, encoding="utf-8").read().splitlines()
out, done = [], False
for line in lines:
    if re.match(rf"^\s*{re.escape(key)}\s*=", line):
        if not done:
            out.append(f"{key}={val}")
            done = True
        continue
    out.append(line)
if not done:
    out.append(f"{key}={val}")
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
st = os.stat(path)
os.chown(tmp, st.st_uid, st.st_gid)
os.chmod(tmp, st.st_mode & 0o777)
os.replace(tmp, path)
PY
    changed=1
}

ask() {      # ask VAR "prompt"          (visible)
    local __v; read -r -p "$2" __v || true; printf -v "$1" '%s' "$__v"
}
ask_secret() {   # ask_secret VAR "prompt"   (hidden)
    local __v; read -r -s -p "$2" __v || true; echo; printf -v "$1" '%s' "$__v"
}
has() { grep -qE "^\s*$1=.+" "$ENV_FILE"; }
state() { if has "$1"; then echo "set"; else echo "not set"; fi; }

echo
echo "Meridian — owner settings ($ENV_FILE). Press Enter at any question to skip it."

# ── 1. outside monitor ───────────────────────────────────────────────────────
echo
echo "1/4  Outside monitor — tells you if the VM or the dashboard goes down (now: $(state MERIDIAN_HEARTBEAT_URL))."
echo "     At https://healthchecks.io (free): New check -> Period 1 minute, Grace 2 minutes -> add your email or"
echo "     Telegram under Integrations -> copy the ping URL (https://hc-ping.com/...)."
ask hb "     Ping URL: "
if [[ -n "$hb" ]]; then
    if [[ "$hb" != https://* ]]; then
        echo "     Not saved: it must start with https://"
    else
        VAL="$hb" set_kv MERIDIAN_HEARTBEAT_URL
        if curl -fsS -m 10 -o /dev/null -K - <<<"url = \"$hb\""; then
            echo "     Saved, and a first ping was sent: the check should turn green on healthchecks.io."
        else
            echo "     Saved, but the test ping failed — check the URL on healthchecks.io."
        fi
    fi
fi

# ── 2. alerts ────────────────────────────────────────────────────────────────
echo
echo "2/4  Alerts on your phone — trade in/out, kill switches, crashes (now: $(state MERIDIAN_TELEGRAM_BOT_TOKEN))."
echo "     In Telegram: message @BotFather, send /newbot, follow it, copy the token. Then send your new bot any"
echo "     message (say 'hi') so it may write to you."
ask_secret tg "     Bot token (hidden): "
if [[ -n "$tg" ]]; then
    chat="$(curl -fsS -m 15 -K - <<<"url = \"https://api.telegram.org/bot${tg}/getUpdates\"" 2>/dev/null \
        | python3 -c 'import json,sys
try:
    r = json.load(sys.stdin).get("result") or []
    ids = [u.get("message", {}).get("chat", {}).get("id") for u in r if u.get("message")]
    print(ids[-1] if ids else "")
except Exception:
    print("")' || true)"
    if [[ -z "$chat" ]]; then
        ask chat "     Could not find your chat id (did you message the bot?). Chat id, or Enter to skip: "
    fi
    if [[ -n "$chat" ]]; then
        reply="$(curl -fsS -m 15 -K - <<<"url = \"https://api.telegram.org/bot${tg}/sendMessage\"
data-urlencode = \"chat_id=${chat}\"
data-urlencode = \"text=Meridian alerts are on. You will hear about trades, kill switches and crashes here.\"" 2>/dev/null || true)"
        if [[ "$reply" == *'"ok":true'* ]]; then
            VAL="$tg" set_kv MERIDIAN_TELEGRAM_BOT_TOKEN
            VAL="$chat" set_kv MERIDIAN_TELEGRAM_CHAT_ID
            echo "     Saved. A test message was sent to your Telegram."
        else
            echo "     Not saved: Telegram refused the test message — check the token and that you messaged the bot."
        fi
    fi
fi
unset tg

# ── 3. backups off the VM ────────────────────────────────────────────────────
echo
echo "3/4  Backups off the VM — the nightly backup also goes to Oracle storage (now: $(state MERIDIAN_BACKUP_URL))."
echo "     OCI console -> Storage -> Buckets -> Create bucket 'meridian-backups' -> open it -> Pre-Authenticated"
echo "     Requests -> Create: 'Bucket', 'Permit object reads and writes', expiry a few years ahead -> copy the URL."
ask_secret bu "     Pre-authenticated URL (hidden; it must end with /): "
if [[ -n "$bu" ]]; then
    if [[ "$bu" != https://* || "$bu" != */ ]]; then
        echo "     Not saved: it must start with https:// and end with /"
    else
        ask_secret pp "     Passphrase to encrypt the copies (hidden; Enter = make a strong one for you): "
        made=0
        if [[ -z "$pp" ]]; then pp="$(openssl rand -base64 30 | tr -d '\n=+/' | cut -c1-32)"; made=1; fi
        VAL="$bu" set_kv MERIDIAN_BACKUP_URL
        VAL="$pp" set_kv MERIDIAN_BACKUP_PASSPHRASE
        if [[ $made -eq 1 ]]; then
            echo
            echo "     ┌──────────────────────────────────────────────────────────────────────┐"
            echo "     │ Your backup passphrase (shown once — save it in a password manager,  │"
            echo "     │ NOT on this VM; without it the off-VM copies cannot be opened):      │"
            printf  "     │   %-67s│\n" "$pp"
            echo "     └──────────────────────────────────────────────────────────────────────┘"
            read -r -p "     Press Enter once it is saved somewhere safe. " _ || true
            clear 2>/dev/null || true
        fi
        echo "     Running a backup now and uploading it (a minute or two)..."
        if systemctl start meridian-backup.service; then
            echo "     Backup done. The Reliability page shows where it went."
        else
            echo "     The backup run reported a problem: journalctl -u meridian-backup -n 30 --no-pager"
        fi
    fi
fi
unset bu pp

# ── 4. Angel for the after-close check ───────────────────────────────────────
echo
echo "4/4  Angel One for the after-close check — from 15:35 the desk reads Angel's position and trade books"
echo "     (read-only) and checks them against its own record (now: $(state ANGEL_API_KEY))."
echo "     The same four values your trading program uses; they are never shown."
ask how "     [p] copy them from your uploaded program, [t] type them (hidden), Enter to skip: "
case "${how,,}" in
    p|t)
        py="${MERIDIAN_VENV_PY:-/opt/meridian/backend/.venv/bin/python}"
        tool="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../scripts/set_angel_credentials.py"
        env_val() { grep -E "^[[:space:]]*$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }
        db="$(env_val MERIDIAN_DB_PATH)"; data="$(env_val MERIDIAN_DATA_DIR)"
        args=(--env "$ENV_FILE" --db "${db:-${data:-/var/lib/meridian}/meridian.db}" --no-restart)
        [[ "${how,,}" == p ]] && args+=(--from-program)
        if [[ ! -x "$py" || ! -f "$tool" ]]; then
            echo "     Skipped: $py or $tool is missing — run install.sh first."
        else
            before="$(sha256sum "$ENV_FILE")"
            # The existing tool checks each value (the TOTP seed by making a code) and never prints one.
            "$py" -u "$tool" "${args[@]}" | sed -e '/Restart the API to use them/d' -e 's/^/     /' || echo "     Not saved."
            if [[ "$(sha256sum "$ENV_FILE")" != "$before" ]]; then
                changed=1
                echo "     The API is restarted at the end of this script; today's 15:35 check will log in and confirm."
            fi
        fi
        ;;
esac

chown root:"$APP_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"
echo
if [[ $changed -eq 1 ]]; then
    echo "Restarting the dashboard API to read the new settings (a running trading program keeps running)..."
    systemctl restart meridian-api
    for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1 && break; sleep 1; done
    echo "Done. Open the Reliability page: the items you set should no longer say 'not set up'."
else
    echo "Nothing changed."
fi
echo "Settings now: monitor $(state MERIDIAN_HEARTBEAT_URL) · alerts $(state MERIDIAN_TELEGRAM_BOT_TOKEN) · off-VM backups $(state MERIDIAN_BACKUP_URL) · Angel $(state ANGEL_API_KEY)"
