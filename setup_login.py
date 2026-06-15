"""One-time setup: open real Chrome, let the user log in by hand (and solve any
Cloudflare check once), save the session into the persistent profile, then dump
the appointment-page HTML so we can tune the slot-detection selectors.

Run:  python3 setup_login.py
"""
import sys
import time

import browser
import config


def main():
    print("\n=== TLScontact watcher — first-time setup ===\n")
    print("A real Chrome window will open. Do this in it:")
    print("  1. Click through any Cloudflare check.")
    print("  2. Log in with your TLScontact email + password.")
    print(f"  3. Pick country '{ 'Armenia' }', open 'My applications', click your first")
    print("     application, and reach the appointment-booking page (the 3-month calendar).")
    print("  4. Copy that page's URL — you'll paste it into .env as APPOINTMENT_URL.\n")
    print("Leave the window open while you do this. Come back to THIS terminal when done.\n")

    with browser.sync_playwright() as p:
        context, page = browser.launch_context(p)
        # Always start at the HOME page. Hitting the deep appointment URL while logged
        # out triggers Cloudflare's hard "Sorry, you have been blocked" page.
        try:
            page.goto(config.HOME_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:  # noqa: BLE001
            print(f"(navigation note: {e})")

        input("\n>>> Press ENTER here once you are ON the appointment calendar page... ")

        # Dump whatever page is currently open for selector tuning.
        try:
            html = page.content()
            out = config.BASE_DIR / "calendar_dump.html"
            out.write_text(html, encoding="utf-8")
            print(f"\nSaved current page HTML to: {out}")
            print(f"Current URL: {page.url}")
            print("→ Put this URL into .env as APPOINTMENT_URL if not already set.")
        except Exception as e:  # noqa: BLE001
            print(f"Could not dump page: {e}")

        shot = str(config.SCREENSHOT_DIR / "setup.png")
        try:
            page.screenshot(path=shot, full_page=True)
            print(f"Saved screenshot to: {shot}")
        except Exception:  # noqa: BLE001
            pass

        print("\nSession is now saved in the profile. You can close the window.")
        print("Next: open calendar_dump.html, find the calendar day elements, and set")
        print("AVAILABLE_SELECTORS / CALENDAR_SELECTOR in .env. Then run tls_bot.py.\n")
        time.sleep(1)
        context.close()


if __name__ == "__main__":
    sys.exit(main())
