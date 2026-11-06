"""An uploaded program's code with every credential line blanked, and diffs of it.

An uploaded program carries the operator's Angel One credentials in its source
(API key, client code, PIN, TOTP seed). The code viewer must show the code
without them, so the server blanks those lines before anything is sent: the
browser never receives a credential, not even inside a diff's context lines.

The rules are deliberately broad. Hiding an innocent line costs a line of
context; showing a credential costs the account. A line is hidden when:

* any identifier or word on it is, or is built from, a credential word
  (``API_KEY``, ``clientCode``, ``ANGEL_PIN``, ``totp_secret``, ``X-PrivateKey``,
  ``password=``, a ``# my pin`` comment, ``SmartConnect(``, ``generateSession(``);
* or any string on it looks like a secret whatever it is called: a JWT, a TOTP
  base32 seed, a long token, a mixed-case alphanumeric key, a client code such
  as ``A123456``, or a 4–6 digit PIN.

When any physical line of a statement is hidden, every line of that statement
is: a credentials dict or a login call spread over several lines goes whole.
"""

from __future__ import annotations

import difflib
import io
import re
import tokenize

MARKER = "··· credential line hidden ···"

# Identifier parts (after splitting on "_" and camelCase) that mark a line.
CREDENTIAL_PARTS = frozenset({
    "key", "keys", "apikey", "secret", "secrets", "token", "tokens", "jwt", "jwttoken", "feedtoken",
    "refreshtoken", "password", "passwd", "pwd", "pass", "passphrase", "pin", "mpin", "totp", "otp",
    "seed", "client", "clientcode", "clientid", "user", "userid", "username", "login", "auth",
    "cred", "creds", "credential", "credentials", "private", "privatekey", "bearer", "authorization",
})
# Whole identifiers (lowercased) that mark a line: the calls credentials are passed to.
CREDENTIAL_NAMES = frozenset({"smartconnect", "generatesession", "pyotp", "getprofile", "renewaccesstoken"})

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_STRING = re.compile(r"""(?P<q>['"])(?P<body>(?:\\.|(?!(?P=q)).)*)(?P=q)""")

_SECRET_VALUES = (
    re.compile(r"^eyJ[A-Za-z0-9_-]{8,}"),                       # a JWT
    re.compile(r"^[A-Z2-7]{16,}={0,6}$"),                        # a base32 TOTP seed
    re.compile(r"^[A-Za-z0-9+/=_.\-]{24,}$"),                    # a long token
    re.compile(r"^[A-Z]{1,3}\d{4,9}$"),                          # an Angel client code
    re.compile(r"^\d{4}$|^\d{6}$"),                              # a PIN
    re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I),  # a UUID key
)


def _mixed_key(s: str) -> bool:
    """6–64 letters and digits with upper case, lower case and a digit: an API key's shape."""
    return (6 <= len(s) <= 64 and s.isalnum() and any(c.isupper() for c in s)
            and any(c.islower() for c in s) and any(c.isdigit() for c in s))


def _parts(word: str) -> list[str]:
    out = []
    for chunk in word.split("_"):
        out += [p.lower() for p in _CAMEL.findall(chunk)]
    return out


def secret_value(s: str) -> bool:
    s = s.strip()
    return bool(s) and (any(p.search(s) for p in _SECRET_VALUES) or _mixed_key(s))


def sensitive_line(line: str) -> bool:
    """Whether a physical line names a credential or carries something shaped like one."""
    for word in _WORD.findall(line.replace("-", "_")):
        if word.lower() in CREDENTIAL_NAMES or word.lower() in CREDENTIAL_PARTS:
            return True
        if any(p in CREDENTIAL_PARTS for p in _parts(word)):
            return True
    return any(secret_value(m.group("body")) for m in _STRING.finditer(line))


def _statements(source: str) -> list[tuple[int, int]] | None:
    """(first, last) physical line, 1-based, of each multi-line logical line; None if it does not tokenize."""
    spans, start = [], None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING):
                continue
            if start is None:
                start = tok.start[0]
            if tok.type in (tokenize.NEWLINE, tokenize.ENDMARKER):
                if tok.start[0] > start:
                    spans.append((start, tok.start[0]))
                start = None
            elif tok.type == tokenize.STRING and tok.end[0] > tok.start[0]:
                spans.append((tok.start[0], tok.end[0]))  # a multi-line string, wherever it sits
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    return spans


def redact(source: str) -> tuple[list[str], int]:
    """The source's lines with every credential line replaced by a marker, and how many were hidden."""
    lines = source.splitlines()
    hidden = {i for i, line in enumerate(lines, start=1) if sensitive_line(line)}
    spans = _statements(source)
    if spans is None:
        # Not valid Python: hide each flagged line and, conservatively, its neighbours.
        hidden |= {j for i in list(hidden) for j in (i - 1, i + 1) if 1 <= j <= len(lines)}
    else:
        grow = True
        while grow:
            grow = False
            for a, b in spans:
                if any(a <= i <= b for i in hidden) and not all(i in hidden for i in range(a, b + 1)):
                    hidden |= set(range(a, b + 1))
                    grow = True
    out = []
    for i, line in enumerate(lines, start=1):
        if i in hidden:
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f"{indent}# {MARKER}")
        else:
            out.append(line)
    return out, len(hidden)


def diff(source_a: str, source_b: str, context: int = 3) -> dict:
    """A unified diff of two versions, computed on the redacted code only."""
    a, hidden_a = redact(source_a)
    b, hidden_b = redact(source_b)
    hunks, added, removed = [], 0, 0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for group in matcher.get_grouped_opcodes(context):
        lines = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines += [{"kind": " ", "a": i + 1, "b": j + 1, "text": a[i]} for i, j in zip(range(i1, i2), range(j1, j2))]
                continue
            if tag in ("replace", "delete"):
                lines += [{"kind": "-", "a": i + 1, "b": None, "text": a[i]} for i in range(i1, i2)]
                removed += i2 - i1
            if tag in ("replace", "insert"):
                lines += [{"kind": "+", "a": None, "b": j + 1, "text": b[j]} for j in range(j1, j2)]
                added += j2 - j1
        first, last = group[0], group[-1]
        hunks.append({"a_start": first[1] + 1, "a_len": last[2] - first[1],
                      "b_start": first[3] + 1, "b_len": last[4] - first[3], "lines": lines})
    return {"hunks": hunks, "added": added, "removed": removed, "identical": not hunks,
            "lines": {"a": len(a), "b": len(b)}, "hidden": {"a": hidden_a, "b": hidden_b}}


def scrub_strings(value):
    """A JSON-like value with every string that looks like a secret replaced (for gate reports)."""
    if isinstance(value, dict):
        return {k: scrub_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_strings(v) for v in value]
    if isinstance(value, str):
        return "\n".join(f"# {MARKER}" if sensitive_line(line) else line for line in value.split("\n"))
    return value
