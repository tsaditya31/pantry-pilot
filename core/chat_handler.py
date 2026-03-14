"""
Chat handler — route commands, photos, and natural language messages.
"""

import json
import logging
from datetime import date

import anthropic

from config import settings
from db.store import (
    get_current_pantry_items,
    get_recent_purchases,
    clear_pantry_items,
    insert_receipt,
    insert_receipt_items,
    insert_pantry_snapshot,
    insert_pantry_items,
)
from core.receipt_extractor import extract_receipt, format_receipt_summary
from core.pantry_extractor import extract_pantry, format_pantry_summary
from core.shopping_engine import generate_suggestions, format_suggestions

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

_WELCOME = """Welcome to <b>Pantry Pilot</b>!

I help you track what you buy and what you have, then tell you what to get next.

<b>How to use:</b>
1. Send a receipt photo with caption "receipt"
2. Send a pantry/fridge photo with caption "pantry" or "fridge"
3. Use /list to get shopping suggestions

Type /help for all commands."""

_HELP = """<b>Pantry Pilot Commands</b>

<b>Photos:</b>
  Send photo + caption "receipt" — Log a receipt
  Send photo + caption "pantry" — Update pantry inventory
  Send photo + caption "fridge" — Update fridge inventory
  Send photo + caption "freezer" — Update freezer inventory

<b>Commands:</b>
  /list — Get shopping suggestions
  /history — Recent purchases (7 days)
  /items — Current pantry/fridge inventory
  /clear — Reset all pantry data
  /help — Show this message

<b>Chat:</b>
  Send any text to ask questions or make corrections."""


def handle_message(user_id: int, chat_id: int, text: str) -> str:
    """Handle a text message from a user."""
    cmd = text.strip().lower()

    if cmd == "/start":
        return _WELCOME

    if cmd == "/help":
        return _HELP

    if cmd == "/list":
        suggestions = generate_suggestions(user_id, history_days=settings.purchase_history_days)
        return format_suggestions(suggestions)

    if cmd == "/history":
        return _format_history(user_id)

    if cmd == "/items":
        return _format_inventory(user_id)

    if cmd == "/clear":
        clear_pantry_items(user_id)
        return "Pantry data cleared. Send new photos to rebuild your inventory."

    # Natural language — pass to Claude
    return _handle_chat(user_id, text)


def handle_photo(user_id: int, chat_id: int, file_id: str,
                 image_data: bytes, caption: str) -> str:
    """Handle a photo message from a user."""
    if not caption:
        return (
            "Please resend the photo with a caption:\n"
            '  • "receipt" — to log a purchase\n'
            '  • "pantry", "fridge", or "freezer" — to update inventory'
        )

    if caption in ("receipt", "r"):
        return _process_receipt(user_id, file_id, image_data)

    if caption in ("pantry", "fridge", "freezer", "p", "f"):
        location = caption[0]
        location_map = {"p": "pantry", "f": "fridge", "r": "receipt"}
        location_type = {"p": "pantry", "f": "fridge"}.get(location, caption)
        if caption in ("pantry", "fridge", "freezer"):
            location_type = caption
        return _process_pantry(user_id, file_id, image_data, location_type)

    return (
        f'Unknown caption "{caption}". Please use:\n'
        '  • "receipt" — to log a purchase\n'
        '  • "pantry", "fridge", or "freezer" — to update inventory'
    )


def _process_receipt(user_id: int, file_id: str, image_data: bytes) -> str:
    """Extract and store receipt data."""
    try:
        data = extract_receipt(image_data)
    except Exception as exc:
        logger.error("Receipt extraction failed: %s", exc)
        return "Sorry, I couldn't read that receipt. Please try a clearer photo."

    # Store receipt
    receipt_id = insert_receipt(
        user_id=user_id,
        telegram_file_id=file_id,
        store_name=data.get("store_name"),
        purchase_date=_parse_date(data.get("purchase_date")),
        total_amount=data.get("total_amount"),
        raw_extraction=data,
    )

    # Store items
    items = []
    for item in data.get("items", []):
        items.append({
            "item_name": item.get("name", ""),
            "normalized_name": item.get("normalized_name", ""),
            "category": item.get("category"),
            "quantity": item.get("quantity", 1),
            "unit": item.get("unit"),
            "price": item.get("price"),
        })

    if items:
        insert_receipt_items(receipt_id, user_id, items)

    return format_receipt_summary(data)


def _process_pantry(user_id: int, file_id: str, image_data: bytes,
                    location_type: str) -> str:
    """Extract and store pantry snapshot."""
    try:
        data = extract_pantry(image_data, location_type)
    except Exception as exc:
        logger.error("Pantry extraction failed: %s", exc)
        return f"Sorry, I couldn't analyze that {location_type} photo. Please try a clearer photo."

    # Store snapshot (marks old items as not current)
    snapshot_id = insert_pantry_snapshot(
        user_id=user_id,
        snapshot_type=location_type,
        telegram_file_id=file_id,
        raw_extraction=data,
    )

    # Store items
    items = []
    for item in data.get("items", []):
        items.append({
            "item_name": item.get("name", ""),
            "normalized_name": item.get("normalized_name", ""),
            "category": item.get("category"),
            "estimated_qty": item.get("estimated_qty"),
            "condition": item.get("condition"),
        })

    if items:
        insert_pantry_items(snapshot_id, user_id, items)

    return format_pantry_summary(data, location_type)


def _format_history(user_id: int) -> str:
    """Format recent purchase history."""
    purchases = get_recent_purchases(user_id, days=7)
    if not purchases:
        return "No purchases in the last 7 days. Send receipt photos to start tracking!"

    lines = ["<b>Recent Purchases (7 days)</b>", ""]
    current_store = None

    for p in purchases:
        store = p.get("store_name") or "Unknown Store"
        dt = p.get("purchase_date")
        if store != current_store:
            current_store = store
            date_str = dt.strftime("%m/%d") if dt else ""
            lines.append(f"\n<b>{store}</b> ({date_str})")

        name = p["item_name"]
        qty = p.get("quantity")
        price = p.get("price")
        extras = []
        if qty and qty != 1:
            extras.append(f"x{qty}")
        if price:
            extras.append(f"${price:.2f}")
        suffix = f" ({', '.join(extras)})" if extras else ""
        lines.append(f"  • {name}{suffix}")

    return "\n".join(lines)


def _format_inventory(user_id: int) -> str:
    """Format current pantry/fridge inventory."""
    items = get_current_pantry_items(user_id)
    if not items:
        return "No inventory data. Send pantry or fridge photos to get started!"

    lines = ["<b>Current Inventory</b>", ""]
    current_type = None

    for item in items:
        stype = item.get("snapshot_type", "unknown")
        if stype != current_type:
            current_type = stype
            lines.append(f"\n<b>{stype.title()}</b>")

        name = item["item_name"]
        qty = item.get("estimated_qty", "")
        condition = item.get("condition", "")
        extras = []
        if qty:
            extras.append(qty)
        if condition and condition not in ("good", "unknown"):
            extras.append(condition.replace("_", " "))
        suffix = f" ({', '.join(extras)})" if extras else ""
        lines.append(f"  • {name}{suffix}")

    return "\n".join(lines)


def _handle_chat(user_id: int, text: str) -> str:
    """Handle natural language text via Claude."""
    try:
        response = _client.messages.create(
            model=settings.claude_model,
            max_tokens=500,
            system=(
                "You are Pantry Pilot, a helpful shopping assistant bot on Telegram. "
                "Users send you receipt and pantry photos to track their groceries. "
                "Answer questions about food, cooking, and shopping. "
                "Keep responses concise (2-3 sentences max). "
                "If they seem to be asking about a feature, point them to /help."
            ),
            messages=[{"role": "user", "content": text}],
        )
        return response.content[0].text
    except Exception as exc:
        logger.error("Chat error: %s", exc)
        return "Sorry, I couldn't process that. Try /help for available commands."


def _parse_date(date_str: str | None) -> date | None:
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None
