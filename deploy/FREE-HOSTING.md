# Running the whole thing for free

Three pieces, all on free tiers, no card charged:

| Piece | Where | Cost |
|---|---|---|
| Dashboard (the UI) | **Vercel** | Free forever |
| Bot (the algorithm) | **Oracle Cloud Always Free** VM in Mumbai | Free forever |
| HTTPS for the bot | **Tailscale Funnel** | Free |
| Phone app | Expo Go, or the dashboard added to your home screen | Free |

---

## Why the bot cannot live on Vercel

Vercel runs serverless functions. A function gets 10 seconds on the Hobby
plan (300 at most on paid), then it is killed. It has no persistent disk, it
cannot spawn a subprocess that outlives the request, and it cannot hold a
WebSocket server open.

Your algorithm needs a process alive from 09:15 to 15:45 — six and a half
hours — polling the broker twice a second, with a disk that still holds
`equity_book.json` tomorrow morning. There is no configuration of Vercel that
does that. It is not a limit you can pay your way around; it is what
serverless means.

So Vercel serves the dashboard, which is exactly what it is good at, and a
small always-on VM runs the bot.

---

## 1. The bot — Oracle Cloud Always Free

Oracle's free tier is the only one that is genuinely free forever *and* has an
India region. Both matter: every LTP poll is a round trip to Angel One, and
the bot polls twice a second while holding a position.

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com). Pick **India West
   (Mumbai)** or **India South (Hyderabad)** as your home region — this cannot
   be changed later. A card is required for identity verification; the Always
   Free resources are not charged.

2. Create a VM instance:
   - Image: **Ubuntu 24.04**
   - Shape: **VM.Standard.A1.Flex** (Ampere ARM), 1 OCPU, 6 GB RAM
   - Tick **Assign a public IPv4 address**
   - Save the SSH key it offers you

   > **"Out of host capacity" is the normal experience.** Free ARM in Mumbai
   > is heavily oversubscribed. Do not keep retrying the same shape — switch
   > to the AMD one, which almost always has room:
   >
   > **Shape → VM.Standard.E2.1.Micro** (1/8 OCPU, 1 GB), also Always Free.
   >
   > 1 GB is enough. The bot holds about 130 MB resident; the one spike is
   > parsing Angel One's 30 MB scrip master on the first run of each day, and
   > `deploy/setup.sh` adds 2 GB of swap to absorb it. After that first parse
   > the contracts are cached to disk at ~3% of the size, so restarts are
   > cheap. If you skip the setup script, add swap yourself before starting:
   >
   > ```bash
   > sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
   > sudo mkswap /swapfile && sudo swapon /swapfile
   > echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   > ```
   >
   > Still stuck? See [the alternatives](#if-oracle-will-not-cooperate).

3. Open the firewall. Oracle blocks everything by default, in two places:

   ```bash
   # On the VM
   sudo iptables -I INPUT 1 -p tcp --dport 8000 -j ACCEPT
   sudo netfilter-persistent save
   ```

   You do **not** need to open port 8000 in the Oracle security list if you
   use Tailscale (step 2 below), which is the recommended path.

4. Install and start:

   ```bash
   sudo timedatectl set-timezone Asia/Kolkata
   curl -fsSL https://get.docker.com | sudo sh
   sudo git clone https://github.com/<you>/meridiancapital.git /opt/meridiancapital
   cd /opt/meridiancapital
   cp .env.example .env
   nano .env
   ```

   Fill in five things:

   ```ini
   ADMIN_USER=Sehej            # what you type into the app
   ADMIN_PASSWORD=...          # your passcode — hashed at boot, never stored plain
   ANGEL_API_KEY=...           # the five broker values
   ANGEL_SECRET_KEY=...
   ANGEL_CLIENT_ID=...
   ANGEL_PASSWORD=...
   ANGEL_TOTP_SECRET=...
   API_TOKEN=...               # optional, only for curl and scripted exports
   ```

   Type the broker credentials yourself rather than pasting them through any
   tool — they are the login to your money. Then:

   ```bash
   sudo docker compose -f deploy/docker-compose.yml up -d --build
   curl http://localhost:8000/api/health
   ```

   `login_available: true` in that response means the password took.

---

## 2. HTTPS — Tailscale Funnel

The dashboard on Vercel is served over HTTPS. **A browser will refuse to let
an HTTPS page call a plain-HTTP server.** So the bot needs an HTTPS address.
Tailscale Funnel gives you one free, with no domain to buy and no ports to
open to the internet.

```bash
curl -fsSL https://tailscale.com/install.sh | sudo sh
sudo tailscale up
sudo tailscale funnel --bg 8000
sudo tailscale funnel status
```

That prints a permanent address like:

```
https://meridian.tail1a2b3c.ts.net
```

That is your **server URL**. It survives reboots and IP changes.

<details>
<summary>Alternative: Cloudflare Tunnel (if you already own a domain)</summary>

```bash
curl -fsSL https://pkg.cloudflare.com/cloudflared.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb
cloudflared tunnel login
cloudflared tunnel create meridian
cloudflared tunnel route dns meridian trade.yourdomain.com
cloudflared tunnel run --url http://localhost:8000 meridian
```

Then install it as a service with `sudo cloudflared service install`.
</details>

---

## 3. The dashboard — Vercel

From your own machine, once:

```bash
npm i -g vercel
cd meridiancapital/web
vercel --prod
```

Or without any CLI: push this repo to GitHub, go to
[vercel.com/new](https://vercel.com/new), import it, and set **Root Directory**
to `web`. Leave the framework as *Other*; there is no build step.

You get something like `https://meridian-capital.vercel.app`. Open it, enter
your Tailscale URL and the API token, and you are connected.

There are no secrets in the Vercel deployment. The dashboard is static HTML —
your token is typed in on the device and kept in that browser's local storage.
Nothing is stored on Vercel's side.

---

## 4. The phone

**Free, nothing to install** — open the Vercel URL in Safari or Chrome, then
Share → *Add to Home Screen*. It runs full-screen with its own icon and
behaves like an app. This is the fastest route and costs nothing, ever.

**Free, the native app** — install Expo Go from the store, then on any
computer:

```bash
cd mobile && npm install && npx expo start
```

Scan the QR code. Expo Go is free and unlimited. The catch is that the dev
server has to be running for the app to load, so this suits testing more than
daily use.

**A standalone app you keep** — EAS Build's free tier covers roughly 30 builds
a month, which is far more than you need for an app you build once:

```bash
npm i -g eas-cli
eas login
eas build:configure
eas build --platform android --profile preview   # installable .apk
```

Download the APK from the link it prints and install it. Android only —
iOS needs a $99/year Apple Developer account to install on a real device,
which is exactly why the home-screen web app above is worth using on iPhone.

---

## Putting it together

```
  your phone
      │  HTTPS
      ▼
  meridian-capital.vercel.app          (dashboard, free, static)
      │  HTTPS + bearer token
      ▼
  meridian.tail1a2b3c.ts.net           (Tailscale Funnel, free)
      │
      ▼
  Oracle Cloud VM, Mumbai              (bot, free, always on)
      │
      ▼
  Angel One SmartAPI
```

---

## If Oracle will not cooperate

In order of how much I would actually recommend them. Every one runs the same
repo with the same commands — only the box changes.

**1. Oracle's AMD shape, not ARM.** Before giving up on Oracle: the capacity
problem is specific to `VM.Standard.A1.Flex` (ARM). `VM.Standard.E2.1.Micro`
(AMD, 1 GB) is also Always Free and is usually available immediately. With the
swap the setup script adds, it runs this bot fine. Try this first — it keeps
you on free-forever in Mumbai.

**2. Google Cloud, ₹0 for 90 days.** $300 of credit, any region including
Mumbai, no capacity lottery. An `e2-small` runs comfortably inside it for
three months. Their permanent free `e2-micro` is US-only, so when the credit
ends you either pay ~₹1,000/mo or move. Good if you want to be running today
and decide later.

**3. AWS free tier, ₹0 for 12 months.** `t3.micro` in Mumbai, 1 GB, free for a
year from signup. Longer runway than Google's credit, same one-day setup. Add
swap as above.

**4. Your own hardware — [full guide here](RASPBERRY-PI.md).** A Raspberry Pi
4/5, or any old laptop that can stay plugged in and closed. Free forever, and
the lowest latency to Angel One you will get since it is physically in India:
single-digit milliseconds against 40–60 ms from a Mumbai cloud VM. This does
not contradict wanting a laptop-free setup — Tailscale means your phone
reaches it from anywhere, and you never touch the machine after setup. If you
have a Pi in a drawer, this is genuinely the best option on this list.

```bash
curl -fsSL https://raw.githubusercontent.com/<you>/meridiancapital/main/deploy/pi-setup.sh \
  | sudo REPO_URL=https://github.com/<you>/meridiancapital.git bash
```

**Not recommended:** Render and Koyeb free tiers sleep after inactivity, which
kills a bot that must hold a position; Railway's free credit runs out mid-month;
Fly.io dropped its free allowance. Anything that sleeps or has no India region
is the wrong shape for this.

Paid, if you would rather just not think about it:

| Provider | Region | Spec | Cost |
|---|---|---|---|
| DigitalOcean | Bangalore | 1 vCPU / 2 GB | ~₹1,000/mo |
| AWS Lightsail | Mumbai | 1 vCPU / 2 GB | ~₹850/mo |
| Azure | Central India | B1s | ~₹800/mo |

### What it actually needs

Measured, not guessed: ~130 MB resident once running. The only pressure point
is the first scrip-master parse of each day, which can touch a few hundred
megabytes transiently. So:

- **2 GB RAM** — comfortable, no swap needed.
- **1 GB RAM + 2 GB swap** — works, and is what the free shapes give you.
- **512 MB** — do not.

---

## Checks before you trust it

```bash
# The container's clock must be IST, or it trades at the wrong hours
sudo docker compose -f deploy/docker-compose.yml exec meridian date

# The scheduler should show 09:15 on the next trading day
curl -H "X-API-Token: <token>" http://localhost:8000/api/schedule

# HTTPS reachable from outside
curl https://<your>.ts.net/api/health
```

Angel One ties a session to the calling IP. If your account has IP
restrictions enabled, whitelist the VM's address (`curl https://api.ipify.org`
on the VM) in the SmartAPI dashboard.

---

## Backups

`equity_book.json` is your compounding capital. It lives in the
`meridian-data` Docker volume and is the one file you cannot regenerate.

```bash
sudo docker run --rm -v meridian-data:/data -v "$PWD:/backup" alpine \
  tar czf /backup/meridian-$(date +%F).tar.gz -C /data .
```

Weekly. Copy it off the VM.
