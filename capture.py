"""Guided one-time capture + login.

Opens the bot's own browser, walks YOU through the real click-path (the deep
booking URL can't be loaded directly — Cloudflare blocks it / it redirects out),
and dumps the DOM at each checkpoint so we can wire exact navigation + slot
detection. Also persists your logged-in session into the profile.

Run:  .venv/bin/python capture.py
"""
import time

import config
import browser
from patchright.sync_api import sync_playwright


def dump(page, name):
    html = page.content()
    (config.BASE_DIR / f"{name}.html").write_text(html, encoding="utf-8")
    try:
        page.screenshot(path=str(config.SCREENSHOT_DIR / f"{name}.png"), full_page=True)
    except Exception:
        pass
    print(f"  saved {name}.html ({len(html)} bytes)  url: {page.url}")


def list_links(page, pattern):
    js = """(re) => {
      const rx = new RegExp(re, 'i'); const out=[];
      document.querySelectorAll('a,button,[role=button]').forEach(e=>{
        const t=(e.innerText||'').trim().replace(/\\s+/g,' ').slice(0,50);
        const h=e.getAttribute('href')||'';
        const cls=(e.getAttribute('class')||'').slice(0,60);
        if(rx.test(t+' '+h+' '+cls)) out.push({tag:e.tagName,text:t,href:h,cls});
      });
      return out.slice(0,25);
    }"""
    return page.evaluate(js, pattern)


def main():
    print("\n=== GUIDED CAPTURE ===")
    print("A browser window opens at the HOME page. Follow the prompts here.\n")
    with sync_playwright() as p:
        ctx, page = browser.launch_context(p)
        page.goto(config.HOME_URL, wait_until="domcontentloaded", timeout=60000)

        input("STEP 1 — In the window: pick country, LOG IN, and open your "
              "'My applications' LIST page. Then press ENTER here... ")
        time.sleep(2)
        print(f"Current URL: {page.url}")
        dump(page, "step_applications")
        print("  application-ish links:")
        for r in list_links(page, "appl|book|continue|view|detail|workflow"):
            print("   ", r)

        input("\nSTEP 2 — Now click your FIRST application and reach the "
              "3-MONTH CALENDAR (the appointment-booking page). Then press ENTER... ")
        time.sleep(2)
        print(f"Current URL: {page.url}")
        dump(page, "calendar")
        print("  next-month-ish controls:")
        for r in list_links(page, "next|prev|›|‹|>|<|month|forward|arrow"):
            print("   ", r)

        # quick structural probe of the calendar
        probe = page.evaluate("""() => {
          const pick = sel => document.querySelectorAll(sel).length;
          return {
            tables: pick('table'),
            tds: pick('td'),
            buttons: pick('button'),
            disabled: pick('[disabled],.disabled,[aria-disabled=true]'),
            available_guess: pick('[class*=available],[class*=free],[class*=open],[class*=slot]'),
          };
        }""")
        print("  calendar probe:", probe)

        print("\nSession + dumps saved. You can close the window.")
        print("Files: step_applications.html, calendar.html (+ screenshots/).")
        input("Press ENTER to finish... ")
        ctx.close()


if __name__ == "__main__":
    main()
