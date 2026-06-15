"""Telegram Bot API helpers. Never raise — a Telegram blip must not kill the loop."""
import logging

import requests

import config

log = logging.getLogger("telegram")
_API = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"


def send_message(text: str) -> bool:
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured; skipping message: %s", text)
        return False
    for attempt in range(3):
        try:
            r = requests.post(
                f"{_API}/sendMessage",
                data={"chat_id": config.TELEGRAM_CHAT_ID, "text": text},
                timeout=20,
            )
            if r.ok:
                return True
            log.warning("sendMessage failed (%s): %s", r.status_code, r.text[:200])
        except Exception as e:  # noqa: BLE001
            log.warning("sendMessage error (try %d): %s", attempt + 1, e)
    return False


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
