# Deployment

Backend on an Oracle Cloud Always Free VM, dashboard on Vercel. Roughly an hour end to
end, most of it waiting for DNS.

## 1. The Oracle VM

In the OCI console, create a **Compute instance**:

- **Shape** — `VM.Standard.A1.Flex` (Ampere ARM), 2 OCPU / 12 GB is ample. The Always
  Free allowance is 4 OCPU / 24 GB total.
- **Image** — Ubuntu 22.04 or 24.04.
- **Networking** — assign a public IPv4 address.
- **SSH** — upload your public key.

> Ampere capacity is often unavailable in a given availability domain. If creation fails
> with "Out of host capacity", try another AD in the same region, or retry later — it
> frees up. Two AMD `VM.Standard.E2.1.Micro` instances are the fallback; one is enough to
> run this, though with 1 GB RAM you should add swap before installing pandas.

### Open the ports — both gates

Traffic needs **both** of these. Missing either looks identical from outside: a timeout.

**Gate 1 — OCI Security List.** VCN → Subnet → Security List → Add Ingress Rules:

| Source | Protocol | Destination port |
|---|---|---|
| `0.0.0.0/0` | TCP | 80 |
| `0.0.0.0/0` | TCP | 443 |

**Gate 2 — the VM's own iptables.** Oracle's Ubuntu image ships a chain that drops
everything except SSH, and it is *not* managed by ufw. `install.sh` inserts the rules and
persists them, so you do not need to do this by hand.

## 2. A hostname

You need one for TLS — browsers will not open a `wss://` connection from an HTTPS page to
a bare IP with a self-signed certificate.

- **A domain you own** — point an `A` record at the VM's public IP.
- **Free alternative** — [DuckDNS](https://duckdns.org) gives you
  `something.duckdns.org` pointed at any IP. Caddy gets a Let's Encrypt certificate for it
  over HTTP-01 exactly as it would for your own domain.

## 2b. Sign-in: PIN and Face ID

The dashboard takes a single operator credential. Set or change it on the VM:

```bash
cd /path/to/meridiancapital
sudo python3 backend/scripts/bootstrap_secrets.py --write
sudo systemctl restart meridian-api
```

It prompts twice, never echoes, and rewrites only `MERIDIAN_PASSWORD_HASH` (and
`MERIDIAN_JWT_SECRET` unless you pass `--keep-jwt`) inside
`/etc/meridian/meridian.env`. Everything else in that file — your Angel One
credentials — is left untouched. Rotating the JWT secret signs out every open
session, which is what you want after a credential change.

Only the hash is stored, so there is no way to recover a forgotten PIN; run the
script again.

> A 4-digit PIN is 10,000 combinations. What makes that survivable is the login
> limiter: five attempts, then a lockout that doubles to an hour, applied to the
> Face ID route as well so it cannot be used to sidestep the lock. Enrol Face ID
> and treat the PIN as the fallback.

### Face ID / Touch ID

WebAuthn binds a credential to the origin of the **page**, which is the Vercel
dashboard, not this API. Both must be set in `/etc/meridian/meridian.env`:

```bash
MERIDIAN_RP_ID='meridiancapital.vercel.app'
MERIDIAN_RP_ORIGIN='https://meridiancapital.vercel.app'
```

Use your own domain if you have one pointed at the dashboard. Getting these
wrong produces a credential the browser refuses to use — it fails closed, it
does not silently weaken anything.

Then, from the phone: sign in with the PIN, open **Controls**, and press *Enrol
this device*. The private key stays in the phone's secure enclave; the server
stores only a public key and a counter. Remove a lost device from the same card.

## 3. Install

```bash
ssh ubuntu@<vm-ip>
git clone https://github.com/sehejsharm/meridiancapital.git
cd meridiancapital
sudo bash backend/deploy/install.sh api.your-host.example https://your-app.vercel.app
```

The second argument is your dashboard's own origin. Caddy 403s any browser
request whose `Origin` is not on its allow-list, and the WebSocket live feed does
send one — omit it and the dashboard signs in fine and then never updates, which
is a confusing way to find a one-line mismatch.

The script is idempotent — re-run it after every code update. It never touches
`/etc/meridian/meridian.env` once that file exists, so credentials survive.

It installs Python and Caddy, creates the `meridian` service account, builds the venv,
enables `chrony` (the engine refuses to start if NTP says the clock is off by more than
30s), installs the systemd unit, writes the Caddyfile, and opens the local firewall.

## 4. Configure

```bash
sudo -u meridian /opt/meridian/backend/.venv/bin/python \
     /opt/meridian/backend/scripts/bootstrap_secrets.py
```

It prompts for a dashboard password without echoing, and prints the two lines to paste in.
Then edit the environment file:

```bash
sudo nano /etc/meridian/meridian.env
```

Fill in:

- `ANGEL_API_KEY`, `ANGEL_CLIENT_ID`, `ANGEL_PASSWORD`, `ANGEL_TOTP_SECRET` — **the
  rotated ones**, see [SECURITY.md](SECURITY.md)
- `MERIDIAN_PASSWORD_HASH`, `MERIDIAN_JWT_SECRET` — from the command above
- `MERIDIAN_CORS_ORIGINS` — your Vercel URL, e.g. `https://meridian.vercel.app`
- Leave `MERIDIAN_TRADING_MODE=paper`

Then:

```bash
sudo systemctl restart meridian-api
curl -s https://api.your-host.example/health
```

### Angel One account prerequisites

- **Historical Data API** must be enabled — the strategy needs 92+ one-minute candles and
  will log `candle feed empty` without it.
- The account needs enough margin for `INTRADAY` NFO option buying.
- Capital must be at or above ₹50,000. Below that the engine refuses to start; this build
  was validated at ₹50k and the backtest froze below it.

## 5. The dashboard on Vercel

From [vercel.com/new](https://vercel.com/new), import the repository:

| Setting | Value |
|---|---|
| Root Directory | `frontend` |
| Framework | Next.js (detected) |
| Build / install | defaults |

Add one environment variable, for all environments:

```
MERIDIAN_API_URL = https://api.your-host.example
```

**Not** `NEXT_PUBLIC_` prefixed. It is read server-side by Next route handlers; prefixing
it would ship your API origin to every browser.

Deploy, then go back and make sure `MERIDIAN_CORS_ORIGINS` on the VM matches the Vercel
URL you were assigned, and `systemctl restart meridian-api`.

> Vercel gives each preview deployment its own URL. If you want previews to work, add
> them to `MERIDIAN_CORS_ORIGINS` as a comma-separated list — or just use production.

## 6. First run

1. Sign in to the dashboard with the operator password.
2. **Controls → NSE holiday calendar** — add this year's dates from the NSE circular.
3. **Controls → Automation → Arm** — anything you have started will now start itself at
   09:05 and stop at 15:25 on trading days. The engine is up ten minutes before the 09:15
   bell so it has time to log in to Angel, pull the scrip master and build its candles.
4. Start an algorithm; the desk asks whether it runs on paper or real money. Paper is
   worth a full session first.
5. Check the Blotter, the Journal, and the end-of-day report.
6. Stop it, then start it again on **real money** when you are satisfied.

## Updating

```bash
cd ~/meridiancapital && git pull
sudo bash backend/deploy/install.sh api.your-host.example https://your-app.vercel.app
```

The frontend redeploys itself on push.

Update outside market hours. `install.sh` restarts the API, and although the engine
survives that (it runs detached), there is no reason to do it with money on the table.

## Operating

```bash
journalctl -u meridian-api -f          # API logs
systemctl status meridian-api          # service state
ls -la /var/lib/meridian/              # database, state file, scrip cache
sudo -u meridian sqlite3 /var/lib/meridian/meridian.db "select * from trades order by id desc limit 5;"
```

The engine's own output goes to the database and is read through the dashboard's Journal
page, which is more useful than the journal — it carries the structured payloads.

### Disk

Always Free gives 200 GB. Events are pruned via `prune_events()`; the scrip cache is
refreshed every 12 hours. Nothing here grows unbounded, but `du -sh /var/lib/meridian`
occasionally costs nothing.

### If the VM's public IP changes

Angel's session headers include it. Leave `ANGEL_PUBLIC_IP` empty in the environment file
and it is discovered at each login, which handles a reboot that reassigns the address.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `/health` times out from outside, works over SSH | One of the two firewall gates is still closed — check the OCI Security List. |
| Dashboard shows "Offline" | `MERIDIAN_API_URL` wrong, or the API is down. `curl` the health endpoint from your laptop. |
| Dashboard loads, calls fail with CORS errors | `MERIDIAN_CORS_ORIGINS` does not match the Vercel URL exactly, scheme included. |
| Header shows "Polling" rather than "Streaming" | WebSocket upgrade is not getting through. Check Caddy is proxying `/ws/live` — the shipped Caddyfile does. |
| `candle feed empty` in the log | Historical Data API is not enabled on the Angel account. |
| Engine exits immediately at startup | Read the Journal. Usually missing credentials, capital below ₹50,000, or clock drift over 30s. |
| `Out of host capacity` creating the VM | Ampere is busy in that availability domain. Try another AD, or retry later. |
