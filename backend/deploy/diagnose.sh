#!/usr/bin/env bash
# Where the time goes on this VM, in one report.
#
#   sudo bash ~/meridiancapital/backend/deploy/diagnose.sh
#
# The machine (CPU, load, memory, swap), the processes using it, the database,
# the API's own response time for every call the dashboard makes (measured here
# on the VM, so Vercel and the internet are not in the figure), and the slow
# requests the API has logged. Read-only: it changes nothing and prints no
# secret.
set -u

APP_DIR="/opt/meridian"
DATA_DIR="/var/lib/meridian"
ENV_FILE="/etc/meridian/meridian.env"
API="http://127.0.0.1:8080"

if [ "$(id -u)" -ne 0 ]; then
    echo "run it with sudo: sudo bash $0" >&2
    exit 1
fi

echo "== machine"
echo "cpus: $(nproc)   $(uptime | sed 's/.*load average/load average/')"
free -m | sed 's/^/  /'
echo "  swap in/out since boot (pages): $(grep -E '^(pswpin|pswpout)' /proc/vmstat | tr '\n' ' ')"

echo
echo "== busiest processes (cpu %, memory %, resident MB)"
ps -eo pid,pcpu,pmem,rss,etime,args --sort=-pcpu | head -9 \
    | awk 'NR==1 {print "  " $0; next} {printf "  %-7s %5s %5s %7.0f %12s %s\n", $1, $2, $3, $4/1024, $5, substr($0, index($0,$6), 90)}'

echo
echo "== database"
ls -la "${DATA_DIR}"/*.db* 2>/dev/null | awk '{printf "  %10.1f MB  %s\n", $5/1048576, $9}'

echo
echo "== API response times, measured on this VM (best / worst of 3, ms)"
set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a
cd "${APP_DIR}/backend" || exit 1
"${APP_DIR}/backend/.venv/bin/python" - "${API}" <<'PY'
import json, sqlite3, sys, time, urllib.request
from app.config import settings
from app.security import issue_token

api = sys.argv[1]
token = issue_token("diagnose", 300)
paths = ["/health", "/api/status", "/api/algos", "/api/snapshot", "/api/trades?limit=500", "/api/equity",
         "/api/equity?limit=600&daily=0", "/api/events?limit=200", "/api/health/detail", "/api/ticker",
         "/api/market/chain", "/api/market/nifty", "/api/news", "/api/control/holidays"]
for path in paths:
    times, size, status = [], 0, None
    for _ in range(3):
        req = urllib.request.Request(api + path, headers={"Authorization": f"Bearer {token}"})
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                size, status = len(r.read()), r.status
        except urllib.error.HTTPError as e:
            status = e.code
        except Exception as e:
            status = type(e).__name__
        times.append((time.perf_counter() - start) * 1000)
    flag = "   <-- slow" if max(times) > 800 else ""
    print(f"  {path:34s} {min(times):8.0f} {max(times):8.0f}   {size/1024:7.1f} KB  {status}{flag}")

print()
print("== rows")
db = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
for table in ("events", "equity_samples", "trades", "kv", "commands", "audit_log"):
    try:
        print(f"  {table:16s} {db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:>10,}")
    except sqlite3.Error:
        pass
idx = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='equity_samples'")]
print(f"  equity_samples indexes: {', '.join(sorted(idx))}")
print(f"  sqlite {sqlite3.sqlite_version}")
PY

echo
echo "== the public route Vercel uses (name lookup, connection, TLS, first byte; seconds)"
HOST="$(grep -m1 -oE '^[A-Za-z0-9.-]+\.[A-Za-z]{2,}' /etc/caddy/Caddyfile 2>/dev/null)"
HOST="${HOST:-meridiancap.duckdns.org}"
for i in 1 2 3; do
    curl -s -o /dev/null --max-time 30 \
        -w "  dns %{time_namelookup}  connect %{time_connect}  tls %{time_appconnect}  first byte %{time_starttransfer}  total %{time_total}\n" \
        "https://${HOST}/health" || echo "  https://${HOST}/health failed"
done

echo
echo "== slow requests the API logged in the last 2 hours"
journalctl -u meridian-api --since "2 hours ago" --no-pager 2>/dev/null | grep "slow request" | tail -20 | sed 's/^/  /' \
    || true
echo "  (none above means none over 0.8 s since the API was last updated)"
