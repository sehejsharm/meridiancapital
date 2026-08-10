# Oracle Cloud setup, from zero

Complete walkthrough for someone who has never used Oracle Cloud before.
Every step, in order, nothing assumed. Takes about 20 minutes plus however
long Oracle's identity verification takes.

By the end you will have a server running the trading bot, reachable from
your phone over HTTPS, for **₹0/month forever**.

---

## 1. Create the Oracle Cloud account

1. Go to **[cloud.oracle.com](https://cloud.oracle.com)** and click **Start
   for free**.
2. Enter your email, verify it (check your inbox for a code).
3. Fill in your name, address, and phone number. Verify the phone number by
   SMS code.
4. **Pick your Home Region carefully — this cannot be changed later.**
   Choose **India West (Mumbai)**. If Mumbai is not offered, pick
   **India South (Hyderabad)**. This determines the network latency to Angel
   One, so it matters.
5. Enter a credit or debit card. **This is identity verification only.**
   Oracle's Always Free resources are never charged — a small refundable
   authorization (usually ₹2) may appear and reverse itself. Do not worry
   about this step causing charges.
6. Wait for the account to provision. This can take a few minutes to (rarely)
   a few hours. You'll get an email when it's ready.
7. Sign in at **cloud.oracle.com** with the credentials you just set.

---

## 2. Create the virtual machine

Once signed in, you land on the **OCI Console**.

1. Click the **hamburger menu** (☰) top-left → **Compute** → **Instances**.
2. Click the blue **Create Instance** button.
3. **Name**: type `meridian` (or anything).
4. **Placement**: leave as default (your home region, an available domain).
5. **Image and shape** — click **Edit**:
   - Click **Change Image**. Either **Ubuntu 24.04** or the default **Oracle
     Linux 9** works — the setup script handles both. Note which one you pick,
     because it decides your SSH username in step 3: Ubuntu logs in as
     `ubuntu`, Oracle Linux as `opc`. Click **Select Image**.
   - Click **Change Shape**. Under **Instance type**, choose **Virtual
     Machine**. Under **Shape series**, choose **AMD**. Select
     **VM.Standard.E2.1.Micro**.

     **Do not pick the ARM shape (VM.Standard.A1.Flex).** It is also free but
     is almost always out of capacity in Indian regions, and you'll get
     "Out of host capacity" errors for hours. The AMD Micro shape is smaller
     (1 GB RAM) but is reliably available and is enough for this bot — the
     setup script adds swap to make 1 GB comfortable.
   - Confirm it says **"Always Free eligible"** next to the shape. If it
     doesn't, you picked the wrong one — go back and re-select.
6. **Networking**: leave the defaults (it creates a new VCN and subnet
   automatically). Make sure **"Assign a public IPv4 address"** is checked
   (it is, by default).
7. **Add SSH keys** — this is how you'll log in. You have two options:
   - **Easiest**: leave "Generate a key pair for me" selected, then click
     **Save Private Key** and **Save Public Key** — both download to your
     computer. Keep the private key file (`ssh-key-...key`) safe; it's the
     only way to log in.
   - **If you already have a key pair**: choose "Upload public key file" and
     upload your `.pub` file.
8. Leave **Boot volume** at its defaults (50 GB is plenty).
9. Click the blue **Create** button at the bottom.

Wait 1–2 minutes. The instance will show a status of **PROVISIONING** then
**RUNNING**. Once it says RUNNING, click into the instance and copy its
**Public IP address** from the instance details page — you'll need it next.

> **If you get "Out of host capacity"** even on the AMD shape (rare, but
> possible): try a different Availability Domain (there's a dropdown under
> Placement), or try again in a few minutes. AMD capacity issues are much
> rarer than ARM ones.

---

## 3. Connect over SSH

**If you're on macOS or Linux:**

Open Terminal. Move the downloaded private key somewhere sensible and fix its
permissions (SSH refuses to use a key that's readable by anyone else):

```bash
mkdir -p ~/.ssh
mv ~/Downloads/ssh-key-*.key ~/.ssh/meridian.key
chmod 600 ~/.ssh/meridian.key
```

Connect. Replace `<PUBLIC_IP>` with the address you copied, and use the
username that matches the image you chose in step 2 — **`ubuntu`** for Ubuntu,
**`opc`** for Oracle Linux:

```bash
ssh -i ~/.ssh/meridian.key opc@<PUBLIC_IP>
```

Type `yes` if asked about the host's fingerprint — that's expected the first
time.

**If you're on Windows:**

Use **PowerShell** (comes with Windows 10/11):

```powershell
ssh -i "$env:USERPROFILE\Downloads\ssh-key-....key" opc@<PUBLIC_IP>
```

Or use **PuTTY** if you prefer a GUI: convert the `.key` file to `.ppk` using
PuTTYgen first (PuTTY's own guide covers this), then connect with PuTTY using
the same username.

You should land on a prompt like `opc@meridian:~$`. You're in.

---

## 4. Run the setup script

Copy-paste this whole block. It installs Docker, clones the repository,
generates a login token, and (because this is a 1 GB box) sets up swap so it
doesn't run out of memory:

```bash
sudo REPO_URL=https://github.com/sehejsharm/meridiancapital.git bash \
  <(curl -fsSL https://raw.githubusercontent.com/sehejsharm/meridiancapital/main/deploy/setup.sh)
```

It will finish by telling you it created a `.env` file and stop, printing
something like:

```
Your API token (put this into the phone app):
  AbCdEf123...

Now edit .env and fill in the five ANGEL_* values, then re-run this script.
```

---

## 5. Fill in your credentials

```bash
sudo nano /opt/meridiancapital/.env
```

`nano` is a simple text editor. Use the arrow keys to move around. Find and
fill in these lines (they're near the top and bottom of the file):

```ini
ADMIN_USER=Sehej
ADMIN_PASSWORD=your-chosen-passcode

ANGEL_API_KEY=...
ANGEL_SECRET_KEY=...
ANGEL_CLIENT_ID=...
ANGEL_PASSWORD=...
ANGEL_TOTP_SECRET=...
```

The five `ANGEL_*` values come from your Angel One SmartAPI developer
dashboard. `ADMIN_USER` / `ADMIN_PASSWORD` are what you'll type into the app
to log in — pick your own.

When done editing: press **Ctrl+O** (save), then **Enter** to confirm, then
**Ctrl+X** (exit).

Now run the exact same command from Step 4 again — it detects `.env` is
filled in and proceeds to build and start:

```bash
sudo REPO_URL=https://github.com/sehejsharm/meridiancapital.git bash \
  <(curl -fsSL https://raw.githubusercontent.com/sehejsharm/meridiancapital/main/deploy/setup.sh)
```

This takes a few minutes the first time. When it succeeds you'll see:

```
Running.
  Local health check:  http://localhost:8000/api/health
```

The bot is now live on the box, but only reachable from the box itself. The
next step gives it an address you can open from anywhere.

---

## 6. Give it an HTTPS address (Tailscale)

Your dashboard runs on HTTPS (via Vercel), and a browser will refuse to let
an HTTPS page talk to a plain HTTP server. Tailscale Funnel solves this for
free, with no domain name needed.

> **You do not need to open ports 80 and 443** — not in the VCN security list,
> and not in the OS firewall. Tailscale dials *out* from the box and tunnels
> replies back, so there is no inbound port to forward. Leaving the security
> list closed is both less work and the safer configuration: your API token
> never sits behind a bare HTTP port on the open internet. Skip the firewall
> steps entirely and come straight here.

Still connected over SSH, run:

```bash
curl -fsSL https://tailscale.com/install.sh | sudo sh
sudo tailscale up
```

This prints a URL like `https://login.tailscale.com/a/xxxxxxxxx`. Copy it,
open it in any browser, and sign in (create a free Tailscale account with
Google/Microsoft/GitHub if you don't have one — one click, no card).

Back in the terminal, once authorized:

```bash
sudo tailscale funnel --bg 8000
sudo tailscale funnel status
```

The last command prints your permanent address, something like:

```
https://meridian.tail1a2b3c.ts.net
```

**This is your server address.** Write it down.

---

## 7. Connect the app

1. Open your Vercel dashboard URL (or the app) on your phone or laptop.
2. **Server**: paste the `https://….ts.net` address from Step 6.
3. **Operator**: the `ADMIN_USER` you set (e.g. `Sehej`).
4. **Passcode**: the `ADMIN_PASSWORD` you set.
5. Tap **Authenticate**.

You should land on the dashboard. It will offer to enable Face ID / Touch ID
on that device — accept it so you don't have to type the passcode every time.

---

## 8. Verify it's actually correct

Back in the SSH session:

```bash
date
```

Must show the correct IST time and today's date. If it's wrong, the schedule
will be wrong too — see Troubleshooting below.

```bash
curl -H "X-API-Token: <the token from Step 4>" http://localhost:8000/api/schedule
```

`next_start` should show **09:15** on the next weekday.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Permission denied (publickey)` on SSH | Wrong key file, or wrong username for the image you chose: `opc` on Oracle Linux, `ubuntu` on Ubuntu. Never `root`. |
| SSH just hangs, then `Connection timed out` | Almost always the wrong public IP. Re-copy it from the instance page in the console — it must be an Oracle-owned address, and for Mumbai that means something like `140.238.x.x`, `152.67.x.x` or `168.138.x.x`. A `92.x.x.x` address is not Oracle's; that is your own ISP's. |
| `Out of host capacity` creating the instance | You likely picked the ARM shape by mistake — go back and confirm it says **VM.Standard.E2.1.Micro** and **AMD**. |
| Card declined during signup | Try a different card, or a virtual/prepaid card. Some banks block the small verification hold — call them if it keeps failing. |
| `docker: command not found` after setup | The setup script failed partway — scroll up in the terminal output for the actual error, or re-run it. |
| `date` shows the wrong time | `sudo timedatectl set-ntp true` then wait 30 seconds and check again. |
| App says "cannot reach server" | Confirm `sudo tailscale funnel status` still shows the address as active. Confirm you're using `https://`, not `http://`. |
| Forgot the passcode | SSH in, `sudo nano /opt/meridiancapital/.env`, change `ADMIN_PASSWORD`, then `cd /opt/meridiancapital && sudo docker compose -f deploy/docker-compose.yml restart`. |

---

## What you now have

- A server in Mumbai (or Hyderabad), running for free, forever
- The trading bot starting itself at 09:15 IST and stopping at 15:45, every
  trading day, with nothing connected to it
- A permanent HTTPS address reachable from anywhere
- Nothing exposed to the open internet — Tailscale makes an outbound
  connection only, there's no inbound port forward
