#!/usr/bin/env bash
# One-shot server setup for a fresh Ubuntu 22.04/24.04 box.
#
#   curl -fsSL https://raw.githubusercontent.com/<you>/meridiancapital/main/deploy/setup.sh | bash
# or, after cloning:
#   bash deploy/setup.sh
#
# Installs Docker, clones the repo if needed, generates an API token, and
# writes a .env skeleton. It never invents broker credentials — you fill those
# in yourself before starting.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/meridiancapital}"
GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; BOLD=$'\033[1m'; OFF=$'\033[0m'

say()  { echo "${GREEN}==>${OFF} $*"; }
warn() { echo "${YELLOW}==>${OFF} $*"; }
die()  { echo "${RED}==>${OFF} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root (or with sudo)."

say "Setting the system clock to Asia/Kolkata"
timedatectl set-timezone Asia/Kolkata || warn "Could not set timezone; do it manually."

# The free 1 GB shapes are workable, but parsing Angel One's 30 MB scrip
# master spikes to a few hundred megabytes on the first run of each day.
# Swap absorbs that spike instead of the kernel killing the bot.
TOTAL_MB="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 4096)"
if (( TOTAL_MB < 1800 )) && [[ ! -f /swapfile ]]; then
  say "Only ${TOTAL_MB} MB RAM — adding 2 GB of swap"
  fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=10 >/dev/null
  grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf
fi

if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker"
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
else
  say "Docker already present"
fi

if [[ ! -d "$REPO_DIR/.git" ]]; then
  [[ -n "${REPO_URL:-}" ]] || die "Set REPO_URL=https://github.com/<you>/meridiancapital.git and re-run."
  say "Cloning into $REPO_DIR"
  git clone "$REPO_URL" "$REPO_DIR"
fi

cd "$REPO_DIR"

if [[ ! -f .env ]]; then
  say "Creating .env"
  cp .env.example .env
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  sed -i "s|^API_TOKEN=.*|API_TOKEN=${TOKEN}|" .env
  chmod 600 .env
  echo
  echo "${BOLD}Your API token (put this into the phone app):${OFF}"
  echo "  ${GREEN}${TOKEN}${OFF}"
  echo
  warn "Now edit .env and fill in the five ANGEL_* values, then re-run this script."
  exit 0
fi

missing=()
for key in ANGEL_API_KEY ANGEL_CLIENT_ID ANGEL_PASSWORD ANGEL_TOTP_SECRET API_TOKEN; do
  value="$(grep -E "^${key}=" .env | cut -d= -f2- || true)"
  [[ -n "$value" ]] || missing+=("$key")
done
if (( ${#missing[@]} )); then
  die ".env is missing values for: ${missing[*]}"
fi

say "Building and starting"
docker compose -f deploy/docker-compose.yml up -d --build

say "Waiting for health"
for _ in $(seq 1 30); do
  if curl -fsS http://localhost:8000/api/health >/dev/null 2>&1; then
    echo
    say "Running."
    IP="$(curl -fsS https://api.ipify.org 2>/dev/null || echo '<server-ip>')"
    echo "  Server URL for the app:  ${BOLD}http://${IP}:8000${OFF}"
    echo "  API token:               (the API_TOKEN line in .env)"
    echo
    warn "Plain HTTP exposes your token. Point a domain here and run:"
    echo "     echo 'DOMAIN=trade.example.com' >> .env"
    echo "     docker compose -f deploy/docker-compose.yml --profile tls up -d"
    exit 0
  fi
  sleep 2
done

die "Service did not become healthy. Check: docker compose -f deploy/docker-compose.yml logs"
