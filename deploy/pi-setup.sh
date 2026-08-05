#!/usr/bin/env bash
# Raspberry Pi setup for Meridian Capital.
#
#   curl -fsSL https://raw.githubusercontent.com/<you>/meridiancapital/main/deploy/pi-setup.sh \
#     | sudo REPO_URL=https://github.com/<you>/meridiancapital.git bash
#
# or, after cloning:
#   sudo bash deploy/pi-setup.sh
#
# Handles the things a Pi needs that a cloud VM does not: it has no real-time
# clock, it boots off an SD card that wears out, and it loses power without
# warning. Everything here is idempotent — safe to re-run.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/meridiancapital}"
GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; BOLD=$'\033[1m'; OFF=$'\033[0m'

say()  { echo "${GREEN}==>${OFF} $*"; }
warn() { echo "${YELLOW}==>${OFF} $*"; }
die()  { echo "${RED}==>${OFF} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run with sudo."

# ---------------------------------------------------------------- sanity

ARCH="$(uname -m)"
if [[ "$ARCH" != "aarch64" && "$ARCH" != "arm64" && "$ARCH" != "x86_64" ]]; then
  die "This is a 32-bit OS (${ARCH}). Reflash with the 64-bit Raspberry Pi OS —
     numpy, pandas and scipy have no prebuilt 32-bit ARM wheels, so the install
     would try to compile them and fail. Use Raspberry Pi Imager and pick
     'Raspberry Pi OS (64-bit)' or 'Raspberry Pi OS Lite (64-bit)'."
fi

TOTAL_MB="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)"
say "Architecture ${ARCH}, ${TOTAL_MB} MB RAM"
if (( TOTAL_MB < 900 )); then
  die "Under 1 GB of RAM. A Pi Zero or a 512 MB Pi cannot run this — use a
     Pi 4 (2 GB or more) or a Pi 5."
fi

# ---------------------------------------------------------------- clock
#
# A Pi has no battery-backed clock. After a power cut it boots believing it is
# whenever it last shut down, and a bot with the wrong idea of the time is
# worse than one that is switched off.

say "Setting the timezone to Asia/Kolkata and forcing NTP"
timedatectl set-timezone Asia/Kolkata
timedatectl set-ntp true
systemctl enable --now systemd-timesyncd 2>/dev/null || true

say "Waiting for the clock to synchronise"
for _ in $(seq 1 30); do
  if timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes; then
    say "Clock synchronised: $(date)"
    break
  fi
  sleep 2
done
if ! timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes; then
  warn "Clock has not synchronised yet. Check the network before trading —
       the bot decides when to trade from this clock."
fi

# ---------------------------------------------------------------- swap
#
# 2 GB Pi plus the first scrip-master parse of the day is tight. Pi OS ships
# with a 100 MB swap by default, which is not enough.

CURRENT_SWAP="$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo)"
if (( TOTAL_MB < 3500 )) && (( CURRENT_SWAP < 1500 )); then
  say "Raising swap to 2 GB (was ${CURRENT_SWAP} MB)"
  if [[ -f /etc/dphys-swapfile ]]; then
    # Pi OS manages swap through dphys-swapfile, not a plain /swapfile.
    dphys-swapfile swapoff 2>/dev/null || true
    sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile
    grep -q '^CONF_MAXSWAP' /etc/dphys-swapfile \
      && sed -i 's/^CONF_MAXSWAP=.*/CONF_MAXSWAP=4096/' /etc/dphys-swapfile \
      || echo 'CONF_MAXSWAP=4096' >> /etc/dphys-swapfile
    dphys-swapfile setup
    dphys-swapfile swapon
  elif [[ ! -f /swapfile ]]; then
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
fi
# Prefer RAM: swapping an SD card hard is how you kill it.
sysctl -w vm.swappiness=10 >/dev/null
grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf

# ---------------------------------------------------------------- SD wear
#
# Every write is a write the card does not get back. These two changes remove
# the bulk of the routine churn without touching anything the bot needs.

if ! grep -q '/var/log.*tmpfs' /etc/fstab; then
  say "Moving /var/log to RAM to spare the card"
  echo 'tmpfs /var/log tmpfs defaults,noatime,nosuid,mode=0755,size=64M 0 0' >> /etc/fstab
fi
if ! grep -q 'noatime' /etc/fstab; then
  warn "Consider adding 'noatime' to the root filesystem line in /etc/fstab"
fi

ROOT_SRC="$(findmnt -no SOURCE / || echo '')"
if [[ "$ROOT_SRC" == /dev/mmcblk* ]]; then
  warn "Running from an SD card. It will work, but a USB SSD is far more
       durable for something that writes every trading day. Either way, take
       the weekly backup described in the guide."
fi

# ---------------------------------------------------------------- watchdog
#
# If the kernel wedges, nobody is in the room to power-cycle it.

if [[ -e /dev/watchdog ]] || grep -q 'bcm2835_wdt' /proc/modules 2>/dev/null; then
  say "Enabling the hardware watchdog"
  apt-get install -y --no-install-recommends watchdog >/dev/null 2>&1 || true
  if [[ -f /etc/watchdog.conf ]]; then
    grep -q '^watchdog-device' /etc/watchdog.conf \
      || echo 'watchdog-device = /dev/watchdog' >> /etc/watchdog.conf
    grep -q '^max-load-1' /etc/watchdog.conf \
      || echo 'max-load-1 = 24' >> /etc/watchdog.conf
    systemctl enable --now watchdog 2>/dev/null || true
  fi
fi

# ---------------------------------------------------------------- docker

if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker (a few minutes on a Pi)"
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
  # So the login user can run docker without sudo after the next login.
  for u in pi ubuntu "${SUDO_USER:-}"; do
    [[ -n "$u" ]] && id "$u" &>/dev/null && usermod -aG docker "$u" || true
  done
else
  say "Docker already installed"
fi
systemctl enable docker >/dev/null 2>&1 || true

# ---------------------------------------------------------------- repo

if [[ ! -d "$REPO_DIR/.git" ]]; then
  [[ -n "${REPO_URL:-}" ]] || die "Set REPO_URL=https://github.com/<you>/meridiancapital.git and re-run."
  say "Cloning into $REPO_DIR"
  apt-get install -y --no-install-recommends git >/dev/null 2>&1 || true
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
  echo "${BOLD}Now edit ${REPO_DIR}/.env and fill in:${OFF}"
  echo "    ADMIN_USER      your login name"
  echo "    ADMIN_PASSWORD  your passcode"
  echo "    ANGEL_*         the five broker values"
  echo
  echo "  ${BOLD}sudo nano ${REPO_DIR}/.env${OFF}"
  echo
  echo "Then run this script again to build and start."
  exit 0
fi

missing=()
for key in ADMIN_PASSWORD ANGEL_API_KEY ANGEL_CLIENT_ID ANGEL_PASSWORD ANGEL_TOTP_SECRET; do
  value="$(grep -E "^${key}=" .env | cut -d= -f2- || true)"
  [[ -n "$value" ]] || missing+=("$key")
done
if (( ${#missing[@]} )); then
  die ".env is still missing: ${missing[*]}
     Edit it with: sudo nano ${REPO_DIR}/.env"
fi

# ---------------------------------------------------------------- build

say "Building — first run takes 5-10 minutes on a Pi"
docker compose -f deploy/docker-compose.yml up -d --build

say "Waiting for health"
for _ in $(seq 1 60); do
  if curl -fsS http://localhost:8000/api/health >/dev/null 2>&1; then
    echo
    say "Running."
    echo
    echo "  ${BOLD}On this network:${OFF}  http://$(hostname -I | awk '{print $1}'):8000"
    echo
    warn "That address only works at home. For access from anywhere — and the
     HTTPS the dashboard needs — install Tailscale:"
    echo
    echo "     curl -fsSL https://tailscale.com/install.sh | sudo sh"
    echo "     sudo tailscale up"
    echo "     sudo tailscale funnel --bg 8000"
    echo "     sudo tailscale funnel status      # your https://....ts.net address"
    echo
    exit 0
  fi
  sleep 3
done

die "Did not become healthy. Check: docker compose -f deploy/docker-compose.yml logs"
