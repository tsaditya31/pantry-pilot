"""
Telegram Bot API helpers — sendMessage, getFile, sendChatAction.
"""

import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

_BASE = f"https://api.telegram.org/bot{settings.telegram_bot_token}"


def send_message(chat_id: int, text: str, parse_mode: str = "HTML") -> bool:
    url = f"{_BASE}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        resp = httpx.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 400 and parse_mode:
            logger.info("Retrying without parse_mode...")
            payload.pop("parse_mode", None)
            try:
                resp = httpx.post(url, json=payload, timeout=15)
                resp.raise_for_status()
                return True
            except Exception as retry_exc:
                logger.error("Plain text retry failed: %s", retry_exc)
        else:
            logger.error("Telegram API error: %s — %s", exc.response.status_code, exc.response.text)
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
    return False


def send_chat_action(chat_id: int, action: str = "typing"):
    url = f"{_BASE}/sendChatAction"
    try:
        httpx.post(url, json={"chat_id": chat_id, "action": action}, timeout=10)
    except Exception as exc:
        logger.warning("sendChatAction failed: %s", exc)


def get_file_url(file_id: str) -> str | None:
    """Get download URL for a Telegram file."""
    url = f"{_BASE}/getFile"
    try:
        resp = httpx.post(url, json={"file_id": file_id}, timeout=10)
        resp.raise_for_status()
        file_path = resp.json()["result"]["file_path"]
        return f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"
    except Exception as exc:
        logger.error("getFile failed: %s", exc)
        return None


def download_file(file_id: str) -> bytes | None:
    """Download file content as bytes."""
    file_url = get_file_url(file_id)
    if not file_url:
        return None
    try:
        resp = httpx.get(file_url, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        logger.error("File download failed: %s", exc)
        return None
