#!/usr/bin/env bash
# The devices allowed to sign in to the dashboard: list, approve, revoke — on the VM.
#
#   sudo bash ~/meridiancapital/backend/deploy/devices.sh                    # list
#   sudo bash ~/meridiancapital/backend/deploy/devices.sh approve ABCD-EFGH  # the code on the waiting device
#   sudo bash ~/meridiancapital/backend/deploy/devices.sh revoke <id>
#   sudo bash ~/meridiancapital/backend/deploy/devices.sh signout-all
#
# Runs as the service account with the settings file loaded, so the database
# stays owned by it. Prints no secret.
set -euo pipefail

APP_DIR="/opt/meridian"
ENV_FILE="/etc/meridian/meridian.env"
APP_USER="meridian"

if [ "$(id -u)" -ne 0 ]; then
    echo "run it with sudo: sudo bash $0 $*" >&2
    exit 1
fi
[ -r "$ENV_FILE" ] || { echo "no settings file at $ENV_FILE" >&2; exit 1; }
cd "$APP_DIR/backend"

# The settings are read by the service account itself (the file is group-readable
# by it), never passed on a command line where ps would show them.
exec sudo -u "$APP_USER" -H bash -c 'set -a; . "$1"; set +a; shift; exec "$@"' devices \
    "$ENV_FILE" "$APP_DIR/backend/.venv/bin/python" scripts/devices.py "$@"
