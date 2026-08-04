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

   > **Expect to retry.** Free ARM capacity in Mumbai is frequently exhausted
   > and you will see *"Out of host capacity"*. Try again over a few hours, or
   > try Hyderabad. If you cannot get one, skip to
   > [the paid fallback](#if-the-free-vm-will-not-come-up) — it is about ₹800
   > a month and takes five minutes.

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
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # copy this
   nano .env          # paste it as API_TOKEN, fill in the five ANGEL_* values
   sudo docker compose -f deploy/docker-compose.yml up -d --build
   curl http://localhost:8000/api/health
   ```

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

## If the free VM will not come up

Oracle's free ARM capacity genuinely runs out. Rather than fighting it for
days, any of these work the same way — same repo, same commands:

| Provider | Region | Spec | Cost |
|---|---|---|---|
| DigitalOcean | Bangalore | 1 vCPU / 2 GB | ~₹1,000/mo |
| AWS Lightsail | Mumbai | 1 vCPU / 2 GB | ~₹850/mo |
| Azure | Central India | B1s | ~₹800/mo |

2 GB RAM is the floor — pandas, scipy and the scrip master need about 600 MB
resident, and 1 GB instances get killed under load.

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
