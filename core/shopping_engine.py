"""
Shopping engine — analyze purchase history vs current pantry to generate suggestions.
"""

import logging
from datetime import date, timedelta

from db.store import get_purchase_history, get_current_pantry_items, save_suggestions

logger = logging.getLogger(__name__)


def generate_suggestions(user_id: int, history_days: int = 90) -> list[dict]:
    """Generate shopping suggestions based on purchase history vs pantry.

    Algorithm:
    1. Get purchase history (last N days), grouped by normalized_name
    2. Get current pantry items (is_current=TRUE)
    3. For each regularly-purchased item:
       - NOT in pantry → high priority
       - In pantry but "nearly_empty" → normal priority
       - Overdue vs avg purchase interval → low priority
    4. Return sorted by priority
    """
    history = get_purchase_history(user_id, days=history_days)
    pantry = get_current_pantry_items(user_id)

    # Build pantry lookup: normalized_name → item info
    pantry_lookup = {}
    for item in pantry:
        pantry_lookup[item["normalized_name"]] = item

    suggestions = []
    today = date.today()

    for purchase in history:
        norm_name = purchase["normalized_name"]
        purchase_count = purchase["purchase_count"]
        last_purchased = purchase["last_purchased"]
        first_purchased = purchase["first_purchased"]

        # Skip items bought only once (not a pattern)
        if purchase_count < 2:
            continue

        # Compute average purchase interval
        if last_purchased and first_purchased and last_purchased != first_purchased:
            span_days = (last_purchased - first_purchased).days
            avg_interval = span_days / (purchase_count - 1)
        else:
            avg_interval = None

        pantry_item = pantry_lookup.get(norm_name)
        days_since = (today - last_purchased).days if last_purchased else None

        if pantry_item is None:
            # Not in pantry at all → high priority
            suggestions.append({
                "item_name": norm_name.title(),
                "normalized_name": norm_name,
                "reason": f"Bought {purchase_count}x but not in pantry",
                "priority": "high",
                "last_purchased": last_purchased,
                "in_pantry": False,
            })
        elif pantry_item.get("condition") == "nearly_empty":
            # In pantry but running low → normal priority
            suggestions.append({
                "item_name": norm_name.title(),
                "normalized_name": norm_name,
                "reason": f"Running low ({pantry_item.get('estimated_qty', 'nearly empty')})",
                "priority": "normal",
                "last_purchased": last_purchased,
                "in_pantry": True,
            })
        elif avg_interval and days_since and days_since > avg_interval * 1.2:
            # Overdue based on purchase pattern → low priority
            suggestions.append({
                "item_name": norm_name.title(),
                "normalized_name": norm_name,
                "reason": f"Usually buy every ~{avg_interval:.0f} days, last bought {days_since} days ago",
                "priority": "low",
                "last_purchased": last_purchased,
                "in_pantry": True,
            })

    # Sort by priority
    priority_order = {"high": 0, "normal": 1, "low": 2}
    suggestions.sort(key=lambda s: (priority_order.get(s["priority"], 3), s["item_name"]))

    # Save to DB
    if suggestions:
        save_suggestions(user_id, suggestions)

    logger.info("Generated %d shopping suggestions for user %d", len(suggestions), user_id)
    return suggestions


def format_suggestions(suggestions: list[dict]) -> str:
    """Format shopping suggestions into a readable message."""
    if not suggestions:
        return (
            "No shopping suggestions yet.\n\n"
            "Send me some receipt photos to build your purchase history, "
            "then pantry photos so I know what you have!"
        )

    lines = ["<b>Shopping Suggestions</b>", ""]

    current_priority = None
    priority_labels = {"high": "Need to Buy", "normal": "Running Low", "low": "Might Need Soon"}

    for s in suggestions:
        if s["priority"] != current_priority:
            current_priority = s["priority"]
            label = priority_labels.get(current_priority, current_priority)
            emoji = {"high": "!!", "normal": "!", "low": "?"}
            lines.append(f"\n<b>[{emoji.get(current_priority, '')}] {label}</b>")

        reason = s.get("reason", "")
        lines.append(f"  • {s['item_name']} — {reason}")

    return "\n".join(lines)
