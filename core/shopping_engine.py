"""
Shopping engine — analyze purchase history vs current pantry to generate suggestions.
Integrates stocking rules and consumption rate modeling.
"""

import logging
from datetime import date, timedelta

from core.consumption_model import compute_all_rates
from db.store import (
    get_purchase_history,
    get_current_pantry_items,
    get_stocking_rules,
    save_suggestions,
)

logger = logging.getLogger(__name__)


def generate_suggestions(user_id: int, history_days: int = 90) -> list[dict]:
    """Generate shopping suggestions based on stocking rules, consumption
    modeling, purchase history, and current pantry state."""
    history = get_purchase_history(user_id, days=history_days)
    pantry = get_current_pantry_items(user_id)

    pantry_lookup = {item["normalized_name"]: item for item in pantry}

    suggestions = []
    suggested_names: set[str] = set()
    today = date.today()

    # ── Pass 1: Stocking rules ───────────────────────────────────────────
    rules = get_stocking_rules(user_id)
    for rule in rules:
        norm_name = rule["normalized_name"]
        display = rule["display_name"]
        pantry_item = pantry_lookup.get(norm_name)

        if pantry_item is None:
            suggestions.append({
                "item_name": display,
                "normalized_name": norm_name,
                "reason": "Always-stock rule: not in pantry",
                "priority": "high",
                "last_purchased": None,
                "in_pantry": False,
            })
            suggested_names.add(norm_name)
        elif pantry_item.get("condition") == "nearly_empty":
            suggestions.append({
                "item_name": display,
                "normalized_name": norm_name,
                "reason": "Always-stock rule: running low",
                "priority": "high",
                "last_purchased": None,
                "in_pantry": True,
            })
            suggested_names.add(norm_name)

    # ── Pass 2: Consumption rate predictions ─────────────────────────────
    rates = compute_all_rates(user_id)
    rate_lookup = {r["normalized_name"]: r for r in rates}

    # ── Pass 3: Purchase history analysis ────────────────────────────────
    for purchase in history:
        norm_name = purchase["normalized_name"]
        if norm_name in suggested_names:
            continue

        purchase_count = purchase["purchase_count"]
        last_purchased = purchase["last_purchased"]
        first_purchased = purchase["first_purchased"]

        if purchase_count < 2:
            continue

        pantry_item = pantry_lookup.get(norm_name)

        if pantry_item is None:
            suggestions.append({
                "item_name": norm_name.title(),
                "normalized_name": norm_name,
                "reason": f"Bought {purchase_count}x but not in pantry",
                "priority": "high",
                "last_purchased": last_purchased,
                "in_pantry": False,
            })
            suggested_names.add(norm_name)
            continue

        if pantry_item.get("condition") == "nearly_empty":
            suggestions.append({
                "item_name": norm_name.title(),
                "normalized_name": norm_name,
                "reason": f"Running low ({pantry_item.get('estimated_qty', 'nearly empty')})",
                "priority": "normal",
                "last_purchased": last_purchased,
                "in_pantry": True,
            })
            suggested_names.add(norm_name)
            continue

        # Use consumption rate if available, otherwise fall back to simple interval
        rate = rate_lookup.get(norm_name)
        if rate and rate["estimated_runout_date"]:
            days_until_runout = (rate["estimated_runout_date"] - today).days
            if days_until_runout <= 3:
                suggestions.append({
                    "item_name": norm_name.title(),
                    "normalized_name": norm_name,
                    "reason": f"Predicted to run out in ~{max(days_until_runout, 0)} days",
                    "priority": "low",
                    "last_purchased": last_purchased,
                    "in_pantry": True,
                })
                suggested_names.add(norm_name)
        else:
            # Fallback: simple interval check
            if last_purchased and first_purchased and last_purchased != first_purchased:
                span_days = (last_purchased - first_purchased).days
                avg_interval = span_days / (purchase_count - 1)
                days_since = (today - last_purchased).days
                if days_since > avg_interval * 1.2:
                    suggestions.append({
                        "item_name": norm_name.title(),
                        "normalized_name": norm_name,
                        "reason": f"Usually buy every ~{avg_interval:.0f} days, last bought {days_since} days ago",
                        "priority": "low",
                        "last_purchased": last_purchased,
                        "in_pantry": True,
                    })
                    suggested_names.add(norm_name)

    # Sort by priority
    priority_order = {"high": 0, "normal": 1, "low": 2}
    suggestions.sort(key=lambda s: (priority_order.get(s["priority"], 3), s["item_name"]))

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
