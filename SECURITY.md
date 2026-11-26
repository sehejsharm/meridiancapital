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

Getting in takes two things: the operator's PIN, and a device an operator approved.

- **PIN** — PBKDF2-HMAC-SHA256, 320,000 rounds, per-hash random salt. Generate with
  `scripts/bootstrap_secrets.py`, which prompts without echoing so the PIN never
  enters shell history.
- **Approved devices** (`app/devices.py`) — each browser holds a random 256-bit secret
  in an httpOnly, SameSite=Strict cookie the page's script cannot read; the server
  stores only its SHA-256. The right PIN on a device nobody approved does not sign in:
  the device is shown an 8-character code and the operator is alerted. An operator
  approves it by typing that code on a signed-in device or on the VM
  (`deploy/devices.sh`). Requiring the code means approval cannot be given by
  accident or to a request the operator never saw. A passkey sign-in approves the
  device it is used on (passkeys are enrolled only from a signed-in session).
- **Sessions** — HS256 tokens, 12-hour expiry, renewed while the desk is open but never
  past a week from the last PIN. Every token names its device, and the server checks
  that device on every call: removing a device, signing out, or "sign out everywhere"
  ends sessions on the server immediately, open live feeds included.
- **The cookie** — the token is sealed with AES-256-GCM under a key only the dashboard
  deployment holds, in an httpOnly, Secure, `__Host-` cookie. `proxy.ts` opens it
  before serving any page or relaying any call, so a forged or altered cookie never
  reaches a page. The browser never sees the token in readable form and never learns
  the API origin; every REST call is relayed server-side.
- **WebSocket** — a browser cannot send an `Authorization` header on a WS handshake, and
  putting a session token in a URL leaves it in proxy logs. Instead the dashboard mints a
  ~60-second, single-purpose ticket over authenticated HTTP. A captured socket URL is
  worthless within the minute, can never be replayed against the REST API, and the
  socket closes when the session behind it ends.
- **Login throttling** — five wrong PINs from an address lock it out, doubling to an
  hour. An approved device has a lockout of its own, so guesses from elsewhere never
  lock the operator out. Wrong PINs from unapproved devices are also capped across all
  addresses together: past the cap, PIN sign-in from new devices pauses for an hour.
- **Requests from other sites** — every state-changing route refuses a request whose
  `Origin` / `Sec-Fetch-Site` says it did not come from the desk's own pages.
- **Audit log** — every sign-in, failed attempt, device approval or removal and control
  action is recorded with operator, action, detail and the visitor's address, readable
  at `/api/audit` and on the Journal page.

## Secrets the processes do not get

The API starts trading programs, the after-close check and the acceptance gate. None
of them is given the API's own secrets (the dashboard's signing key, PIN hash, relay
secret, backup passphrase and upload address): a program that prints its environment
cannot leak a way into the dashboard. The acceptance gate, which imports uploaded code
before it is trusted, gets no secret at all — broker credentials included
(`app/child_env.py`).

## Guarded actions

Two actions can lose money if triggered by accident, so both require an exact typed
phrase the UI does not prefill:

| Action | Phrase | Extra guard |
|---|---|---|
| Flatten an open position | `FLATTEN` | Refused when the book is empty |

Stopping the engine is refused outright while a position is open, since an unmanaged
position has no stop-loss watcher. Flatten first, or square off in the Angel One app.

## Network exposure

The API binds to `127.0.0.1` only. Caddy terminates TLS and is the sole public listener.
With `MERIDIAN_RELAY_SECRET` set, the API answers no REST call that does not prove it
holds that secret — only the dashboard's server-side relay does — so the sign-in route
cannot even be reached from the internet. The secret itself never travels: each call
carries an HMAC over the current time, valid for five minutes, so nothing copied out of
a proxy log can be replayed later. `/health` stays open for monitoring and says only that the
service is up; the live WebSocket stays reachable and requires a valid ticket.

The dashboard serves a strict Content-Security-Policy: no third-party script, frame,
font or image, and connections only to itself and the live feed, so even an injected
script would have nowhere to send what it read. Responses carry HSTS, `nosniff`,
`frame-ancestors 'none'`, COOP/CORP same-origin and `noindex`.

Two separate gates control inbound traffic on Oracle Cloud and traffic needs both: the OCI
Security List in the console, and the VM's own iptables rules. `deploy/install.sh` handles
the second; the first is a manual step in the console.

## What CI checks

Besides the tests: every tracked file is scanned for credential-shaped text
(`tests/test_repo_secrets.py`) and the build fails on a high or critical advisory in the
dashboard's runtime dependencies. The workflow's token is read-only.

## If something looks wrong

1. **Flatten** from the dashboard, or square off in the Angel One app.
2. **Stop the engine** and **disarm automation** so the scheduler does not restart it.
3. Switch the mode back to **paper** before investigating.
4. Read the Journal page — engine runs, control commands, and the audit log are all there.

If you suspect the PIN leaked or a device was lost: Controls → Devices and sessions →
remove the device (or "sign out everywhere"), then change the PIN with
`scripts/bootstrap_secrets.py --write`, which also rotates `MERIDIAN_JWT_SECRET` and so
invalidates every issued token. A stranger with the PIN still cannot sign in without an
approval you would have to type their code for.
