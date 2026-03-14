"""
Receipt extractor — Claude vision to parse receipt photos into structured items.
"""

import base64
import json
import logging
from datetime import date

import anthropic

from config import settings
from core.item_normalizer import normalize

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

_SYSTEM_PROMPT = """You are a receipt parser. Extract structured data from receipt photos.

Return valid JSON with this exact structure:
{
  "store_name": "Store Name or null",
  "purchase_date": "YYYY-MM-DD or null",
  "total_amount": 12.99 or null,
  "items": [
    {
      "name": "Item Name",
      "quantity": 1,
      "unit": "each" or null,
      "price": 3.49 or null,
      "category": "produce" or "dairy" or "meat" or "bakery" or "frozen" or "beverages" or "snacks" or "pantry" or "household" or "other"
    }
  ]
}

Rules:
- Extract every line item you can read
- For quantity, default to 1 if not shown
- Normalize item names to plain English (e.g., "ORG BANA" → "Organic Bananas")
- Guess the category from the item name
- If the receipt is unclear, do your best and include what you can read
- Return ONLY valid JSON, no markdown or explanation"""


def extract_receipt(image_data: bytes) -> dict:
    """Extract receipt data from image bytes. Returns parsed dict."""
    b64 = base64.standard_b64encode(image_data).decode("utf-8")

    response = _client.messages.create(
        model=settings.claude_model,
        max_tokens=2000,
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
                    {
                        "type": "text",
                        "text": "Parse this receipt and extract all items. Return JSON only.",
                    },
                ],
            }
        ],
        system=_SYSTEM_PROMPT,
    )

    raw_text = response.content[0].text.strip()
    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1] if "\n" in raw_text else raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

    data = json.loads(raw_text)

    # Add normalized names
    for item in data.get("items", []):
        item["normalized_name"] = normalize(item.get("name", ""))

    logger.info("Extracted %d items from receipt", len(data.get("items", [])))
    return data


def format_receipt_summary(data: dict) -> str:
    """Format extracted receipt data into a readable summary."""
    lines = []
    store = data.get("store_name") or "Unknown Store"
    dt = data.get("purchase_date") or "Unknown Date"
    total = data.get("total_amount")

    lines.append(f"<b>Receipt: {store}</b>")
    lines.append(f"Date: {dt}")
    if total:
        lines.append(f"Total: ${total:.2f}")
    lines.append("")

    items = data.get("items", [])
    if items:
        lines.append(f"<b>{len(items)} items found:</b>")
        for item in items:
            name = item.get("name", "?")
            qty = item.get("quantity", 1)
            price = item.get("price")
            price_str = f" — ${price:.2f}" if price else ""
            qty_str = f" x{qty}" if qty and qty != 1 else ""
            lines.append(f"  • {name}{qty_str}{price_str}")
    else:
        lines.append("No items could be extracted.")

    return "\n".join(lines)
