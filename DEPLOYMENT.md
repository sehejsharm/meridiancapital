# Deployment

Backend on an Oracle Cloud Always Free VM, dashboard on Vercel. Roughly an hour end to
end, most of it waiting for DNS.

## 1. The Oracle VM

In the OCI console, in your **home region**, create a **Compute instance**:

- **Shape** — *Change shape → Ampere →* `VM.Standard.A1.Flex`, **2 OCPU / 12 GB**. That
  is the whole Always Free Ampere allowance since Oracle halved it in June 2026 (it was
  4 / 24), shared across every A1 instance in the tenancy. Twelve times the memory of an
  `E2.1.Micro`, and ample for this.
- **Image** — Canonical Ubuntu 24.04. With an A1 shape the console picks the `aarch64`
  build; every package in `requirements.txt` has an ARM wheel.
- **Boot volume** — *Specify a custom size*: 100 GB, performance **Balanced** (10 VPU).
  See the storage note below before going higher.
- **Networking** — assign a public IPv4 address.
- **SSH** — upload your public key.

The card should read **"Always Free-eligible"**. If it does not, the shape is wrong:
stop and go back.

> Ampere capacity is often unavailable in a given availability domain. If creation fails
> with "Out of host capacity", try another AD in the same region, or retry later — it
> frees up. **Do not** fall back to an `E3`/`E4`/`E5`/`Standard3` "Flex" shape: those are
> paid, even for an hour. The only free fallback is `VM.Standard.E2.1.Micro` (1 GB RAM;
> add swap before installing pandas).

### Staying on Always Free

Oracle bills anything outside the Always Free list the moment a trial ends — an `E5`
instance left running for a day becomes an invoice. To stay at zero:

| Resource | Free limit (whole tenancy) | Keep it at |
|---|---|---|
| Ampere A1 compute | 2 OCPU / 12 GB total | one VM, 2 OCPU / 12 GB |
| AMD compute | two `VM.Standard.E2.1.Micro` | nothing else AMD — no `E3`/`E4`/`E5` |
| Block storage | 200 GB **total**, boot volumes included | every boot + block volume added up, under 200 GB |
| Volume performance | Balanced (10 VPU) | never Higher/Ultra High |
| Volume backups | 5 | the nightly backup here is a file on disk, not an OCI backup |
| Region | home region only | create nothing in another region |

Two quieter traps: a terminated instance keeps its boot volume unless you tick
*Permanently delete the attached boot volume*, and that orphan still counts toward the
200 GB (check *Storage → Boot volumes*). And on a free-tier account (not upgraded to Pay
As You Go) Oracle may stop an Always Free instance it judges idle — CPU, network and, on
A1, memory all under 20% for seven days. A free external uptime check on
`https://<your-host>/health` tells you if that happens.

*Billing & Cost Management → Budgets* will email you at a threshold of your choosing; one
at 0.01 in your currency catches any charge within a day.

### Storage

Always Free block storage is 200 GB **across all boot and block volumes in the tenancy**.
A 100 GB boot volume leaves room for the old Micro's ~47 GB boot volume while both exist.
Once the old VM and its boot volume are deleted you can grow this one
(*Boot volume → Edit → size*, then reboot outside market hours; Ubuntu expands the root
filesystem at boot) as long as the total stays under 200 GB.

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

Removed from the dashboard. The control plane's passkey endpoints are still there
and dormant — with no enrolment card no device can be enrolled, so the login page
never offers Face ID. The enrolment UI can be restored from git history
(`frontend/components/PasskeyManager.tsx`) if it is wanted again.

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

- **The VM's IP must be whitelisted.** Angel accepts orders only from the static IP
  registered for your SmartAPI app, and rejects the rest with `AG7002` — exits included.
  Get the VM's address with `curl -s https://api.ipify.org`, set it at
  smartapi.angelone.in → My Apps → edit app, wait a few minutes, then put the same value
  in `ANGEL_PUBLIC_IP` in `/etc/meridian/meridian.env`. A live engine then refuses to start
  if its traffic ever leaves from a different address. Reserve the VM's public IP in
  Oracle so a reboot does not change it.
- **Historical Data API** must be enabled — the strategy needs 92+ one-minute candles and
  will log `candle feed empty` without it.
- The account needs enough margin for `CARRYFORWARD` NFO option buying.
- There is no capital floor. Sizing skips any trade that breaches the equity or risk caps,
  so an account too small for one lot simply waits.

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

### Recommended: the relay secret

Every sign-in through the dashboard reaches the VM from Vercel's servers, so without
this the login lockout is keyed on Vercel's address — shared by everyone — and a bot's
five wrong guesses lock you out too. With a shared secret the dashboard forwards the
real visitor's address and the lockout lands on whoever is guessing. On the VM:

```bash
S=$(openssl rand -hex 32)
echo "MERIDIAN_RELAY_SECRET=$S" | sudo tee -a /etc/meridian/meridian.env >/dev/null
sudo systemctl restart meridian-api
echo "$S"        # copy this
```

Then in Vercel add `MERIDIAN_RELAY_SECRET` with that value (not `NEXT_PUBLIC_`), and
redeploy. Unset on either side, the old behaviour stands; nothing breaks.

> Vercel gives each preview deployment its own URL. If you want previews to work, add
> them to `MERIDIAN_CORS_ORIGINS` as a comma-separated list — or just use production.

## 6. First run

1. Sign in to the dashboard with the operator password.
2. **Controls → NSE holiday calendar** — NSE's published list for the year is already
   loaded; check it, and add any closure NSE declares at short notice.
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

## Moving to a new VM

For example from an `E2.1.Micro` to an Ampere A1. Nothing in the code changes. What moves
is the database (trades, journal, audit log, every algorithm's source), the program
folders and their own state, the engine state file, `/etc/meridian/meridian.env`, and any
package you pip-installed into the venv for a program. Do it on an evening or a weekend,
with every algorithm stopped.

The old VM's public IP should move with it. It is the IP whitelisted at Angel, the one
DuckDNS points at, and the one an uploaded program may name in its own code (the
GANESH KAVACH program does: `PUBLIC_IP = "..."`, and it refuses to start from any other
address). A **reserved** public IP can be detached from one instance and attached to
another in the same region, so after the move none of those change.

1. **Create the new VM** as in section 1, **in the same region as the old one** (a
   reserved IP cannot leave its region), with a temporary public IP for setup, and add
   the same port 80/443 ingress rules if it is on a different subnet.
2. **Old VM** — bundle everything up:

   ```bash
   cd ~/meridiancapital && git pull
   sudo bash backend/deploy/migrate.sh export
   ```

   It refuses while anything is running. Then it **stops and disables the API on the old
   VM** before copying, so the two VMs can never both start the same algorithm at 09:05
   and nothing written after the copy is lost. It prints the bundle's path and the exact
   commands for the new VM.
3. **Copy the bundle across**, through your laptop:

   ```bash
   scp ubuntu@<old-ip>:meridian-move-*.tar.gz .
   scp meridian-move-*.tar.gz ubuntu@<new-ip>:
   ```

4. **New VM** — install, then restore:

   ```bash
   git clone https://github.com/sehejsharm/meridiancapital.git && cd meridiancapital
   sudo bash backend/deploy/install.sh <same-hostname> <same-dashboard-origin>
   sudo bash backend/deploy/migrate.sh import ~/meridian-move-<stamp>.tar.gz
   ```

   `import` warns that the new VM is not on the old IP yet and lists any algorithm that
   names that IP in its code. Both are expected; the next step settles them.
5. **Move the reserved IP.** Each edit is *Instance → Attached VNICs → (primary VNIC) →
   IPv4 Addresses → ⋮ on the primary private IP → Edit*:
   1. **New VM** → *Public IP type: No public IP* → Update. (Drops the temporary IP.)
   2. **Old VM** → *No public IP* → Update. The reserved IP is now unattached, still
      yours, listed under *Networking → IP Management → Reserved public IPs*.
   3. **New VM** → *Reserved public IP → Select existing* → the old IP → Update.

   SSH to the new VM on the old IP (your laptop may warn that the host key changed:
   `ssh-keygen -R <ip>`), then `sudo systemctl restart caddy` so it fetches its
   certificate. **DuckDNS, Vercel, Angel's whitelist, `ANGEL_PUBLIC_IP` and the program's
   `PUBLIC_IP` all stay as they are.**

   *Only if the old IP was ephemeral rather than reserved:* whitelist the new IP at
   Angel, set `ANGEL_PUBLIC_IP` to it, change it inside each program `import` listed and
   upload them again, and point the hostname at it.
6. **Check** `curl -s https://<hostname>/health` from your laptop, sign in, and confirm
   the blotter and journal show your history. Run a paper session first.
7. **Clean up** once you are satisfied: delete the bundle on all three machines (it holds
   your credentials), then terminate the old VM **with *Permanently delete the attached
   boot volume* ticked**, so it stops counting against the 200 GB. Never release the
   reserved IP: Angel's whitelist depends on it.

Until step 7, going back is reversing step 5 (the IP back to the old VM) and
`sudo systemctl enable --now meridian-api` there.

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

Always Free gives 200 GB of block storage for the whole tenancy, boot volumes included
(see *Storage* in section 1). Events are pruned via `prune_events()`; the scrip cache is
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
| `Out of host capacity` creating the VM | Ampere is busy in that availability domain. Try another AD, or retry later. Never fall back to a paid `E3`/`E4`/`E5` shape. |
| An Oracle invoice for "Standard E5 Compute" (or E4, E3) | A paid AMD instance was running after the trial ended. Terminate it, delete its boot volume, and follow *Staying on Always Free*. |
