"""Run the watcher standalone (no dashboard). For the web control panel use server.py.

    python3 tls_bot.py
"""
import sys
import threading

import config
from watcher import WatcherState, run_watcher

if __name__ == "__main__":
    if not config.APPOINTMENT_URL:
        print("APPOINTMENT_URL not set in .env. Run setup_login.py first.")
        sys.exit(1)
    state = WatcherState()
    stop = threading.Event()
    try:
        run_watcher(state, stop)
    except KeyboardInterrupt:
        stop.set()
