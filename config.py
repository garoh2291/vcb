"""Shared config loaded from .env."""
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _split(value: str):
    return [s.strip() for s in (value or "").split(",") if s.strip()]


TLS_LOGIN = os.getenv("TLS_LOGIN", "")
TLS_PASSWORD = os.getenv("TLS_PASSWORD", "")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

HOME_URL = os.getenv("HOME_URL", "https://visas-it.tlscontact.com/en-us")
APPOINTMENT_URL = os.getenv("APPOINTMENT_URL", "")

# Derive the bits we need from the (blocked) deep appointment URL:
#   https://visas-it.tlscontact.com/workflow/appointment-booking/<LOCATION>/<GROUP_ID>
_parts = APPOINTMENT_URL.rstrip("/").split("/") if APPOINTMENT_URL else []
GROUP_ID = os.getenv("GROUP_ID", "").strip() or (_parts[-1] if _parts else "")
LOCATION = os.getenv("LOCATION", "").strip() or (_parts[-2] if len(_parts) >= 2 else "")

_h = urlparse(HOME_URL)
BASE = f"{_h.scheme}://{_h.netloc}"
LOGIN_URL = f"{BASE}/en-us/login"
# A normal (non-blocked) workflow page we can load, then click the "Appointment booking" tab.
WORKFLOW_ENTRY_URL = f"{BASE}/en-us/{GROUP_ID}/workflow/applicants-information" if GROUP_ID else HOME_URL

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "1800"))

PORT = int(os.getenv("PORT", "3025"))
AUTOSTART = os.getenv("AUTOSTART", "true").lower() in ("1", "true", "yes", "on")
MONTHS_TO_CHECK = int(os.getenv("MONTHS_TO_CHECK", "3"))

# Browser channel: blank = patchright's bundled stealth Chromium (isolated, recommended
# on macOS so it never collides with your everyday Google Chrome). Set to "chrome" only
# if you keep your normal Chrome fully quit while the bot runs.
BROWSER_CHANNEL = os.getenv("BROWSER_CHANNEL", "").strip()

# Best-effort auto-book AFTER the Telegram alert is sent (never blocks the alert).
AUTO_BOOK = os.getenv("AUTO_BOOK", "true").lower() in ("1", "true", "yes", "on")
# Candidate selectors for an available slot tile/time (tuned once a real slot appears).
SLOT_SELECTORS = _split(os.getenv(
    "SLOT_SELECTORS",
    '[data-testid*="slot" i],button[class*="slot" i],[class*="slot" i] button,'
    '[class*="timeslot" i],button[class*="time" i],'
    '[class*="available" i]:not([data-testid*="month"])',
))
BOOK_BUTTON_TEXT = os.getenv("BOOK_BUTTON_TEXT", "Book your appointment")

# Resolve profile dir relative to project so launchd (any cwd) still finds it.
_profile = os.getenv("PROFILE_DIR", "./chrome_profile")
PROFILE_DIR = str((BASE_DIR / _profile).resolve()) if _profile.startswith(".") else _profile

AVAILABLE_SELECTORS = _split(os.getenv("AVAILABLE_SELECTORS", ""))
CALENDAR_SELECTOR = os.getenv("CALENDAR_SELECTOR", "").strip()
NO_SLOT_TEXT = [s.lower() for s in _split(os.getenv("NO_SLOT_TEXT", ""))]
NEXT_MONTH_SELECTORS = _split(os.getenv(
    "NEXT_MONTH_SELECTORS",
    'button[aria-label*="next" i],.next-month,button.next,.fc-next-button,'
    '[class*="next"]:not([disabled]),button:has-text("›"),button:has-text(">")',
))
MONTH_LABEL_SELECTORS = _split(os.getenv(
    "MONTH_LABEL_SELECTORS",
    '.month-title,.calendar-title,[class*="month-name"],[class*="monthName"],h2,h3',
))

SCREENSHOT_DIR = BASE_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)
