# Running it on a Raspberry Pi

Free forever, and the lowest latency to Angel One of any option — the Pi is
physically in India, so a quote round-trip is single-digit milliseconds instead
of the 40–60 ms a Mumbai cloud VM gives you and the 250 ms+ a US free tier
would.

You touch the Pi once during setup. After that it sits in a drawer and your
phone reaches it from anywhere.

---

## What you need

| | |
|---|---|
| **Board** | Raspberry Pi 4 (2 GB or more) or Pi 5. A Pi 3 works but builds slowly. |
| **Storage** | 32 GB A2 microSD, or better, any USB SSD |
| **Power** | The official adapter — undervoltage causes silent instability |
| **Network** | Ethernet if you can; Wi-Fi is fine |
| **OS** | **Raspberry Pi OS 64-bit** (Lite is ideal — no desktop needed) |

**Not usable:** Pi Zero / Zero 2 W (512 MB), or any 32-bit OS. numpy, pandas
and scipy publish no prebuilt 32-bit ARM wheels, so the install would try to
compile them and fail.

---

## 1. Flash the card

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/).

- **OS** → Raspberry Pi OS (other) → **Raspberry Pi OS Lite (64-bit)**
- Click the **gear icon** before writing and set:
  - Hostname: `meridian`
  - **Enable SSH** → *Use password authentication*
  - Username and password
  - Wi-Fi credentials and country, if not using Ethernet
  - **Locale / timezone: Asia/Kolkata**

That preconfiguration is what makes this headless — no monitor or keyboard.

Boot the Pi and SSH in from any machine on the same network:

```bash
ssh <your-user>@meridian.local
# or use the IP from your router if .local does not resolve
```

---

## 2. One command

```bash
curl -fsSL https://raw.githubusercontent.com/sehejsharm/meridiancapital/claude/paper-trading-bot-setup-y5nxn5/deploy/pi-setup.sh \
  | sudo REPO_URL=https://github.com/sehejsharm/meridiancapital.git bash
```

It refuses to continue on a 32-bit OS or under 1 GB of RAM, then handles the
things a Pi needs that a cloud VM does not:

- **Clock.** A Pi has no battery-backed clock. After a power cut it boots
  believing it is whenever it last shut down, and a trading bot with the wrong
  idea of the time is worse than one that is switched off. The script forces
  NTP and waits for an actual sync before continuing.
- **Swap.** Pi OS ships 100 MB, which is not enough for the first scrip-master
  parse of each day. Raised to 2 GB, with `swappiness=10` so it prefers RAM.
- **SD wear.** `/var/log` moves to RAM, removing most of the routine write
  churn.
- **Watchdog.** The hardware watchdog reboots the board if the kernel wedges,
  since nobody is in the room to power-cycle it.
- Docker, the repo, and a generated API token.

It stops and asks you to fill in `.env`:

```bash
sudo nano /opt/meridiancapital/.env
```

```ini
ADMIN_USER=Sehej
ADMIN_PASSWORD=...        # your app passcode
ANGEL_API_KEY=...
ANGEL_SECRET_KEY=...
ANGEL_CLIENT_ID=...
ANGEL_PASSWORD=...
ANGEL_TOTP_SECRET=...
```

Save with `Ctrl+O`, Enter, `Ctrl+X`. Then run the same command again — it picks
up where it left off, builds, and starts. First build is 5–10 minutes.

---

## 3. Reach it from anywhere

At this point it works on your home Wi-Fi only. Tailscale gives it a permanent
HTTPS address without opening a single port on your router:

```bash
curl -fsSL https://tailscale.com/install.sh | sudo sh
sudo tailscale up                    # opens a link to authorise
sudo tailscale funnel --bg 8000
sudo tailscale funnel status
```

That prints something like `https://meridian.tail1a2b3c.ts.net`. **That is your
server address** — put it in the app. It survives reboots and works on mobile
data.

Nothing about your home network is exposed. Tailscale makes the outbound
connection; there is no inbound port forward and no firewall change.

---

## 4. Check it

```bash
curl http://localhost:8000/api/health          # {"ok":true, "login_available":true}
docker compose -f /opt/meridiancapital/deploy/docker-compose.yml exec meridian date
curl -H "X-API-Token: $(grep ^API_TOKEN /opt/meridiancapital/.env | cut -d= -f2)" \
     http://localhost:8000/api/schedule
```

The `date` must read IST, and `next_start` must show 09:15 on the next trading
day. If either is wrong, fix it before you trust it with a session.

Then open your Vercel dashboard, enter the `.ts.net` address and your login,
and add it to your home screen.

---

## Living with a Pi

**Power cuts.** Docker restarts the stack automatically, and the algorithm
reloads its open position from `paper_state.json`, so a reboot mid-session
picks the trade back up. If the server comes back inside market hours, the
scheduler notices and restarts the bot rather than losing the day.

What it cannot do is trade while the power is off. If your area has frequent
cuts, a small UPS — even a ₹2,000 mini-UPS for the router and Pi — turns a lost
session into a non-event. Worth it if the bot is managing real money.

**Angel One and your IP.** Home connections usually have a dynamic IP. That is
fine unless you have enabled IP restrictions on your SmartAPI account — if you
have, either turn them off or expect to update the whitelist when your ISP
changes it.

**Backups.** `equity_book.json` is your compounding capital and the one file
you cannot regenerate. SD cards fail without warning:

```bash
sudo docker run --rm -v meridian-data:/data -v "$HOME:/backup" alpine \
  tar czf /backup/meridian-$(date +%F).tar.gz -C /data .
```

Copy it off the Pi. Weekly. To make it automatic:

```bash
sudo crontab -e
# add:
0 18 * * 5 docker run --rm -v meridian-data:/data -v /home/pi:/backup alpine \
  tar czf /backup/meridian-$(date +\%F).tar.gz -C /data .
```

**Heat.** A Pi 4 under sustained load throttles without a heatsink. This
workload is light, but stick a ₹150 heatsink on it and check occasionally:

```bash
vcgencmd measure_temp        # under 70°C is comfortable
vcgencmd get_throttled       # 0x0 means it has never throttled
```

**Updates.**

```bash
cd /opt/meridiancapital && sudo git pull
sudo docker compose -f deploy/docker-compose.yml up -d --build
```

Do it outside 09:15–15:45. During a session it stops the bot mid-flight — hit
Stop in the app first so any open position is flattened properly.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `This is a 32-bit OS` | Wrong Pi OS image | Reflash with the 64-bit build |
| Build fails compiling numpy/scipy | 32-bit OS, or an unavailable wheel | Confirm `uname -m` reports `aarch64` |
| Bot starts at the wrong hour | Clock never synced after a power cut | `sudo timedatectl set-ntp true`, check `timedatectl` |
| Killed during startup | Swap too small | `free -h`; re-run `pi-setup.sh` |
| `meridian.local` will not resolve | No mDNS on your network | Use the IP from your router |
| Slow or random freezes | Undervoltage or heat | Official PSU; `vcgencmd get_throttled` |
| Works at home, not on mobile data | Tailscale Funnel not running | `sudo tailscale funnel status` |
| Dashboard cannot reach the server | HTTPS page calling a plain-HTTP address | Use the `.ts.net` address, not the LAN IP |
