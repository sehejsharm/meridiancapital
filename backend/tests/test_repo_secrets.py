"""Nothing that looks like a live credential is ever committed.

The repository is public: anything committed is published, and stays in the
history even after it is deleted. This scans every tracked file for the shapes
real secrets take — private keys, bot tokens, cloud keys, a broker credential or
dashboard secret given a value — and fails the build if one appears. Test
fixtures that exist to exercise the redaction code are the only exceptions.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "Telegram bot token": re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    "Slack token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "AI provider API key": re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{32,}"),
    "broker credential with a value": re.compile(
        r"""\bANGEL_(?:API_KEY|CLIENT_ID|PASSWORD|TOTP_SECRET)[ \t]*[:=][ \t]*["']?[A-Za-z0-9]{4,}"""),
    "dashboard secret with a value": re.compile(
        r"""\bMERIDIAN_(?:JWT_SECRET|PASSWORD_HASH|RELAY_SECRET|SESSION_SECRET|BACKUP_PASSPHRASE|TELEGRAM_BOT_TOKEN)"""
        r"""[ \t]*[:=][ \t]*["']?(?!\$|<|\{)[A-Za-z0-9+/=_.$-]{16,}"""),
    "PBKDF2 password hash": re.compile(r"pbkdf2_sha256\$\d+\$[A-Za-z0-9+/=]{16,}\$[A-Za-z0-9+/=]{32,}"),
}

# Files whose job is to hold made-up credentials (they test the redaction and the
# credential-setting script), and this file, which holds the patterns.
FIXTURES = {
    "backend/tests/test_code_redact.py",
    "backend/tests/test_set_angel_credentials.py",
    "backend/tests/test_repo_secrets.py",
}
# Never committed at all, whatever they hold.
FORBIDDEN_NAMES = re.compile(r"(^|/)(\.env(\.[^/]*)?|[^/]*\.(pem|key|p12|pfx)|id_(rsa|ed25519|ecdsa)|meridian\.env)$")
ALLOWED_NAMES = {"frontend/.env.example", "backend/deploy/meridian.env.example"}


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True)
    return [p for p in out.stdout.decode().split("\0") if p]


def test_no_secret_file_is_tracked():
    bad = [p for p in tracked_files() if FORBIDDEN_NAMES.search(p) and p not in ALLOWED_NAMES]
    assert not bad, f"files that must never be committed: {bad}"


def test_no_tracked_file_holds_a_live_looking_credential():
    found = []
    for rel in tracked_files():
        if rel in FIXTURES:
            continue
        path = REPO / rel
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue          # binary: icons and the like
        for kind, rx in PATTERNS.items():
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                found.append(f"{rel}:{line} ({kind})")   # never the value itself
    assert not found, "credential-shaped text is committed:\n  " + "\n  ".join(found)


def test_the_scan_would_catch_one():
    sample = "ANGEL_TOTP_SECRET=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP\nMERIDIAN_JWT_SECRET=" + "q" * 40
    assert PATTERNS["broker credential with a value"].search(sample)
    assert PATTERNS["dashboard secret with a value"].search(sample)
    assert not PATTERNS["dashboard secret with a value"].search("MERIDIAN_JWT_SECRET=$S")
    assert not PATTERNS["dashboard secret with a value"].search("MERIDIAN_JWT_SECRET=")
