"""
Instacart integration — generate pre-filled shopping list URLs via the
Instacart Developer Platform API.
"""

import hashlib
import logging
import time
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

_API_URL = "https://connect.instacart.com/idp/v1/products/products_link"
_CACHE_TTL = 3600  # 1 hour

# In-memory cache: (user_id, items_hash) → (url, timestamp)
_cache: dict[str, tuple[str, float]] = {}


def _items_hash(items: list[dict]) -> str:
    """Deterministic hash of suggestion items for cache keying."""
    parts = sorted(s.get("normalized_name", "") for s in items)
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def _build_line_items(suggestions: list[dict]) -> list[dict]:
    """Map shopping suggestions to Instacart line_items."""
    line_items = []
    for s in suggestions:
        line_items.append({
            "name": s.get("item_name", s.get("normalized_name", "")),
            "quantity": 1,
        })
    return line_items


def create_shopping_list(user_id: int, suggestions: list[dict]) -> Optional[str]:
    """Create an Instacart shopping list URL from shopping suggestions.

    Returns the URL string, or None if the API key is missing, there are
    no suggestions, or the request fails.
    """
    api_key = settings.instacart_api_key
    if not api_key:
        return None

    if not suggestions:
        return None

    # Check cache
    h = _items_hash(suggestions)
    cache_key = f"{user_id}:{h}"
    cached = _cache.get(cache_key)
    if cached:
        url, ts = cached
        if time.time() - ts < _CACHE_TTL:
            logger.debug("Instacart URL cache hit for user %d", user_id)
            return url

    line_items = _build_line_items(suggestions)
    payload = {
        "title": "Pantry Pilot Shopping List",
        "line_items": line_items,
        "landing_page_configuration": {
            "partner_linkback_url": "",
            "enable_pantry_items": True,
        },
    }

    try:
        resp = httpx.post(
            _API_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        url = data.get("products_link_url")
        if url:
            _cache[cache_key] = (url, time.time())
            logger.info("Created Instacart list for user %d (%d items)", user_id, len(line_items))
        return url
    except Exception as exc:
        logger.error("Instacart API error: %s", exc)
        return None
