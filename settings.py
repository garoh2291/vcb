"""Mutable runtime settings that override .env, persisted to runtime_config.json.

Holds the editable bits (email, password, schedule). Everything falls back to the
.env values from config.py when not overridden. Thread-safe.
"""
import json
import logging
import os
import threading

import config

log = logging.getLogger("settings")

_FILE = config.BASE_DIR / "runtime_config.json"
_lock = threading.RLock()
_data = {}


def load():
    global _data
    with _lock:
        try:
            if _FILE.exists():
                _data = json.loads(_FILE.read_text(encoding="utf-8")) or {}
            else:
                _data = {}
        except Exception as e:  # noqa: BLE001
            log.warning("could not read runtime_config.json: %s", e)
            _data = {}


def save():
    with _lock:
        try:
            _FILE.write_text(json.dumps(_data, indent=2), encoding="utf-8")
            try:
                os.chmod(_FILE, 0o600)  # contains the password
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            log.warning("could not write runtime_config.json: %s", e)


# ---- credentials ----

def get_email() -> str:
    with _lock:
        return _data.get("email") or config.TLS_LOGIN


def get_password() -> str:
    with _lock:
        return _data.get("password") or config.TLS_PASSWORD


def email_source() -> str:
    with _lock:
        return "saved" if _data.get("email") else ".env"


def password_set() -> bool:
    with _lock:
        return bool(_data.get("password") or config.TLS_PASSWORD)


def set_credentials(email: str = "", password: str = ""):
    """Overwrite only the provided non-empty fields."""
    with _lock:
        if email and email.strip():
            _data["email"] = email.strip()
        if password and password.strip():
            _data["password"] = password.strip()
        save()


# ---- schedule ----

def get_schedule() -> dict:
    with _lock:
        s = _data.get("schedule") or {}
        return {
            "enabled": bool(s.get("enabled")),
            "start": s.get("start", ""),
            "end": s.get("end", ""),
            "days": list(s.get("days", [])),
        }


def set_schedule(start: str, end: str, days):
    with _lock:
        _data["schedule"] = {
            "enabled": True,
            "start": start,
            "end": end,
            "days": list(days or []),
        }
        save()


def clear_schedule():
    with _lock:
        _data["schedule"] = {"enabled": False}
        save()


load()
