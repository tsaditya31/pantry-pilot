"""
Telegram bot — long-polling loop with multi-user support and photo handling.
"""

import logging
import time

import httpx

from config import settings
from bot.telegram_api import send_message, send_chat_action, download_file
from core.chat_handler import handle_message, handle_photo
from db.store import upsert_user

logger = logging.getLogger(__name__)

_BASE = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
_POLL_INTERVAL = settings.bot_poll_interval
_TIMEOUT = 30


def _get_updates(offset: int) -> list[dict]:
    url = f"{_BASE}/getUpdates"
    try:
        resp = httpx.get(
            url,
            params={"offset": offset, "timeout": _TIMEOUT},
            timeout=_TIMEOUT + 5,
        )
        resp.raise_for_status()
        return resp.json().get("result", [])
    except Exception as exc:
        logger.warning("getUpdates error: %s", exc)
        return []


def _delete_webhook():
    url = f"{_BASE}/deleteWebhook"
    try:
        resp = httpx.post(url, timeout=10)
        resp.raise_for_status()
        logger.info("deleteWebhook: %s", resp.json())
    except Exception as exc:
        logger.warning("deleteWebhook failed: %s", exc)


def _process_message(msg: dict):
    """Route a single incoming message."""
    chat_id = msg.get("chat", {}).get("id")
    if not chat_id:
        return

    from_user = msg.get("from", {})
    telegram_id = from_user.get("id", chat_id)
    first_name = from_user.get("first_name")
    username = from_user.get("username")

    # Auto-register user
    user_id = upsert_user(telegram_id, first_name, username)

    # Photo message
    photos = msg.get("photo")
    if photos:
        send_chat_action(chat_id, "typing")
        # Telegram sends multiple sizes; pick the largest
        file_id = photos[-1]["file_id"]
        caption = (msg.get("caption") or "").strip().lower()
        logger.info("Photo from user %s (caption: %s)", telegram_id, caption or "<none>")

        try:
            image_data = download_file(file_id)
            if not image_data:
                send_message(chat_id, "Sorry, I couldn't download the photo. Please try again.")
                return
            reply = handle_photo(user_id, chat_id, file_id, image_data, caption)
        except Exception as exc:
            logger.error("Photo handling error: %s", exc)
            reply = "Sorry, something went wrong processing your photo."

        send_message(chat_id, reply)
        return

    # Text message
    text = (msg.get("text") or "").strip()
    if not text:
        return

    logger.info("Message from user %s: %s", telegram_id, text[:100])
    send_chat_action(chat_id, "typing")

    try:
        reply = handle_message(user_id, chat_id, text)
    except Exception as exc:
        logger.error("chat_handler error: %s", exc)
        reply = "Sorry, something went wrong processing your request."

    send_message(chat_id, reply)


def run_polling_loop():
    """Block forever, polling Telegram for new messages."""
    _delete_webhook()
    logger.info("Pantry Pilot bot polling started.")
    offset = 0

    while True:
        updates = _get_updates(offset)

        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message")
            if not msg:
                continue

            try:
                _process_message(msg)
            except Exception as exc:
                logger.error("Error processing update %s: %s", update["update_id"], exc)

        if not updates:
            time.sleep(_POLL_INTERVAL)
