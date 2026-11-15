#!/usr/bin/env bash
# Whether an update of the server should wait.
#
# Sourced by install.sh. An update restarts the API. That does not stop a running
# algorithm (it runs as its own process and is adopted again), but for the half
# minute the API is down the dashboard, its alerts and its controls are gone,
# and a failed update leaves them gone with a position open. So updates belong
# outside the session, and install.sh refuses inside it unless told --force.
#
#   update_blocked_reason <repo dir>   prints why and returns 0 when it should wait
#
# For tests: MERIDIAN_NOW="2026-11-10 2 1030" (date, ISO weekday, HHMM in IST)
# and MERIDIAN_SKIP_PROCESS_CHECK=1.

update_blocked_reason() {
    local repo="${1:-.}" now today dow hm data_dir
    now="${MERIDIAN_NOW:-$(TZ=Asia/Kolkata date '+%F %u %H%M')}"
    read -r today dow hm <<<"$now"
    data_dir="${MERIDIAN_DATA_DIR:-/var/lib/meridian}"

    # A running algorithm, at any hour: this also covers special sessions such
    # as Muhurat trading, which fall outside the normal calendar.
    if [[ -z "${MERIDIAN_SKIP_PROCESS_CHECK:-}" ]]; then
        if pgrep -f "${data_dir}/programs/" >/dev/null 2>&1 || pgrep -f "engine\.runner" >/dev/null 2>&1; then
            echo "an algorithm is running right now"
            return 0
        fi
    fi

    (( 10#$dow >= 6 )) && return 1
    (( 10#$hm < 900 || 10#$hm >= 1530 )) && return 1
    # NSE's published closures ship with the code as "YYYY-MM-DD": "name".
    if grep -q "\"${today}\":" "${repo}/backend/shared/nse_holidays.py" 2>/dev/null; then
        return 1
    fi
    echo "the market is open (09:00 to 15:30 IST on a trading day)"
    return 0
}
