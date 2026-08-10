#!/usr/bin/env bash
# One-shot server setup for a fresh box.
#
# Supports the two images the free tiers actually hand you:
#   * Ubuntu 22.04/24.04            (login user: ubuntu)
#   * Oracle Linux 9 / RHEL family  (login user: opc)
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

# ---- Which package manager are we on? ----------------------------------
if command -v dnf >/dev/null 2>&1; then
  PKG=dnf
elif command -v apt-get >/dev/null 2>&1; then
  PKG=apt
else
  die "Neither dnf nor apt-get found — unsupported distribution."
fi

pkg_install() {
  case "$PKG" in
    dnf) dnf install -y "$@" >/dev/null ;;
    apt) DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" >/dev/null ;;
  esac
}

say "Package manager: ${PKG}"

# Oracle Linux's minimal image ships without curl's full build or git, and the
# token generator needs python3. Install them before anything reaches for them.
for tool in curl git python3; do
  command -v "$tool" >/dev/null 2>&1 || { say "Installing $tool"; pkg_install "$tool"; }
done

say "Setting the system clock to Asia/Kolkata"
timedatectl set-timezone Asia/Kolkata || warn "Could not set timezone; do it manually."

# The free 1 GB shapes are workable, but parsing Angel One's 30 MB scrip
# master spikes to a few hundred megabytes on the first run of each day.
# Swap absorbs that spike instead of the kernel killing the bot.
TOTAL_MB="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 4096)"
if (( TOTAL_MB < 1800 )) && [[ ! -f /swapfile ]]; then
  say "Only ${TOTAL_MB} MB RAM — adding 2 GB of swap"
  # fallocate reports success on XFS but hands back unwritten extents, and
  # swapon then refuses the file for "having holes". Oracle Linux formats root
  # as XFS, so that path has to be written out with dd instead. ext4 keeps the
  # fast path.
  ROOT_FS="$(findmnt -no FSTYPE -T / 2>/dev/null || echo unknown)"
  if [[ "$ROOT_FS" == "xfs" ]] || ! fallocate -l 2G /swapfile 2>/dev/null; then
    rm -f /swapfile
    dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  fi
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=10 >/dev/null
  grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf
fi

if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker"
  if [[ "$PKG" == "dnf" ]]; then
    # Oracle Linux 9 ships podman plus its own runc, and both collide with
    # containerd.io. get.docker.com walks into that collision and stops, so the
    # repo is added directly and --allowerasing is allowed to swap the
    # conflicting packages out.
    pkg_install dnf-plugins-core
    dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo >/dev/null
    dnf install -y --allowerasing \
      docker-ce docker-ce-cli containerd.io \
      docker-buildx-plugin docker-compose-plugin >/dev/null
  else
    curl -fsSL https://get.docker.com | sh
  fi
  systemctl enable --now docker
else
  say "Docker already present"
fi

docker compose version >/dev/null 2>&1 \
  || die "The Docker Compose plugin is missing. Install 'docker-compose-plugin' and re-run."

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
    echo "  Local health check:  ${BOLD}http://localhost:8000/api/health${OFF}"
    echo "  API token:           (the API_TOKEN line in .env)"
    # bootstrap.sh drives Tailscale itself and prints its own summary, so the
    # advice below would only be a confusing duplicate when called from there.
    if [[ -z "${MERIDIAN_BOOTSTRAP:-}" ]]; then
      echo
      warn "One step left: give it an HTTPS address."
      echo
      echo "  The dashboard is served over HTTPS, and a browser will not let an"
      echo "  HTTPS page call a plain-HTTP server. Tailscale Funnel fixes that for"
      echo "  free, and because it dials out there is nothing to open on the"
      echo "  firewall or in your cloud security list:"
      echo
      echo "     curl -fsSL https://tailscale.com/install.sh | sudo sh"
      echo "     sudo tailscale up"
      echo "     sudo tailscale funnel --bg 8000"
      echo "     sudo tailscale funnel status"
      echo
      echo "  The address it prints (https://….ts.net) is what you paste into the"
      echo "  dashboard's Server field."
    fi
    exit 0
  fi
  sleep 2
done

die "Service did not become healthy. Check: docker compose -f deploy/docker-compose.yml logs"
