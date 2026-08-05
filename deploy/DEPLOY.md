# Deploying

The whole point is that nothing lives on your laptop. A small always-on server
runs the algorithm; the phone is a window onto it.

---

## 1. Pick a server

Latency to Angel One matters — every LTP poll is a round trip, and the bot
polls twice a second while in a position. Put the box in India.

| Provider | Region | Size | Cost |
|---|---|---|---|
| DigitalOcean | Bangalore (BLR1) | Basic 1 vCPU / 2 GB | ~$12/mo |
| AWS Lightsail | Mumbai (ap-south-1) | 1 vCPU / 2 GB | ~$10/mo |
| Azure | Central India | B1s | ~$9/mo |

2 GB is the floor — pandas plus scipy plus the scrip master needs roughly
600 MB resident, and 1 GB boxes get killed under load. 25 GB disk is plenty.

Ubuntu 24.04 LTS.

---

## 2. Set it up

SSH in and run:

```bash
sudo REPO_URL=https://github.com/sehejsharm/meridiancapital.git bash \
  <(curl -fsSL https://raw.githubusercontent.com/sehejsharm/meridiancapital/claude/paper-trading-bot-setup-y5nxn5/deploy/setup.sh)
```

Or manually:

```bash
sudo timedatectl set-timezone Asia/Kolkata     # not optional
curl -fsSL https://get.docker.com | sudo sh
sudo git clone https://github.com/sehejsharm/meridiancapital.git /opt/meridiancapital
cd /opt/meridiancapital
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # your API token
nano .env                                       # fill in ANGEL_* and API_TOKEN
sudo docker compose -f deploy/docker-compose.yml up -d --build
```

Check it:

```bash
curl http://localhost:8000/api/health
sudo docker compose -f deploy/docker-compose.yml logs -f
```

---

## 3. Put HTTPS in front of it

**Do this before you use it from outside your own network.** The API token
goes out in a header on every request and in the query string on downloads.
On plain HTTP, anyone between your phone and the server can read it — and that
token can start and stop a bot that trades your money.

Point a domain's A record at the server, then:

```bash
echo 'DOMAIN=trade.example.com' | sudo tee -a /opt/meridiancapital/.env
cd /opt/meridiancapital
sudo docker compose -f deploy/docker-compose.yml --profile tls up -d
```

Caddy gets a Let's Encrypt certificate automatically. Close port 8000 to the
world afterwards:

```bash
sudo ufw allow 22,80,443/tcp
sudo ufw deny 8000/tcp
sudo ufw enable
```

The app's server URL is then `https://trade.example.com`.

### Without a domain

Tailscale is the easy alternative — the server and your phone join a private
network and nothing is exposed publicly:

```bash
curl -fsSL https://tailscale.com/install.sh | sudo sh
sudo tailscale up
```

Install Tailscale on the phone too, and use the server's `100.x.y.z` address
as the server URL.

---

## 4. Angel One will need the server's IP

SmartAPI ties sessions to the calling IP. Get it with:

```bash
curl https://api.ipify.org
```

If your Angel One account has IP restrictions enabled, whitelist that address
in the SmartAPI dashboard. Reboots can change the IP on some providers —
attach a reserved/static IP so it does not shift under you.

---

## 5. Verify the schedule

```bash
curl -H "X-API-Token: <token>" http://localhost:8000/api/schedule
```

`next_start` should read 09:15 on the next weekday that is not an NSE holiday.
The container's clock must be IST — confirm with:

```bash
sudo docker compose -f deploy/docker-compose.yml exec meridian date
```

---

## Without Docker

```bash
sudo adduser --system --group meridian
sudo git clone https://github.com/sehejsharm/meridiancapital.git /opt/meridiancapital
cd /opt/meridiancapital
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r backend/requirements.txt
sudo mkdir -p data && sudo chown -R meridian:meridian /opt/meridiancapital
sudo cp .env.example .env && sudo nano .env
sudo cp deploy/meridian.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now meridian
sudo journalctl -u meridian -f
```

---

## Operations

**Where the money lives.** `equity_book.json` inside the `meridian-data`
volume is the compounding capital. It is never reset by a redeploy. Back it up:

```bash
sudo docker run --rm -v meridian-data:/data -v "$PWD:/backup" alpine \
  tar czf /backup/meridian-$(date +%F).tar.gz -C /data .
```

Do this weekly. A lost volume means a lost equity curve.

**Updating.**

```bash
cd /opt/meridiancapital && git pull
sudo docker compose -f deploy/docker-compose.yml up -d --build
```

Safe outside 09:15–15:45; during the session it stops the bot mid-flight. If
you must, hit Stop in the app first so any open position is flattened properly.

**Logs.**

```bash
sudo docker compose -f deploy/docker-compose.yml logs -f --tail 200
```

**Going live with real money.** Set `PAPER_MODE=false` in `.env` and restart.
Read the warning in the root README first — the app turns red and says
LIVE MONEY when this is set, which is the only guardrail between simulation
and the exchange.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Missing credentials` on start | `.env` incomplete | Fill the five `ANGEL_*` values, restart |
| Login rejected | Wrong TOTP secret, or clock drift | Re-copy the base32 seed; `sudo timedatectl set-ntp true` |
| Bot starts at the wrong hour | Container not on IST | `docker compose exec meridian date`; rebuild if wrong |
| App connects but no live feed | WebSocket blocked by a proxy | Use the Caddy profile; it upgrades correctly |
| `not a trading day` on a working day | Wrong entry in `holidays.py` | Remove the date, or Start anyway from Control |
| Bot restarts repeatedly | Angel One rejecting the IP | Whitelist the server IP in SmartAPI |
| Equity reset to seed | Volume lost on redeploy | Restore from backup; check the volume is mounted |
