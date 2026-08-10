#!/usr/bin/env bash
# Meridian Capital — one-shot bootstrap.
#
# Takes a bare server all the way to a live HTTPS address in a single run:
# Docker, swap, the repository, credentials, the container, Tailscale, Funnel.
#
#   curl -fsSL https://raw.githubusercontent.com/sehejsharm/meridiancapital/main/deploy/bootstrap.sh | sudo bash
#
# Safe to re-run. Anything already done is detected and skipped, and questions
# already answered are not asked again — so if it fails halfway, run it again.
#
# Prompts are read from /dev/tty rather than stdin, because stdin is the pipe
# carrying this script when it is curled into bash.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/sehejsharm/meridiancapital.git}"
REPO_DIR="${REPO_DIR:-/opt/meridiancapital}"
BRANCH="${BRANCH:-main}"
RAW="https://raw.githubusercontent.com/sehejsharm/meridiancapital/${BRANCH}/deploy"
PORT="${PORT:-8000}"
ENV_FILE="$REPO_DIR/.env"

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; BOLD=$'\033[1m'; OFF=$'\033[0m'
say()  { echo "${GREEN}==>${OFF} $*"; }
warn() { echo "${YELLOW}==>${OFF} $*"; }
die()  { echo "${RED}==>${OFF} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run with sudo."
[[ -e /dev/tty ]] || die "No terminal available — this script has questions to ask.
Run it from an interactive SSH session, not from a script or cron job."

banner() {
  echo
  echo "${BOLD}────────────────────────────────────────────────────────────${OFF}"
  echo "${BOLD}  $*${OFF}"
  echo "${BOLD}────────────────────────────────────────────────────────────${OFF}"
}

# ---------------------------------------------------------------- .env helpers

env_get() {
  [[ -f "$ENV_FILE" ]] || { echo ""; return; }
  sed -n "s/^$1=//p" "$ENV_FILE" | head -1
}

# Written through python3 rather than sed so that passwords containing slashes,
# ampersands or quotes survive intact.
env_set() {
  python3 - "$ENV_FILE" "$1" "$2" <<'PY'
import sys, pathlib
path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path)
lines = p.read_text().splitlines() if p.exists() else []
out, done = [], False
for line in lines:
    if line.startswith(key + "="):
        out.append(f"{key}={value}")
        done = True
    else:
        out.append(line)
if not done:
    out.append(f"{key}={value}")
p.write_text("\n".join(out) + "\n")
PY
}

# ask VAR "Question" [secret] — skips anything already answered.
ask() {
  local var="$1" prompt="$2" secret="${3:-}" val=""
  [[ -n "$(env_get "$var")" ]] && return 0
  while [[ -z "$val" ]]; do
    if [[ -n "$secret" ]]; then
      read -rs -p "    ${prompt}: " val < /dev/tty || true
      echo
    else
      read -r -p "    ${prompt}: " val < /dev/tty || true
    fi
    [[ -z "$val" ]] && echo "    ${YELLOW}(required)${OFF}"
  done
  env_set "$var" "$val"
}

run_setup() {
  if [[ -f "$REPO_DIR/deploy/setup.sh" ]]; then
    REPO_URL="$REPO_URL" MERIDIAN_BOOTSTRAP=1 bash "$REPO_DIR/deploy/setup.sh"
  else
    REPO_URL="$REPO_URL" MERIDIAN_BOOTSTRAP=1 bash <(curl -fsSL "$RAW/setup.sh")
  fi
}

# ---------------------------------------------------------------- 1. the box

banner "Step 1 of 4 — installing Docker and fetching the code"
run_setup   # exits 0 after writing the .env skeleton; that is the expected path

[[ -f "$ENV_FILE" ]] || die "Setup did not produce $ENV_FILE — nothing to configure."

# ---------------------------------------------------------------- 2. secrets

if [[ -z "$(env_get ADMIN_PASSWORD)" || -z "$(env_get ANGEL_API_KEY)" ]]; then
  banner "Step 2 of 4 — your credentials"
  cat <<'TXT'

  These are stored only in /opt/meridiancapital/.env on this server, with
  permissions that allow nobody but root to read the file. Typing is hidden
  for the secret ones, so the screen staying blank is expected.

  The five ANGEL_ values come from your Angel One SmartAPI dashboard.

TXT
  echo "  ${BOLD}Dashboard login${OFF} — what you will type into the web app:"
  ask ADMIN_USER      "Username (e.g. Sehej)"
  ask ADMIN_PASSWORD  "Password (choose one)" secret
  echo
  echo "  ${BOLD}Angel One SmartAPI${OFF}:"
  ask ANGEL_API_KEY     "API key"
  ask ANGEL_SECRET_KEY  "Secret key"      secret
  ask ANGEL_CLIENT_ID   "Client ID"
  ask ANGEL_PASSWORD    "Account PIN"     secret
  ask ANGEL_TOTP_SECRET "TOTP secret"     secret
  chmod 600 "$ENV_FILE"
  echo
  say "Credentials saved."
else
  banner "Step 2 of 4 — credentials already present, skipping"
fi

# ---------------------------------------------------------------- 3. run it

banner "Step 3 of 4 — building and starting the bot"
run_setup

# ---------------------------------------------------------------- 4. HTTPS

banner "Step 4 of 4 — putting it on the internet over HTTPS"

if ! command -v tailscale >/dev/null 2>&1; then
  say "Installing Tailscale"
  curl -fsSL https://tailscale.com/install.sh | sh
fi

if ! tailscale status >/dev/null 2>&1; then
  cat <<'TXT'

  Tailscale needs you to sign in once. A link appears below — open it in any
  browser and approve. Use the same account your other devices are on.

TXT
  tailscale up --hostname=meridian < /dev/tty || die "Tailscale sign-in did not complete. Re-run this script to retry."
fi
say "Tailscale is connected"

# Funnel is what makes the bot reachable from the public internet over HTTPS.
# It dials outward, so no firewall rule and no cloud security-list change is
# needed — and the API token never sits behind a bare HTTP port.
if ! tailscale funnel --bg "$PORT" 2>/tmp/funnel.err; then
  echo
  warn "Tailscale would not switch Funnel on. It reported:"
  echo
  sed 's/^/    /' /tmp/funnel.err
  cat <<'TXT'

  This is almost always one of two settings on the Tailscale website:

    1. HTTPS certificates are off.
       Turn on at:  https://login.tailscale.com/admin/dns
       ("MagicDNS" and "HTTPS Certificates" both need to be enabled.)

    2. The tailnet policy does not grant the "funnel" attribute.
       Add at:      https://login.tailscale.com/admin/acls

         "nodeAttrs": [
           { "target": ["autogroup:member"], "attr": ["funnel"] }
         ]

  Fix either one, then run this same command again. Everything else is
  already done, so it will pick up right here.

TXT
  exit 1
fi

ADDR="$(tailscale status --json 2>/dev/null \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' \
  2>/dev/null || true)"

# ---------------------------------------------------------------- done

banner "Done"

if [[ -n "$ADDR" ]]; then
  echo
  echo "  ${BOLD}Open your dashboard and sign in with:${OFF}"
  echo
  echo "    Server    ${GREEN}https://${ADDR}${OFF}"
  echo "    Operator  ${GREEN}$(env_get ADMIN_USER)${OFF}"
  echo "    Passcode  the password you chose a minute ago"
else
  echo
  warn "Could not read the address back. Run: sudo tailscale funnel status"
fi

cat <<'TXT'

  Two things left, both on the Tailscale website, both one click:

    1. https://login.tailscale.com/admin/machines
       Find "meridian", open the ⋯ menu, choose "Disable key expiry".
       Without this the server drops off the network in about six months
       and the dashboard silently loses the bot.

    2. Whitelist this server's IP in your Angel One SmartAPI dashboard,
       or the bot will start and then fail at broker login.

  The bot runs on its own from here: it starts at 09:15 IST, stops at 15:45,
  every trading day, whether or not anything is connected to it.

TXT
