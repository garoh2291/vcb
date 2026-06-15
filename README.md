# TLScontact Visa Slot Watcher

Watches the TLScontact appointment calendar (`visas-it.tlscontact.com`, Armenia/Yerevan)
and pings your Telegram when a slot appears.

- Checks the 3-month calendar **every 5 min**.
- Slot appears → **screenshot + Telegram alert**.
- Every 30 min with nothing → **"No new appointments"** heartbeat.
- Session expired / redirected to home → **auto re-login** (manual fallback ping).
- Drives **real Chrome with a saved profile** so Cloudflare rarely triggers. If it does,
  you get a Telegram ping → click it once in the open window → bot continues.

> **Honest note:** no script bypasses Cloudflare 100%. This keeps a warm real-browser
> session to make challenges rare, and asks you to solve the occasional one by hand.
> First login is manual by design.

## Setup

> The bot uses an **isolated bundled Chromium** ("Google Chrome for Testing"), not your
> everyday Google Chrome — so it never collides with your normal browsing. That window
> opening is expected.
>
> **Run only ONE instance at a time** (either the launchd service OR a manual
> `server.py` — never both, and stop the service before `setup_login.py`). Two instances
> fight over the profile and fail with "database is locked".

```bash
cd /Users/beeweb/Documents/Garnik/visa-it
cp .env.example .env          # then fill in login, password, telegram token + chat id
./install_service.sh          # makes .venv, installs deps (does NOT need to stay running yet)
./uninstall_service.sh        # stop the service so setup_login can use the profile
```

### First login (once, by hand)
```bash
.venv/bin/python capture.py
```
A bundled-Chromium window opens at the home page. Pick country, **log in**, and click
through to your application — this saves your session into `chrome_profile/`. Auto-login
is wired (Keycloak), so the bot re-logs in by itself when the session later expires; this
manual run just primes the profile.

`APPOINTMENT_URL` in `.env` only needs to be your real booking URL once — the bot reads
`GROUP_ID` and `LOCATION` from it (e.g. `.../appointment-booking/amEVN2it/2058661`).

### How navigation works (verified against the live site)
- The deep booking URL is Cloudflare-blocked on direct load, so the bot loads a normal
  workflow page (`/en-us/<GROUP_ID>/workflow/...`) and **clicks the "Appointment booking"
  tab** to open the calendar (client-side nav, no block).
- It walks **every reachable month** (current + upcoming, via the prev/next month tabs) and
  flags a month as available when the *"We currently don't have any appointment slots
  available"* message is absent. Slot detection is locked to the real DOM — no tuning needed.

## Run

Everything runs through the **control server** (`server.py`), which serves a
dashboard and owns the watcher so you can start/stop it.

```bash
.venv/bin/python server.py
```
Then open **http://127.0.0.1:3025** — start/stop buttons, running/online status,
live log, last calendar screenshot. The watcher auto-starts on launch
(`AUTOSTART=true`); press ■ stop / ▶ start anytime.

### As a background service (auto-start on login, survives reboot)
`install_service.sh` already points launchd at `server.py`:
```bash
tail -f bot.log                       # live logs
launchctl list | grep visawatcher     # status
./uninstall_service.sh                # stop + remove
```

### Resilience
- **No internet / laptop closed:** the watcher pauses, the dashboard shows
  `OFFLINE`, and nothing crashes. When internet returns it resumes and sends
  `🟢 Back online` to Telegram. launchd restarts the whole thing after a reboot.
- **3 months:** each cycle checks the current month + next 2 by clicking the
  calendar's "next" control (`NEXT_MONTH_SELECTORS`), or reads all 3 at once if
  the page shows them together. Controlled by `MONTHS_TO_CHECK`.

### Watcher only, no dashboard
```bash
.venv/bin/python tls_bot.py
```

## Control from Telegram
The bot also takes commands in the chat (long-polling, no public URL needed). On start it
registers a "/" command menu and shows a **tappable button keyboard** under the input box —
just tap, no typing:

| Command | Button | Action |
|---|---|---|
| `/start` | ▶ Start | start the watcher |
| `/stop` | ■ Stop | stop the watcher |
| `/run` | 🔄 Run now | check immediately (interrupts the sleep) |
| `/shots` | 📸 Run + shots | check now **and** send the 3 month screenshots |
| `/status` | 📊 Status | running/online/cycles/last result |

Only your `TELEGRAM_CHAT_ID` is accepted — commands from anyone else are ignored.

## Files
- `server.py` — dashboard + control panel (port 3025), start/stop, status API, Telegram poller.
- `watcher.py` — the loop: 3-month checks, internet-resilience, shared state.
- `browser.py` — Chrome launch, Cloudflare/login handling, multi-month slot detection.
- `setup_login.py` — one-time manual login + HTML dump.
- `tls_bot.py` — run the watcher standalone (no UI).
- `telegram.py` — Telegram alerts.
- `config.py` / `.env` — settings + secrets.
- `install_service.sh` / `uninstall_service.sh` / `com.garnik.visawatcher.plist` — launchd service.
