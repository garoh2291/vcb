"""Telegram Bot API helpers. Never raise — a Telegram blip must not kill the loop."""
import json
import logging

import requests

import config

log = logging.getLogger("telegram")
_API = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"

# Persistent tappable button keyboard shown under the chat input.
MAIN_KEYBOARD = {
    "keyboard": [
        ["▶ Start", "■ Stop"],
        ["🔄 Run now", "📸 Run + shots"],
        ["📊 Status"],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}


def send_message(text: str, keyboard: bool = False) -> bool:
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured; skipping message: %s", text)
        return False
    data = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text}
    if keyboard:
        data["reply_markup"] = json.dumps(MAIN_KEYBOARD)
    for attempt in range(3):
        try:
            r = requests.post(f"{_API}/sendMessage", data=data, timeout=20)
            if r.ok:
                return True
            log.warning("sendMessage failed (%s): %s", r.status_code, r.text[:200])
        except Exception as e:  # noqa: BLE001
            log.warning("sendMessage error (try %d): %s", attempt + 1, e)
    return False


def set_commands() -> bool:
    """Register the '/' command menu shown in the Telegram client."""
    if not config.TELEGRAM_TOKEN:
        return False
    cmds = [
        {"command": "start", "description": "Start the watcher"},
        {"command": "stop", "description": "Stop the watcher"},
        {"command": "run", "description": "Check now"},
        {"command": "shots", "description": "Check now + send 3 month screenshots"},
        {"command": "status", "description": "Show current status"},
        {"command": "whoami", "description": "Show the email in use"},
        {"command": "setemail", "description": "Set login email: /setemail you@x.com"},
        {"command": "setpassword", "description": "Set password: /setpassword secret"},
        {"command": "schedule", "description": "Show/set schedule: /schedule 09:00 21:00 mon-fri"},
        {"command": "help", "description": "Show commands"},
    ]
    try:
        r = requests.post(f"{_API}/setMyCommands",
                          data={"commands": json.dumps(cmds)}, timeout=20)
        return r.ok
    except Exception as e:  # noqa: BLE001
        log.warning("setMyCommands error: %s", e)
        return False


def get_updates(offset=None, timeout=50):
    """Long-poll for incoming messages. Returns a list of update dicts (may be empty)."""
    if not config.TELEGRAM_TOKEN:
        return []
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(f"{_API}/getUpdates", params=params, timeout=timeout + 15)
        if r.ok:
            return r.json().get("result", [])
        log.warning("getUpdates failed (%s): %s", r.status_code, r.text[:200])
    except Exception as e:  # noqa: BLE001
        log.debug("getUpdates error: %s", e)
    return []


def send_photo(path: str, caption: str = "") -> bool:
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured; skipping photo: %s", caption)
        return False
    for attempt in range(3):
        try:
            with open(path, "rb") as f:
                r = requests.post(
                    f"{_API}/sendPhoto",
                    data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption[:1000]},
                    files={"photo": f},
                    timeout=60,
                )
            if r.ok:
                return True
            log.warning("sendPhoto failed (%s): %s", r.status_code, r.text[:200])
        except Exception as e:  # noqa: BLE001
            log.warning("sendPhoto error (try %d): %s", attempt + 1, e)
    # Fallback: at least tell the user in text.
    send_message(f"[photo failed to upload] {caption}")
    return False
