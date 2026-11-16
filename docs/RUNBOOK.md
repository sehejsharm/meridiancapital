# Runbook

What to do when something is wrong, in the order to do it. Keep this page open on a
second device during the session. Nothing here needs a code change.

**The rule that comes first:** if a position is open and you are unsure what the
system is doing, close the position in the Angel One app yourself. The app is always
the last word; the dashboard and the program are conveniences on top of it.

## 1. The dashboard will not load, or the VM is unreachable, and a position is open

1. Open the **Angel One app** → Positions. If the position is there, square it off in
   the app. Do not wait for the VM.
2. In the app's order book, cancel any stop order still resting for that contract, so
   it cannot sell again after you have sold.
3. Only then look at the VM: `ssh` in and run `sudo bash ~/meridiancapital/backend/deploy/diagnose.sh`.
4. If the API is down but the VM is up: `sudo systemctl restart meridian-api`. A running
   program is a separate process and is adopted again when the API comes back.
5. Do not start the program again that day until you know why it stopped.

## 2. Angel's API is failing (timeouts, rate limits, "upstream" errors)

1. Look at Reliability → Angel API errors. A handful is normal; a wall of them is not.
2. If a position is open, watch it in the Angel app, not on the dashboard.
3. If the program logs "EXIT COULD NOT BE PLACED" or "IS STILL OPEN", sell in the app
   at once, then press Stop on the dashboard so the program does not try again.
4. With no position open: press Stop and leave it stopped until the errors clear.

## 3. The Angel app and the dashboard disagree

The app is right. Then:

1. Reliability → "Run the after-close check now". It compares the desk's records with
   Angel's position book and trade book (it asks Angel only when nothing is running).
2. A position in the app that the dashboard does not show: square it off in the app.
3. A trade on the dashboard that Angel does not have: do nothing in the market; note
   the time and send the journal for that minute.

## 4. Updating the server

After 15:30 IST only, when nothing is running:

    cd ~/meridiancapital && git pull && sudo bash backend/deploy/install.sh

The installer refuses while the market is open or an algorithm is running. `--force`
overrides that; use it only when the alternative is worse.

## 5. Restoring the database

1. Stop the API: `sudo systemctl stop meridian-api` (nothing must be running).
2. Pick a backup in `/var/lib/meridian/backups/` (or fetch the off-machine copy and
   decrypt it: `openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in FILE.gz.enc -out FILE.gz -pass env:MERIDIAN_BACKUP_PASSPHRASE`).
3. `gunzip -k FILE.gz`, then copy it over `/var/lib/meridian/meridian.db`, owned by
   `meridian:meridian`.
4. `sudo systemctl start meridian-api` and check the blotter's last trade.

`sudo bash backend/deploy/restore-test.sh` proves a backup can be restored without
touching the live database; it runs by itself on the 1st of each month.

## 6. The monthly drill (on paper, ten minutes)

On the first trading day of the month, with the program on **paper**:

1. Start it; wait for a paper position (or the first status).
2. Press **Halt new entries**; confirm the journal says so.
3. Press **Flatten**; confirm the position closes and the journal shows the sale.
4. Press **Stop everything**; confirm the card says stopped and nothing restarts.
5. Reliability → send a test alert; confirm it reaches your phone.
6. Write the date and anything that surprised you in your own notes.

If any step does not do what it says, do not trade real money until it does.

## 7. A new program version

Three full paper sessions before real money. The algorithm's page shows the count for
the active version, and the switch to real money warns when it is short.

## 7. The quarterly study (read-only, two minutes)

After each calendar quarter, on the server:

```bash
cd ~/meridiancapital && sudo -u meridian /opt/meridian/backend/.venv/bin/python backend/scripts/quarterly_study.py
```

It prints the quarter's real-money results, each exit rule set on the trades with a recorded price path, what the
fills cost, and whether the results are inside the range the backtest allows. It changes nothing. Two dates
(`2026-07-01 2026-09-30`) choose another period; `--paper` reads the paper trades. A result below the band is a reason to
stop adding size and look, not a reason to change the program by itself: a change still needs a backtest.

## 8. Notifications on the phone

Reliability → Alerts → "Notifications from the desk itself" → **Turn on on this device**, then **Send a test**.
On an iPhone, first add the dashboard to the home screen (Share → Add to Home Screen) and open it from there; Apple does
not deliver notifications to a website in a Safari tab. Telegram and the webhook keep working beside it. If a test does
not arrive: turn it off and on again on the phone, and check the phone's own notification settings for the app.

With no network the dashboard shows an offline page with the last figures the phone saw, marked old. It never shows a
stored page as if it were live. The trading program does not depend on the phone.

## 9. A program without credentials in its file (v14 and later)

From v14 the program file holds no Angel credentials. Before uploading one, the server must have all four lines in
`/etc/meridian/meridian.env`, then `sudo systemctl restart meridian-api`:

```
ANGEL_API_KEY=...
ANGEL_CLIENT_ID=...
ANGEL_PASSWORD=...
ANGEL_TOTP_SECRET=...
```

Reliability → Server shows whether they are set. With any missing, a v14 program stops at start-up and names what is
missing; it places no order. Keep the previous version until one paper session has logged in and run.

## 10. Exit settings (v14 and later)

Controls → Exit settings shows the seven exit figures the program starts with. They can be changed only while the
program is stopped, only inside the ranges shown, with the phrase typed; the change is in force from the next start and
is written to the journal and the audit log. **Back to built-in** removes every change. Only the built-in values have a
backtest behind them. If the program finds the file damaged it ignores all of it, uses the built-in values and says so
in red at start-up.
