"""What Angel is told about the caller, and what a program's output may show.

SmartConnect sends X-ClientPublicIP from its clientPublicIp attribute and, left
alone, fills it with 106.193.147.98 — a placeholder hard-coded in the library,
the same on every machine. The engine used to set _public_ip, which the
library never reads, so every request claimed to come from that address.

When a call fails, the library logs its request headers, bearer token and API
key included; a program's output is followed into the journal, so those must
be masked on the way in.
"""

from __future__ import annotations

from app.program_output import redact
from engine.broker import pin_client_ips

PLACEHOLDER = "106.193.147.98"

# The shape of the line Angel's library logged on the live desk.
FAILED_CALL = (
    "[E 260929 04:07:02 smartConnect:221] Error occurred while making a POST request to "
    "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1/getLtpData. "
    "Headers: {'Content-type': 'application/json', 'X-ClientLocalIP': '127.0.0.1', "
    "'X-ClientPublicIP': '106.193.147.98', 'X-MACAddress': '02:00:17:04:19:10', "
    "'Accept': 'application/json', 'X-PrivateKey': 'AbCd1234', 'X-UserType': 'USER', "
    "'X-SourceID': 'WEB', 'Authorization': 'Bearer eyJhbGciOiJIUzUxMiJ9.eyJ1c2VybmFtZSI6.sig-_x'}"
)


def test_requests_carry_the_whitelisted_ip_not_the_library_placeholder():
    from SmartApi import SmartConnect

    api = SmartConnect(api_key="test")
    assert PLACEHOLDER in str(api.requestHeaders()), "the library default this guards against"

    pin_client_ips(api, "137.23.55.120", "10.0.0.5")
    headers = api.requestHeaders()
    assert headers["X-ClientPublicIP"] == "137.23.55.120"
    assert headers["X-ClientLocalIP"] == "10.0.0.5"
    assert PLACEHOLDER not in str(headers)
    assert api._public_ip == "137.23.55.120", "our own rejection messages quote it"


def test_a_failed_call_log_line_loses_its_token_and_key():
    out = redact(FAILED_CALL)
    assert "eyJ" not in out and "sig-_x" not in out
    assert "AbCd1234" not in out
    assert "'Authorization': 'Bearer [redacted]'" in out
    assert "'X-PrivateKey': '[redacted]'" in out
    # Everything else an operator needs to read the failure survives.
    assert "getLtpData" in out and "'X-ClientPublicIP': '106.193.147.98'" in out
    assert "'X-UserType': 'USER'" in out


def test_json_style_session_fields_are_masked():
    out = redact('login ok {"jwtToken": "eyJabc.def", "refreshToken": "r3fr3sh", "feedToken": "f33d", "state": "ok"}')
    assert "eyJabc" not in out and "r3fr3sh" not in out and "f33d" not in out
    assert '"state": "ok"' in out


def test_ordinary_lines_pass_through_untouched():
    for line in (
        "NIFTY spot 22,618.3",
        "Angel API 4 calls | 4/40 per min | 0 self-throttled | 1 REJECTED by Angel",
        "ORDER FILLED BUY 65 NIFTY29SEP2622600CE @ 142.35",
    ):
        assert redact(line) == line
