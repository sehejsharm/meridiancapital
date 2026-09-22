#!/usr/bin/env bash
#
# Meridian Capital — one-time provisioning for an Oracle Cloud Always Free VM.
#
#   sudo bash deploy/install.sh api.your-host.example
#
# Idempotent: safe to re-run after a code update. It never touches
# /etc/meridian/meridian.env once that file exists, so your credentials survive.
#
# Before running, in the OCI console: VCN > Security List > add ingress rules for
# TCP 80 and 443 from 0.0.0.0/0. The console rule and the local firewall rule
# below are two separate gates and traffic needs both.

set -euo pipefail

DOMAIN="${1:-}"
# The dashboard's own origin. Caddy 403s any browser request whose Origin is not
# on the allow-list, and the WebSocket live feed does send one — so a wrong value
# here shows up as a dashboard that logs in fine and then never updates.
DASHBOARD_ORIGIN="${2:-}"
APP_USER="meridian"
APP_DIR="/opt/meridian"
DATA_DIR="/var/lib/meridian"
ENV_DIR="/etc/meridian"
ENV_FILE="${ENV_DIR}/meridian.env"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }

log "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates \
    debian-keyring debian-archive-keyring apt-transport-https chrony netfilter-persistent

log "Clock discipline"
# The engine refuses to start if NTP says the clock is off by more than 30s,
# because a skewed clock would move the entry and force-close windows.
systemctl enable --now chrony
timedatectl set-timezone UTC
chronyc -a makestep >/dev/null 2>&1 || true

log "Service account and directories"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR" "$DATA_DIR" "$ENV_DIR"
chown -R "$APP_USER:$APP_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"

log "Application code"
if [[ "$REPO_DIR" != "$APP_DIR" ]]; then
    rsync -a --delete \
        --exclude '.git' --exclude '.venv' --exclude 'frontend/node_modules' \
        --exclude '__pycache__' --exclude '.next' \
        "$REPO_DIR/" "$APP_DIR/"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

log "Python environment"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/backend/.venv"
sudo -u "$APP_USER" "$APP_DIR/backend/.venv/bin/pip" install -q --upgrade pip wheel
sudo -u "$APP_USER" "$APP_DIR/backend/.venv/bin/pip" install -q -r "$APP_DIR/backend/requirements.txt"

log "Environment file"
if [[ ! -f "$ENV_FILE" ]]; then
    cp "$APP_DIR/backend/deploy/meridian.env.example" "$ENV_FILE"
    warn "Created $ENV_FILE from the template — it is NOT yet usable."
    warn "Fill in the Angel One credentials, then run:"
    warn "  sudo -u $APP_USER $APP_DIR/backend/.venv/bin/python \\"
    warn "       $APP_DIR/backend/scripts/bootstrap_secrets.py"
else
    echo "    $ENV_FILE already exists — left untouched."
fi
chown root:"$APP_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"

log "systemd unit"
install -m 644 "$APP_DIR/backend/deploy/meridian-api.service" /etc/systemd/system/
install -m 644 "$APP_DIR/backend/deploy/meridian-backup.service" /etc/systemd/system/
install -m 644 "$APP_DIR/backend/deploy/meridian-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable meridian-api
systemctl enable --now meridian-backup.timer

log "Caddy (TLS termination)"
if ! command -v caddy >/dev/null 2>&1; then
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
        | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    apt-get update -qq
    apt-get install -y -qq caddy
fi
mkdir -p /var/log/caddy && chown caddy:caddy /var/log/caddy

if [[ -n "$DOMAIN" ]]; then
    sed "s/api\.meridiancapital\.example/${DOMAIN}/g" \
        "$APP_DIR/backend/deploy/Caddyfile" > /etc/caddy/Caddyfile
    echo "    Caddyfile written for ${DOMAIN}"

    if [[ -n "$DASHBOARD_ORIGIN" ]]; then
        sed -i "s#https://meridian\.vercel\.app#${DASHBOARD_ORIGIN}#g" /etc/caddy/Caddyfile
        echo "    Origin allow-list set to ${DASHBOARD_ORIGIN}"
    else
        warn "No dashboard origin given — the Origin allow-list still names the"
        warn "placeholder https://meridian.vercel.app, which will 403 your live feed."
        warn "Re-run as: sudo bash deploy/install.sh ${DOMAIN} https://your-app.vercel.app"
    fi
else
    warn "No domain argument given — /etc/caddy/Caddyfile left as-is."
    warn "Re-run as: sudo bash deploy/install.sh api.your-host.example https://your-app.vercel.app"
fi

log "Firewall"
# Oracle's Ubuntu image ships an iptables chain that drops everything except SSH,
# and it is NOT managed by ufw. Insert the rules above that catch-all REJECT.
for port in 80 443; do
    if ! iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
        iptables -I INPUT 6 -p tcp --dport "$port" -m conntrack --ctstate NEW -j ACCEPT
        echo "    opened TCP $port"
    fi
done
netfilter-persistent save >/dev/null

log "Starting services"
systemctl restart meridian-api
[[ -n "$DOMAIN" ]] && systemctl restart caddy || true
sleep 2

log "Status"
systemctl --no-pager --lines=5 status meridian-api || true
curl -fsS http://127.0.0.1:8080/health && echo

cat <<EOF

────────────────────────────────────────────────────────────────────────
Installed. Remaining steps:

  1. Fill in $ENV_FILE (Angel One credentials + dashboard secrets).
  2. sudo systemctl restart meridian-api
  3. Point ${DOMAIN:-your hostname} at this VM's public IP.
  4. Add TCP 80 and 443 ingress in the OCI console Security List.
  5. Load the NSE holiday calendar from the dashboard's Controls page.
  6. Leave MERIDIAN_TRADING_MODE=paper until a full session has run clean.

Logs:    journalctl -u meridian-api -f
Engine:  journalctl -t meridian-api -f   (engine events also land in the DB)
────────────────────────────────────────────────────────────────────────
EOF
