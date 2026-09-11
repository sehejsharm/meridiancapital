"""IST clock helpers and NTP drift verification."""

from __future__ import annotations

import socket
import struct
import time
from datetime import datetime, timedelta, timezone

from engine.config import MARKET_CLOSE, MARKET_OPEN, NTP_SERVERS

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    """Naive IST datetime, matching the original build's convention."""
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)


def now_ist_aware() -> datetime:
    return datetime.now(IST)


def ge(t: datetime, hm: tuple[int, int]) -> bool:
    return (t.hour, t.minute) >= hm


def lt(t: datetime, hm: tuple[int, int]) -> bool:
    return (t.hour, t.minute) < hm


def is_market_hours(t: datetime | None = None) -> bool:
    t = t or now_ist()
    return ge(t, MARKET_OPEN) and lt(t, MARKET_CLOSE)


def ntp_offset(servers=NTP_SERVERS, timeout: float = 3.0) -> float | None:
    """Seconds our clock is AHEAD of true time (positive) or behind (negative).

    Returns None if no NTP server is reachable.
    """
    for host in servers:
        try:
            pkt = b"\x1b" + 47 * b"\0"
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(timeout)
            t0 = time.time()
            s.sendto(pkt, (host, 123))
            data, _ = s.recvfrom(1024)
            t3 = time.time()
            s.close()
            if len(data) < 48:
                continue
            fields = struct.unpack("!12I", data[:48])
            server_t = (fields[10] - 2208988800) + fields[11] / 2**32
            return ((t0 + t3) / 2.0) - server_t
        except Exception:
            continue
    return None
