"""A throwaway API with a little made-up desk data, for the all-pages check in CI.

    python scripts/site_check_server.py <empty data dir>      # serves on 127.0.0.1:8090

Not for production: the password is fixed and public ("site-check"), nothing
trades, and the data directory is whatever empty folder it is given. It talks to
no broker. The figures are invented and exist only so every page has something
to draw.
"""
import os
import sys

data = sys.argv[1]
os.makedirs(data, exist_ok=True)
os.environ.update(MERIDIAN_DATA_DIR=data, MERIDIAN_DB_PATH=os.path.join(data, "site-check.db"),
                  MERIDIAN_JWT_SECRET="site-check-" + "x" * 40, MERIDIAN_AUTOSTART="0", MERIDIAN_TRADING_MODE="paper",
                  MERIDIAN_CORS_ORIGINS="http://127.0.0.1:3100")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.security import hash_password  # noqa: E402

settings.password_hash = hash_password("site-check", rounds=1000)

from app import research  # noqa: E402
from engine.clock import now_ist  # noqa: E402
from shared.db import Database  # noqa: E402

db = Database(os.environ["MERIDIAN_DB_PATH"])
db.upsert_algo("check", "site check program")
vid = db.add_version("check", "import sys\nMIN_DTE, MAX_DTE = 2, 6\nif __name__ == '__main__':\n    live = '--live' in sys.argv or '--paper' in sys.argv\n",
                     "0" * 64, "site-check")
db.set_algo_fields("check", active_version=vid)
day = now_ist().date().isoformat()
TSYM = "NIFTY-CHECK-25500CE"
rec, t = research.TickRecorder(db), 1000.0
for hms, prem, peak, stop in (("10:30:05", 150.5, 0.3, 82.5), ("10:31:02", 158.0, 5.3, 82.5), ("10:32:30", 181.0, 20.7, 151.0),
                              ("10:33:10", 190.0, 26.7, 160.0), ("10:34:15", 159.0, 26.7, 160.0)):
    t += 40
    rec.record("check", {"ts": f"{day}T{hms}", "signal": {"spot": 25510.0}, "position": {
        "tsym": TSYM, "side": "CE", "strike": 25500, "expiry": day, "opened_ts": f"{day}T10:30:00",
        "entry_premium": 150.0, "live_premium": prem, "peak_pct": peak, "stop_price": stop, "qty": 65}}, "paper", t)
trade_id = db.add_trade({"entry_ts": f"{day}T10:30:00", "exit_ts": f"{day}T10:34:20", "session_date": day, "mode": "paper",
                         "side": "CE", "strike": 25500, "expiry": day, "tsym": TSYM, "lots": 1, "qty": 65,
                         "entry_prem": 150.0, "exit_prem": 158.0, "gross": 520.0, "charges": 70.0, "net": 450.0,
                         "reason": "TRAIL", "peak_pct": 26.7, "hold_min": 4, "algo_id": "check"})
for k in range(6):
    db.add_equity_sample(day, 50000.0 + 75 * k, 0.0, 50000.0 + 75 * k, 75.0 * k, algo_id="check", spot=25500.0 + k)
db.add_event("ok", "[10:30:01] ENTER BUY CE 25500 (ITM) x1 lot @ 150.00", source="program", algo_id="check")
with open(os.path.join(data, "trade_id"), "w", encoding="utf-8") as fh:
    fh.write(str(trade_id))

import uvicorn  # noqa: E402

uvicorn.run("app.main:app", host="127.0.0.1", port=8090, log_level="warning")
