"""
Pantry extractor — Claude vision to identify items from pantry/fridge/freezer photos.
"""

import base64
import json
import logging

import anthropic

from config import settings
from core.item_normalizer import normalize

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

_SYSTEM_PROMPT = """You are a pantry/fridge inventory analyzer. Identify all food items visible in the photo.

Return valid JSON with this exact structure:
{
  "items": [
    {
      "name": "Item Name",
      "estimated_qty": "full" or "half" or "nearly empty" or "1 bottle" or "2 bags",
      "condition": "good" or "nearly_empty" or "expiring_soon" or "unknown",
      "category": "produce" or "dairy" or "meat" or "bakery" or "frozen" or "beverages" or "snacks" or "pantry" or "condiments" or "other"
    }
  ]
}

Rules:
- List every distinct food item you can identify
- Estimate quantity/fullness where possible
- Mark condition as "nearly_empty" for items that look low
- Mark "expiring_soon" if produce looks like it should be used soon
- Be specific with names (e.g., "whole milk" not just "milk")
- Return ONLY valid JSON, no markdown or explanation"""


def extract_pantry(image_data: bytes, location_type: str = "pantry") -> dict:
    """Extract pantry items from image bytes. Returns parsed dict."""
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
                        "text": f"Identify all food items visible in this {location_type} photo. Return JSON only.",
                    },
                ],
            }
        ],
        system=_SYSTEM_PROMPT,
    )

    raw_text = response.content[0].text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1] if "\n" in raw_text else raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

    data = json.loads(raw_text)

    for item in data.get("items", []):
        item["normalized_name"] = normalize(item.get("name", ""))

    logger.info("Identified %d items in %s", len(data.get("items", [])), location_type)
    return data


def format_pantry_summary(data: dict, location_type: str) -> str:
    """Format extracted pantry data into a readable summary."""
    items = data.get("items", [])
    lines = [f"<b>{location_type.title()} Inventory Updated</b>"]
    lines.append(f"{len(items)} items identified:")
    lines.append("")

    for item in items:
        name = item.get("name", "?")
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
