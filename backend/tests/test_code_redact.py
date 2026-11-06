"""The code viewer never sends a credential to the browser (app/code_redact.py).

Every value below is made up, shaped like the real thing: an Angel API key, a
client code, a PIN, a TOTP seed, a JWT. Each test asserts that none of them
appears anywhere in what the server returns.
"""

from __future__ import annotations

import json

import pytest

from app import code_redact as R
from tests.test_desk import auth, client  # noqa: F401  (fixtures)

API_KEY, CLIENT, PIN, TOTP = "Zq8LmT3x", "Q9876543", "4821", "JBSWY3DPEHPK3PXPJBSWY3DPEH"
JWT = "eyJhbGciOiJIUzUxMiJ9.eyJ1c2VybmFtZSI6IlE5ODc2NTQzIn0.c2lnbmF0dXJl"
PAPER_KEY = "Hy7RwPq2"
SECRETS = (API_KEY, CLIENT, PIN, TOTP, JWT, PAPER_KEY, "hunter2-pass", "Kp4Ds9Lm")

V1 = f'''"""A program as operators write them."""
import os
import pyotp
from SmartApi import SmartConnect

# Used whenever the server's ANGEL_* value is missing.
if not os.environ.get("ANGEL_API_KEY", "").strip(): os.environ["ANGEL_API_KEY"] =     "{API_KEY}"
if not os.environ.get("ANGEL_CLIENT_ID", "").strip(): os.environ["ANGEL_CLIENT_ID"] =   "{CLIENT}"
if not os.environ.get("ANGEL_PIN", "").strip(): os.environ["ANGEL_PIN"] =    "{PIN}"
if not os.environ.get("ANGEL_TOTP_SECRET", "").strip(): os.environ["ANGEL_TOTP_SECRET"] = "{TOTP}"
PAPER_API_KEY = "{PAPER_KEY}"
mpin = os.getenv("MPIN", "{PIN}")
CREDS = {{
    "api": "{API_KEY}",
    "who": "{CLIENT}",
}}
login_args = (
    "{CLIENT}",
    "{PIN}",
)
headers = {{"Authorization": "Bearer {JWT}"}}
SESSION = "{JWT}"
x = "Kp4Ds9Lm"  # shaped like a key, whatever it is called
pw = "hunter2-pass"  # my password

STOP_FRAC = 0.45
BE_TRIGGER = 0.20


def run():
    api = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
    api.generateSession(
        os.environ["ANGEL_CLIENT_ID"], os.environ["ANGEL_PIN"],
        pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now(),
    )
    return api
'''

V2 = V1.replace("BE_TRIGGER = 0.20", "BE_TRIGGER = 0.25\nTRAIL_FRAC = 0.20").replace(API_KEY, "Wn5TcR8v")


def test_no_credential_survives_redaction():
    lines, hidden = R.redact(V1)
    text = "\n".join(lines)
    for secret in SECRETS:
        assert secret not in text, f"a {len(secret)}-character credential leaked"
    assert hidden >= 20
    # The strategy itself is still readable.
    assert "STOP_FRAC = 0.45" in text and "BE_TRIGGER = 0.20" in text and "def run():" in text


def test_a_multi_line_statement_goes_whole():
    lines, _ = R.redact(V1)
    start = V1.splitlines().index("CREDS = {")
    assert all(R.MARKER in lines[i] for i in range(start, start + 4)), "the dict and its closing brace"
    start = V1.splitlines().index("login_args = (")
    assert all(R.MARKER in lines[i] for i in range(start, start + 4)), "positional credentials in a tuple"


def test_indentation_is_kept_and_line_numbers_do_not_move():
    lines, _ = R.redact(V1)
    assert len(lines) == len(V1.splitlines())
    gen = V1.splitlines().index("    api.generateSession(")
    assert lines[gen].startswith("    # ")


@pytest.mark.parametrize("line", [
    'API_KEY = "abc"', "clientCode='x'", "obj.totp_secret = s", "password: str = 'x'", '"X-PrivateKey": k',
    "# my pin is in the file", "user_id = 1", "api = SmartConnect(k)", 'token = "t"', "otp = pyotp.TOTP(s)",
    'value = "JBSWY3DPEHPK3PXP"', 'value = "A1234567"', 'value = "123456"', 'value = "aB3dE5fG"',
])
def test_lines_that_are_hidden(line):
    assert R.sensitive_line(line)


@pytest.mark.parametrize("line", [
    "STOP_FRAC = 0.45", "def on_bar(ctx):", 'tsym = f"NIFTY{expiry}{strike}CE"', "for i in range(10):",
    'log("ENTER BUY CE 24500")', "return round(x, 2)", 'MODE = "paper"', 'EXPIRY_WEEKDAY = "TUE"',
])
def test_lines_that_stay(line):
    assert not R.sensitive_line(line)


def test_code_that_does_not_parse_is_still_redacted_with_its_neighbours():
    src = f'def broken(:\n    k = (\n    "{API_KEY}"\nSTOP = 1\n'
    lines, _ = R.redact(src)
    assert API_KEY not in "\n".join(lines)


def test_the_diff_shows_the_change_and_hides_the_credentials():
    d = R.diff(V1, V2)
    body = json.dumps(d)
    for secret in (*SECRETS, "Wn5TcR8v"):
        assert secret not in body
    changed = [ln["text"] for h in d["hunks"] for ln in h["lines"] if ln["kind"] != " "]
    assert changed == ["BE_TRIGGER = 0.20", "BE_TRIGGER = 0.25", "TRAIL_FRAC = 0.20"]
    assert d["added"] == 2 and d["removed"] == 1 and not d["identical"]
    assert R.diff(V1, V1)["identical"]


def test_gate_report_strings_are_scrubbed():
    report = {"passed": False, "error": f'SyntaxError: line 9\n    API_KEY = "{API_KEY}"\n', "checks": [{"key": "size_capital", "detail": "ok"}]}
    out = R.scrub_strings(report)
    assert API_KEY not in json.dumps(out)
    assert out["checks"][0] == {"key": "size_capital", "detail": "ok"}


# ── the API ──────────────────────────────────────────────────────────────────
def _upload(client, auth, source):  # noqa: F811
    r = client.post("/api/algos", json={"name": "redact probe", "source": source, "mode": "paper"}, headers=auth)
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_the_api_never_returns_source_or_a_credential(client, auth):  # noqa: F811
    first = _upload(client, auth, V1)
    second = _upload(client, auth, V2)
    aid, v1, v2 = second["algo_id"], first["version_id"], second["version_id"]
    assert first["algo_id"] == aid

    pages = [
        client.post("/api/algos", json={"name": "redact probe", "source": V2, "mode": "paper"}, headers=auth),
        client.get(f"/api/algos/{aid}", headers=auth),
        client.get(f"/api/algos/{aid}/versions/{v1}", headers=auth),
        client.get(f"/api/algos/{aid}/diff", params={"a": v1, "b": v2}, headers=auth),
        client.get(f"/api/algos/{aid}/diff", params={"a": v1, "b": v2, "context": 100000}, headers=auth),
        client.get("/api/algos", headers=auth),
    ]
    for r in pages:
        assert r.status_code == 200, r.text
        for secret in (*SECRETS, "Wn5TcR8v"):
            assert secret not in r.text, f"{r.request.url.path} leaked a {len(secret)}-character credential"
    assert "source" not in pages[1].json()
    whole = pages[3].json()
    assert whole["lines"]["b"] == len(V2.splitlines()) and whole["hidden"]["b"] >= 20


def test_the_diff_refuses_another_algorithms_version(client, auth):  # noqa: F811
    a = _upload(client, auth, V1)
    r = client.get(f"/api/algos/{a['algo_id']}/diff", params={"a": a["version_id"], "b": 999999}, headers=auth)
    assert r.status_code == 404
    assert client.get(f"/api/algos/{a['algo_id']}/diff", params={"a": 1, "b": 1}).status_code == 401
