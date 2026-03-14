"""
Chat handler — route commands, photos, and natural language messages.
Natural language uses a tool-use agent loop with conversation history.
"""

import base64
import json
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

import anthropic

from config import settings
from core.item_normalizer import normalize as _normalize_item
from core.shopping_engine import generate_suggestions, format_suggestions
from db.store import (
    get_current_pantry_items,
    get_recent_purchases,
    get_purchase_history,
    clear_pantry_items,
    insert_receipt,
    insert_receipt_items,
    insert_pantry_snapshot,
    insert_pantry_items,
    insert_chat_message,
    get_recent_chat_messages,
    add_manual_pantry_item,
    remove_pantry_item,
    insert_reminder,
    get_pending_reminders,
    get_user_timezone,
)
from core.receipt_extractor import extract_receipt, format_receipt_summary
from core.pantry_extractor import extract_pantry, format_pantry_summary

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

_WELCOME = """Welcome to <b>Pantry Pilot</b>!

I help you track what you buy and what you have, then tell you what to get next.

<b>How to use:</b>
1. Send a receipt photo — I'll extract your purchases
2. Send a pantry/fridge/freezer photo — I'll identify what you have
3. Use /list to get shopping suggestions

Type /help for all commands."""

_HELP = """<b>Pantry Pilot Commands</b>

<b>Photos:</b>
  Just send a photo — I'll auto-detect if it's a receipt, pantry, fridge, or freezer!
  You can also add a caption to be explicit.

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
    # If caption provided, use it directly
    if caption in ("receipt", "r"):
        return _process_receipt(user_id, file_id, image_data)
    if caption in ("pantry", "fridge", "freezer", "p", "f"):
        location_type = {"p": "pantry", "f": "fridge"}.get(caption, caption)
        return _process_pantry(user_id, file_id, image_data, location_type)

    # Auto-classify the photo using Claude vision
    photo_type = _classify_photo(image_data)
    logger.info("Auto-classified photo as: %s", photo_type)

    if photo_type == "receipt":
        return _process_receipt(user_id, file_id, image_data)
    if photo_type in ("pantry", "fridge", "freezer"):
        return _process_pantry(user_id, file_id, image_data, photo_type)

    return (
        "I couldn't tell what this photo is. Please try again with a clearer photo, "
        "or add a caption: \"receipt\", \"pantry\", \"fridge\", or \"freezer\"."
    )


def _classify_photo(image_data: bytes) -> str:
    """Use Claude vision to classify a photo as receipt, pantry, fridge, freezer, or unknown."""
    b64 = base64.standard_b64encode(image_data).decode("utf-8")
    try:
        response = _client.messages.create(
            model=settings.claude_model,
            max_tokens=20,
            system=(
                "Classify this photo into exactly one category. "
                "Reply with ONLY one word: receipt, pantry, fridge, freezer, or unknown. "
                "A receipt is a store receipt or bill. "
                "A pantry is shelves with dry goods/canned items. "
                "A fridge is an open refrigerator showing food. "
                "A freezer is a freezer compartment with frozen items."
            ),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": "What is this?"},
                    ],
                }
            ],
        )
        result = response.content[0].text.strip().lower()
        if result in ("receipt", "pantry", "fridge", "freezer"):
            return result
        return "unknown"
    except Exception as exc:
        logger.error("Photo classification failed: %s", exc)
        return "unknown"


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


_TOOLS = [
    {
        "name": "get_pantry_inventory",
        "description": (
            "Get the user's current pantry/fridge/freezer inventory. "
            "Returns all items currently tracked with their location, quantity, and condition."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_purchase_history",
        "description": (
            "Get the user's recent purchase history from scanned receipts. "
            "Shows items bought, store, date, and price."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of days to look back. Default 30."},
            },
            "required": [],
        },
    },
    {
        "name": "get_shopping_suggestions",
        "description": (
            "Generate smart shopping suggestions by comparing purchase history "
            "against current pantry inventory. Shows what to buy and why."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "add_pantry_item",
        "description": "Add an item to the user's pantry, fridge, or freezer inventory manually.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item_name": {"type": "string", "description": "Name of the item."},
                "location": {"type": "string", "enum": ["pantry", "fridge", "freezer"], "description": "Where the item is stored."},
                "category": {"type": "string", "description": "Optional category (e.g. 'dairy', 'produce')."},
            },
            "required": ["item_name", "location"],
        },
    },
    {
        "name": "remove_pantry_item",
        "description": "Remove an item from the user's pantry/fridge/freezer inventory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item_name": {"type": "string", "description": "Name of the item to remove."},
            },
            "required": ["item_name"],
        },
    },
    {
        "name": "set_reminder",
        "description": "Schedule a reminder. The reminder will be sent as a Telegram message when due.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reminder_text": {"type": "string", "description": "What to remind the user about."},
                "due_at": {"type": "string", "description": "ISO 8601 datetime (e.g. '2026-03-15T09:00:00'). Interpreted in the user's timezone."},
            },
            "required": ["reminder_text", "due_at"],
        },
    },
    {
        "name": "list_reminders",
        "description": "Show the user's pending (unsent) reminders.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]

_MAX_TOOL_ROUNDS = 5


def _build_system_prompt(user_tz: str) -> str:
    now = datetime.now(ZoneInfo(user_tz))
    return (
        "You are Pantry Pilot, a helpful personal assistant integrated with the user's pantry, "
        "purchase history, and reminder system. You can look up their inventory, "
        "suggest what to buy, add or remove items, and set reminders.\n\n"
        "Use the provided tools to answer questions — do NOT guess about inventory "
        "or purchases; always call the relevant tool first.\n\n"
        "When setting reminders, convert relative times (like 'tomorrow morning') "
        "to absolute ISO datetimes based on the current time.\n\n"
        "Keep responses concise and conversational. Use plain text (no HTML, no markdown).\n\n"
        f"Current date/time: {now.strftime('%Y-%m-%d %H:%M %Z')}\n"
        f"User timezone: {user_tz}\n"
    )


def _execute_tool(user_id: int, tool_name: str, tool_input: dict) -> str:
    """Dispatch a tool call to the appropriate function, return a string result."""
    try:
        if tool_name == "get_pantry_inventory":
            items = get_current_pantry_items(user_id)
            if not items:
                return "Pantry is empty. No items tracked yet."
            lines = []
            current_loc = None
            for item in items:
                loc = item.get("snapshot_type", "unknown")
                if loc != current_loc:
                    current_loc = loc
                    lines.append(f"\n[{loc.upper()}]")
                qty = item.get("estimated_qty", "")
                cond = item.get("condition", "")
                extra = f" ({qty})" if qty else ""
                extra += f" - {cond}" if cond and cond != "good" else ""
                lines.append(f"  - {item['item_name']}{extra}")
            return "\n".join(lines)

        if tool_name == "get_purchase_history":
            days = tool_input.get("days", 30)
            items = get_recent_purchases(user_id, days=days)
            if not items:
                return f"No purchases found in the last {days} days."
            lines = [f"Purchases (last {days} days):"]
            for item in items:
                price = f" ${item['price']}" if item.get("price") else ""
                store = f" @ {item['store_name']}" if item.get("store_name") else ""
                dt = f" ({item['purchase_date']})" if item.get("purchase_date") else ""
                lines.append(f"  - {item['item_name']}{price}{store}{dt}")
            return "\n".join(lines)

        if tool_name == "get_shopping_suggestions":
            suggestions = generate_suggestions(user_id)
            return format_suggestions(suggestions)

        if tool_name == "add_pantry_item":
            item_name = tool_input["item_name"]
            location = tool_input["location"]
            category = tool_input.get("category")
            normalized = _normalize_item(item_name)
            add_manual_pantry_item(user_id, item_name, normalized, location, category)
            return f"Added '{item_name}' to {location}."

        if tool_name == "remove_pantry_item":
            item_name = tool_input["item_name"]
            normalized = _normalize_item(item_name)
            count = remove_pantry_item(user_id, normalized)
            if count:
                return f"Removed '{item_name}' from inventory ({count} item(s))."
            return f"No current item matching '{item_name}' found in inventory."

        if tool_name == "set_reminder":
            reminder_text = tool_input["reminder_text"]
            due_at_str = tool_input["due_at"]
            user_tz = get_user_timezone(user_id)
            tz = ZoneInfo(user_tz)
            naive_dt = datetime.fromisoformat(due_at_str)
            if naive_dt.tzinfo is None:
                local_dt = naive_dt.replace(tzinfo=tz)
            else:
                local_dt = naive_dt
            utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
            insert_reminder(user_id, reminder_text, utc_dt.isoformat())
            local_str = local_dt.strftime("%b %d at %I:%M %p %Z")
            return f"Reminder set: '{reminder_text}' — {local_str}"

        if tool_name == "list_reminders":
            reminders = get_pending_reminders(user_id)
            if not reminders:
                return "No pending reminders."
            user_tz = get_user_timezone(user_id)
            tz = ZoneInfo(user_tz)
            lines = ["Pending reminders:"]
            for r in reminders:
                due = r["due_at"]
                if isinstance(due, str):
                    due = datetime.fromisoformat(due)
                local_due = due.astimezone(tz)
                lines.append(
                    f"  - {r['reminder_text']} (due {local_due.strftime('%b %d at %I:%M %p')})"
                )
            return "\n".join(lines)

        return f"Unknown tool: {tool_name}"

    except Exception as exc:
        logger.error("Tool execution error (%s): %s", tool_name, exc)
        return f"Error executing {tool_name}: {exc}"


def _handle_chat(user_id: int, text: str) -> str:
    """Handle natural language text via Claude tool-use agent loop."""
    # Load conversation history
    history = get_recent_chat_messages(user_id, limit=20)
    messages = [{"role": h["role"], "content": h["content"]} for h in history]
    messages.append({"role": "user", "content": text})

    user_tz = get_user_timezone(user_id)
    system_prompt = _build_system_prompt(user_tz)

    for _round in range(_MAX_TOOL_ROUNDS):
        response = _client.messages.create(
            model=settings.claude_model,
            max_tokens=1024,
            system=system_prompt,
            tools=_TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            reply_parts = [
                block.text for block in response.content if block.type == "text"
            ]
            reply = "\n".join(reply_parts) if reply_parts else "I'm not sure how to help with that."
            insert_chat_message(user_id, "user", text)
            insert_chat_message(user_id, "assistant", reply)
            return reply

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result_str = _execute_tool(user_id, block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })
            messages.append({"role": "user", "content": tool_results})
            continue

        reply_parts = [
            block.text for block in response.content if block.type == "text"
        ]
        reply = "\n".join(reply_parts) if reply_parts else "Sorry, something went wrong."
        insert_chat_message(user_id, "user", text)
        insert_chat_message(user_id, "assistant", reply)
        return reply

    reply = "I ran into a loop trying to answer. Could you rephrase your question?"
    insert_chat_message(user_id, "user", text)
    insert_chat_message(user_id, "assistant", reply)
    return reply


def _parse_date(date_str: str | None) -> date | None:
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None
