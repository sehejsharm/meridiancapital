# Security

## Rotate the Angel One credentials

The `ganesh_kavach_9.py` this system was ported from contained live credentials as
plain-text defaults:

```python
os.environ.setdefault("ANGEL_API_KEY",     "...")
os.environ.setdefault("ANGEL_CLIENT_ID",   "...")
os.environ.setdefault("ANGEL_PASSWORD",    "...")   # the account PIN
os.environ.setdefault("ANGEL_TOTP_SECRET", "...")   # the 2FA seed
```

Together those four are complete account takeover. The TOTP secret is the seed your
authenticator app derives codes from — anyone holding it generates valid 2FA codes
forever, so two-factor authentication provides no protection once it leaks.

**Rotate all four in the Angel One portal.** Regenerate the API key, change the PIN, and
re-enrol TOTP so a new seed is issued — changing the PIN alone leaves the seed valid.

Assume the old values are compromised. A file like that gets copied into chat windows,
pasted into issues, synced to cloud drives, and committed to repositories; you cannot
audit every place it reached.

## How this system handles them

Credentials are read from the environment only, at `backend/engine/broker.py`. There is
no default, no fallback, and no config file in the repository that holds them. If any are
missing the engine refuses to start rather than logging in with partial state.

On the VM they live in `/etc/meridian/meridian.env`, owned `root:meridian` with mode
`0640` — readable by the service account, not by other users on the box. `.gitignore`
excludes `.env` and `.env.*`.

A test enforces this: `test_no_broker_credentials_are_baked_into_source` scans every
non-test Python file in `backend/` and fails if an `ANGEL_*` variable is ever given a
source default again.

## Dashboard authentication

Single operator, so there is no user table — one password hash and one signing key.

- **Password** — PBKDF2-HMAC-SHA256, 320,000 rounds, per-hash random salt. Generate with
  `scripts/bootstrap_secrets.py`, which prompts without echoing so the password never
  enters shell history.
- **Sessions** — HS256 JWT, 12-hour expiry, stored in an `httpOnly` `SameSite=Lax`
  cookie set by a Next route handler. The browser never holds a bearer token and never
  learns the API origin; every call is relayed server-side.
- **WebSocket** — a browser cannot send an `Authorization` header on a WS handshake, and
  putting a session token in a URL leaves it in proxy logs. Instead the dashboard mints a
  ~60-second, single-purpose ticket over authenticated HTTP. A captured socket URL is
  worthless within the minute and can never be replayed against the REST API.
- **Login throttling** — 8 failures from one IP within 5 minutes locks that IP out for
  15 minutes.
- **Audit log** — every control action is recorded with operator, action, detail and IP
  before it takes effect, readable at `/api/audit` and on the Journal page.

## Guarded actions

Two actions can lose money if triggered by accident, so both require an exact typed
phrase the UI does not prefill:

| Action | Phrase | Extra guard |
|---|---|---|
| Switch to live trading | `GO LIVE` | Refused while the engine is running |
| Flatten an open position | `FLATTEN` | Refused when the book is empty |

Stopping the engine is refused outright while a position is open, since an unmanaged
position has no stop-loss watcher. Flatten first, or square off in the Angel One app.

## Network exposure

The API binds to `127.0.0.1` only. Caddy terminates TLS and is the sole public listener.
CORS is restricted to your Vercel origin, and `/health` is the only unauthenticated route
— it deliberately returns no trading data.

Two separate gates control inbound traffic on Oracle Cloud and traffic needs both: the OCI
Security List in the console, and the VM's own iptables rules. `deploy/install.sh` handles
the second; the first is a manual step in the console.

## If something looks wrong

1. **Flatten** from the dashboard, or square off in the Angel One app.
2. **Stop the engine** and **disarm automation** so the scheduler does not restart it.
3. Switch the mode back to **paper** before investigating.
4. Read the Journal page — engine runs, control commands, and the audit log are all there.

If you suspect the dashboard password leaked, rotate `MERIDIAN_JWT_SECRET` as well as the
password: changing the secret invalidates every issued session token immediately.
