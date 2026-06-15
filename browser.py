"""Shared browser helpers (patchright / stealth Chrome) used by both the
setup helper and the main watcher."""
import logging
import os
import time

import requests

import config
import settings

log = logging.getLogger("browser")


def is_online() -> bool:
    """Quick internet check (also confirms Telegram is reachable)."""
    for url in ("https://api.telegram.org", "https://www.google.com/generate_204"):
        try:
            requests.head(url, timeout=5)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False

# patchright is a drop-in stealth fork of playwright.
from patchright.sync_api import sync_playwright  # noqa: E402

LOGIN_HINTS = ("login", "oauth2", "authorization", "signin", "/auth", "auth.",
               "openid-connect", "expired-session", "expired")
CF_TITLE_HINTS = ("just a moment", "attention required", "verifying", "un momento")
CF_TEXT_HINTS = ("verify you are human", "checking your browser", "needs to review the security")


def _clean_profile_locks():
    """Remove stale singleton locks so a crashed/killed run doesn't block relaunch."""
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.remove(os.path.join(config.PROFILE_DIR, name))
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            log.warning("could not remove %s: %s", name, e)


def launch_context(playwright):
    """Launch a persistent, headed, stealth context with our own profile. Returns (context, page).

    Defaults to patchright's bundled Chromium (isolated — never collides with the user's
    everyday Google Chrome on macOS). Set BROWSER_CHANNEL=chrome to use real Chrome instead."""
    _clean_profile_locks()
    kwargs = dict(
        user_data_dir=config.PROFILE_DIR,
        headless=False,              # headed → far fewer Cloudflare challenges
        no_viewport=True,
        args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
    )
    if config.BROWSER_CHANNEL:
        kwargs["channel"] = config.BROWSER_CHANNEL
    context = playwright.chromium.launch_persistent_context(**kwargs)
    page = context.pages[0] if context.pages else context.new_page()
    return context, page


def is_cloudflare(page) -> bool:
    try:
        title = (page.title() or "").lower()
        if any(h in title for h in CF_TITLE_HINTS):
            return True
        body = (page.inner_text("body", timeout=2000) or "").lower()
        return any(h in body for h in CF_TEXT_HINTS)
    except Exception:  # noqa: BLE001
        return False


BLOCK_HINTS = ("you have been blocked", "sorry, you have been blocked",
               "error 1020", "access denied")


def is_blocked(page) -> bool:
    try:
        body = (page.inner_text("body", timeout=2000) or "").lower()
        return any(h in body for h in BLOCK_HINTS)
    except Exception:  # noqa: BLE001
        return False


def is_login_page(page) -> bool:
    url = (page.url or "").lower()
    if any(h in url for h in LOGIN_HINTS):
        return True
    try:
        if page.locator("input[type=password]").count() > 0:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def wait_for_cloudflare(page, notify=None, max_wait=600) -> bool:
    """If a Cloudflare challenge is up, ping the user and wait for them to solve it
    in the open window. Returns True once clear, False on timeout."""
    if not is_cloudflare(page):
        return True
    log.warning("Cloudflare challenge detected.")
    if notify:
        try:
            shot = str(config.SCREENSHOT_DIR / "cloudflare.png")
            page.screenshot(path=shot)
            notify(shot, "⚠️ Cloudflare check — please click it in the open Chrome window on the Mac.")
        except Exception:  # noqa: BLE001
            pass
    deadline = time.time() + max_wait
    while time.time() < deadline:
        time.sleep(5)
        if not is_cloudflare(page):
            log.info("Cloudflare cleared.")
            return True
    log.error("Cloudflare not cleared within %ss.", max_wait)
    return False


# Phrases TLScontact shows when a month has no slots (verified against the live page).
NO_SLOT_PHRASES = (
    "any appointment slots available",
    "no slots are currently available",
    "don’t have any",   # don't (curly apostrophe)
    "don't have any",
)


def _settled_off_login(page, seconds=40) -> bool:
    """After submitting, Keycloak bounces through /auth-callback before landing on
    /travel-groups. Poll until the URL actually settles off any login/auth page."""
    end = time.time() + seconds
    while time.time() < end:
        page.wait_for_timeout(1500)
        url = (page.url or "").lower()
        if "auth-callback" in url:
            continue  # mid-redirect, keep waiting
        if not is_login_page(page):
            return True
    return False


def _submit(page, field_sel):
    """Submit a login step. Enter-in-field is the reliable path; clicking the
    Login button is flaky (overlay/timing), so it's only a fallback."""
    try:
        page.locator(field_sel).first.press("Enter")
        return
    except Exception:  # noqa: BLE001
        pass
    page.locator("button[type=submit]").first.click(timeout=8000)


def do_login(page) -> bool:
    """Real TLScontact (Keycloak) login. Robust: handles already-logged-in, the
    redirect lag, an optional two-step (email then password), Cloudflare, and retries."""
    email_val, pwd_val = settings.get_email(), settings.get_password()
    if not (email_val and pwd_val):
        return False
    email_sel = "#email-input-field, input[name=username], input[type=email]"
    pwd_sel = "#password-input-field, input[type=password]"

    for attempt in range(3):
        try:
            page.goto(config.LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)
            wait_for_cloudflare(page)

            # Already authenticated? LOGIN_URL redirects straight to the app.
            if _settled_off_login(page, seconds=6):
                return True

            page.wait_for_selector(email_sel, timeout=25000)
            page.locator(email_sel).first.fill(email_val)
            page.wait_for_timeout(400)

            # Password on the same page, or a two-step flow (submit email first).
            if page.locator(pwd_sel).count() == 0:
                _submit(page, email_sel)
                page.wait_for_selector(pwd_sel, timeout=20000)
            page.locator(pwd_sel).first.fill(pwd_val)
            page.wait_for_timeout(500)
            _submit(page, pwd_sel)

            if _settled_off_login(page, seconds=40):
                log.info("Login succeeded (attempt %d).", attempt + 1)
                return True
            log.warning("Login attempt %d: still on login after submit.", attempt + 1)
        except Exception as e:  # noqa: BLE001
            log.warning("Login attempt %d error: %s", attempt + 1, e)
            page.wait_for_timeout(2000)
    return False


def has_appointment_tab(page) -> bool:
    """True when we're on the application workflow (the 'Appointment booking' step tab
    is present). Absent → we got bounced to home / logged out."""
    try:
        return page.locator('[data-testid="appointment-booking"]').count() > 0
    except Exception:  # noqa: BLE001
        return False


def open_calendar(page) -> bool:
    """From a workflow page, click the 'Appointment booking' tab (client-side nav →
    avoids the Cloudflare block on the deep URL). Returns True when the calendar shows."""
    try:
        tab = page.locator('[data-testid="appointment-booking"]').first
        if tab.count() == 0:
            return False
        try:
            tab.scroll_into_view_if_needed(timeout=3000)
        except Exception:  # noqa: BLE001
            pass
        try:
            tab.click(timeout=10000)
        except Exception:  # noqa: BLE001
            # JS click fallback in case something overlaps it
            page.evaluate("() => { const e = document.querySelector"
                          "('[data-testid=\"appointment-booking\"]'); if (e) e.click(); }")
        page.wait_for_selector('[data-testid*="month"]', timeout=25000)
        page.wait_for_timeout(2000)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("open_calendar failed: %s", e)
        return False


def _month_label(page) -> str:
    try:
        el = page.locator('[data-testid="btn-current-month-available"], '
                          '[data-testid*="current-month"]').first
        if el.count():
            return (el.inner_text(timeout=2000) or "").strip().splitlines()[0][:40]
    except Exception:  # noqa: BLE001
        pass
    return "month"


def _month_has_no_slots(page) -> bool:
    try:
        body = (page.inner_text("body", timeout=5000) or "").lower()
    except Exception:  # noqa: BLE001
        return True  # can't read → assume none, don't false-alarm
    extra = tuple(config.NO_SLOT_TEXT) if config.NO_SLOT_TEXT else ()
    return any(p in body for p in NO_SLOT_PHRASES + extra)


def check_all_months(page, on_slots=None, on_month=None):
    """Traverse current + every reachable month (clickable prev/next tabs), checking each.
    Calls on_slots(label) when slots are found, and on_month(label, has_slots) for every
    month while it's on screen (e.g. to screenshot it). Returns (found, info)."""
    seen = set()
    parts = []
    found = False

    def check_one():
        nonlocal found
        page.wait_for_timeout(1500)
        label = _month_label(page)
        if label in seen:
            return
        seen.add(label)
        none = _month_has_no_slots(page)
        if not none:
            # confirm it wasn't a mid-load blip
            page.wait_for_timeout(1800)
            none = _month_has_no_slots(page)
        parts.append(f"{label}: {'none' if none else 'SLOTS ✶'}")
        if on_month:
            try:
                on_month(label, not none)   # called while this month is on screen
            except Exception:  # noqa: BLE001
                pass
        if not none:
            found = True
            if on_slots:
                try:
                    on_slots(label)
                except Exception:  # noqa: BLE001
                    pass

    check_one()
    # Walk FORWARD only — current month then the upcoming ones (past months are
    # disabled and irrelevant). Stop early once slots are found (we want to grab it).
    for _ in range(4):
        if found:
            break
        btn = page.locator('button[data-testid^="btn-next-month"]').first
        try:
            if btn.count() and btn.is_enabled():
                btn.click(timeout=5000)
                check_one()
                continue
        except Exception:  # noqa: BLE001
            pass
        break
    return found, " | ".join(parts)


def attempt_booking(page):
    """Best-effort: select the first available slot, then click 'Book your appointment'.
    Never raises — returns (clicked_slot, booked, info). Always called AFTER the alert."""
    info = []
    clicked = False
    for sel in config.SLOT_SELECTORS:
        try:
            loc = page.locator(sel)
            n = min(loc.count(), 6)
            for i in range(n):
                el = loc.nth(i)
                try:
                    if el.is_visible() and el.is_enabled():
                        el.click(timeout=4000)
                        clicked = True
                        info.append(f"selected slot via `{sel}`")
                        break
                except Exception:  # noqa: BLE001
                    continue
            if clicked:
                break
        except Exception:  # noqa: BLE001
            continue
    if not clicked:
        info.append("no slot element matched (selectors need tuning)")

    page.wait_for_timeout(1500)

    booked = False
    try:
        book = page.locator(f'button:has-text("{config.BOOK_BUTTON_TEXT}")').first
        if book.count() and book.is_enabled():
            book.click(timeout=6000)
            page.wait_for_timeout(3500)
            booked = True
            info.append("clicked 'Book your appointment'")
        else:
            info.append("Book button not enabled (no slot selected yet)")
    except Exception as e:  # noqa: BLE001
        info.append(f"book click failed: {e}")
    return clicked, booked, "; ".join(info)
