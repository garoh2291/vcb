"""Watcher core: the browser loop, shared state, and internet-resilience.

run_watcher(state, stop_event) runs until stop_event is set. The server (or the
CLI) owns that event so the bot can be started/stopped on demand.
"""
import logging
import random
import threading
import time
from collections import deque

import browser
import config
import telegram

log = logging.getLogger("watcher")


def _now():
    return time.time()


class WatcherState:
    """Thread-safe snapshot of what the bot is doing, for the dashboard."""

    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.online = True
        self.started_at = None
        self.last_check = None
        self.last_result = "—"
        self.found = False
        self.cycles = 0
        self.last_alert = None
        self.last_error = None
        self.last_screenshot = None
        self.month_shots = []   # [{"label": "June 2026", "path": "...png"}]
        self._log = deque(maxlen=300)
        # On-demand control (set by the Telegram poller, consumed by the watcher thread).
        self._wake = threading.Event()
        self._send_shots = False
        self._force_relogin = False

    def request_check(self, send_shots=False):
        """Ask the watcher to run a check immediately (interrupts its sleep)."""
        with self._lock:
            if send_shots:
                self._send_shots = True
        self._wake.set()

    def request_relogin(self):
        """Force a fresh login next cycle (e.g. after credentials change)."""
        with self._lock:
            self._force_relogin = True
        self._wake.set()

    def take_send_shots(self) -> bool:
        with self._lock:
            v = self._send_shots
            self._send_shots = False
        return v

    def take_force_relogin(self) -> bool:
        with self._lock:
            v = self._force_relogin
            self._force_relogin = False
        return v

    def event(self, msg, level="info"):
        getattr(log, level, log.info)(msg)
        with self._lock:
            self._log.appendleft({"t": _now(), "msg": msg, "level": level})

    def set(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def snapshot(self):
        with self._lock:
            return {
                "running": self.running,
                "online": self.online,
                "started_at": self.started_at,
                "last_check": self.last_check,
                "last_result": self.last_result,
                "found": self.found,
                "cycles": self.cycles,
                "last_alert": self.last_alert,
                "last_error": self.last_error,
                "has_screenshot": bool(self.last_screenshot),
                "months": [{"label": m["label"], "index": i,
                            "has_slots": bool(m.get("has_slots"))}
                           for i, m in enumerate(self.month_shots) if m.get("path")],
                "log": list(self._log)[:80],
                "server_time": _now(),
            }


def _sleep(seconds, stop_event, wake=None):
    """Interruptible sleep — returns early if stop is requested or a run-now wake fires."""
    end = _now() + seconds
    while _now() < end:
        if stop_event.is_set():
            return
        if wake is not None and wake.wait(timeout=min(1.0, end - _now())):
            wake.clear()
            return
        if wake is None:
            time.sleep(min(1.0, end - _now()))


def _screenshot(page, name):
    """Fast viewport screenshot. Avoid full_page=True — on this SPA it stitches the
    whole tall page and times out. The calendar sits at the top, so viewport is enough."""
    path = str(config.SCREENSHOT_DIR / name)
    try:
        if config.CALENDAR_SELECTOR:
            el = page.locator(config.CALENDAR_SELECTOR).first
            if el.count() > 0:
                el.screenshot(path=path, timeout=15000)
                return path
        page.screenshot(path=path, timeout=15000)
        return path
    except Exception as e:  # noqa: BLE001
        log.warning("screenshot failed: %s", e)
        return None


def _load_workflow(page):
    """Load the normal (non-blocked) workflow page and clear any Cloudflare check."""
    page.goto(config.WORKFLOW_ENTRY_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    return browser.wait_for_cloudflare(page, notify=telegram.send_photo)


def _reach_calendar(page, state):
    """Get to the 3-month calendar via the click-through path (the deep URL is
    Cloudflare-blocked). Re-logs in if the session expired. Returns True on success."""
    if not _load_workflow(page):
        state.event("Cloudflare not cleared.", "warning")
        return False

    # Logged out? Either an explicit login/expired page, OR we got bounced to the home
    # page (no 'Appointment booking' tab present). Both mean: log in, then reload.
    if browser.is_login_page(page) or not browser.has_appointment_tab(page):
        state.event("Session expired / bounced to home — logging in.")
        if not browser.do_login(page):
            telegram.send_message(
                "🔑 Auto-login failed. Log in manually in the browser window on the Mac; "
                "I'll resume automatically."
            )
            end = _now() + 600
            while _now() < end:
                time.sleep(10)
                if not browser.is_login_page(page):
                    break
            else:
                return False
        else:
            telegram.send_message("🔐 Re-logged in.")
        if not _load_workflow(page):
            return False
        if not browser.has_appointment_tab(page):
            state.event("Still not on the workflow page after login.", "warning")
            return False

    # Open the calendar by clicking the 'Appointment booking' tab.
    if not browser.open_calendar(page):
        state.event("Could not open the Appointment-booking tab.", "warning")
        return False
    return True


def run_watcher(state, stop_event):
    """Main loop. Keeps the browser warm; survives offline periods and crashes."""
    state.set(running=True, started_at=_now())
    state.event(f"Watcher started — checking every {config.CHECK_INTERVAL // 60} min "
                f"across {config.MONTHS_TO_CHECK} months.")
    telegram.send_message(
        f"✅ Visa watcher online. Checking every {config.CHECK_INTERVAL // 60} min "
        f"({config.MONTHS_TO_CHECK} months)."
    )
    last_heartbeat = _now()
    was_online = True

    try:
        while not stop_event.is_set():
            try:
                with browser.sync_playwright() as p:
                    context, page = browser.launch_context(p)
                    try:
                        while not stop_event.is_set():
                            # --- connectivity gate ---
                            if not browser.is_online():
                                if was_online:
                                    was_online = False
                                    state.set(online=False)
                                    state.event("Internet lost — pausing.", "warning")
                                _sleep(20, stop_event)
                                continue
                            if not was_online:
                                was_online = True
                                state.set(online=True)
                                state.event("Internet back — resuming.")
                                telegram.send_message("🟢 Back online — watcher resumed.")
                                last_heartbeat = _now()

                            # --- one check cycle ---
                            cycle_start = _now()
                            send_shots = state.take_send_shots()  # on-demand /shots request
                            if state.take_force_relogin():
                                # Credentials changed → drop the session so we log in fresh.
                                try:
                                    context.clear_cookies()
                                    state.event("Credentials changed — forcing re-login.")
                                except Exception:  # noqa: BLE001
                                    pass
                            if _reach_calendar(page, state):
                                month_shots = []

                                # One screenshot per month, taken while it's on screen.
                                def on_month(label, has_slots):
                                    idx = len(month_shots)
                                    path = _screenshot(page, f"month_{idx}.png")
                                    month_shots.append({"label": label, "path": path,
                                                        "has_slots": has_slots})

                                # Alert immediately on the exact month that has slots,
                                # THEN best-effort auto-book (never blocks the alert).
                                def on_slots(label):
                                    shot = _screenshot(page, f"slot_{int(_now())}.png")
                                    telegram.send_photo(
                                        shot or "",
                                        f"🎉 Appointment slots available — {label}!\n"
                                        f"Open the booking page now.",
                                    )
                                    state.set(last_alert=_now(), last_screenshot=shot)
                                    if not config.AUTO_BOOK:
                                        return
                                    try:
                                        clicked, booked, binfo = browser.attempt_booking(page)
                                        shot2 = _screenshot(page, f"book_{int(_now())}.png")
                                        if booked:
                                            msg = ("✅ Auto-book: clicked 'Book your appointment'! "
                                                   "VERIFY and complete payment NOW.")
                                        elif clicked:
                                            msg = ("⚠️ Selected a slot but couldn't confirm the Book "
                                                   "button — finish booking MANUALLY now.")
                                        else:
                                            msg = ("⚠️ Couldn't auto-select a slot — book MANUALLY "
                                                   "now (slot detected!).")
                                        telegram.send_photo(shot2 or shot, f"{msg}\n{binfo}")
                                        state.event(f"Auto-book: clicked={clicked} booked={booked} "
                                                    f"({binfo})")
                                    except Exception as e:  # noqa: BLE001
                                        telegram.send_message(
                                            f"⚠️ Auto-book error — book MANUALLY now: {e}")
                                        state.event(f"Auto-book error: {e}", "error")

                                found, info = browser.check_all_months(
                                    page, on_slots=on_slots, on_month=on_month)
                                shot = _screenshot(page, "latest.png")
                                state.set(last_check=_now(), last_result=info,
                                          found=found, month_shots=month_shots,
                                          last_screenshot=(state.last_screenshot if found else shot),
                                          cycles=state.cycles + 1, last_error=None)
                                state.event(f"Check: {'FOUND ✶' if found else 'none'} — {info}")
                                if found:
                                    last_heartbeat = _now()
                                # On-demand /shots: send each month screenshot now.
                                if send_shots:
                                    telegram.send_message(f"📸 On-demand check — {info}")
                                    for m in month_shots:
                                        if m.get("path"):
                                            telegram.send_photo(m["path"], m["label"])
                            else:
                                state.set(last_check=_now(),
                                          last_result="navigation/login issue",
                                          cycles=state.cycles + 1)
                                if send_shots:
                                    telegram.send_message(
                                        "⚠️ On-demand check: couldn't reach the calendar "
                                        "(login/navigation). Will retry next cycle.")

                            # --- heartbeat ---
                            if _now() - last_heartbeat >= config.HEARTBEAT_INTERVAL:
                                telegram.send_message("🔄 No new appointments.")
                                last_heartbeat = _now()

                            # Randomized cadence anchored to the cycle START. Each gap varies
                            # (±25%) so the pattern isn't clockwork — gentler on Cloudflare —
                            # while still averaging ~CHECK_INTERVAL regardless of check duration.
                            elapsed = _now() - cycle_start
                            target = config.CHECK_INTERVAL * random.uniform(0.85, 1.25)
                            _sleep(max(20.0, target - elapsed), stop_event, wake=state._wake)
                    finally:
                        try:
                            context.close()
                        except Exception:  # noqa: BLE001
                            pass
            except Exception as e:  # noqa: BLE001
                log.exception("watcher cycle crashed")
                state.set(last_error=str(e))
                state.event(f"Error: {e} — relaunching browser in 30s.", "error")
                if browser.is_online():
                    telegram.send_message(f"⚠️ Watcher error: {e}\nRelaunching browser.")
                _sleep(30, stop_event)
    finally:
        state.set(running=False)
        state.event("Watcher stopped.")
        telegram.send_message("🛑 Visa watcher stopped.")
