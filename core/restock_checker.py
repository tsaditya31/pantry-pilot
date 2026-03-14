"""
Proactive restock checker — identifies items about to run out and creates
consolidated reminder notifications.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from core.consumption_model import compute_all_rates
from db.store import (
    get_all_user_ids,
    get_user_profile,
    insert_restock_notification,
    insert_reminder,
)

logger = logging.getLogger(__name__)

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def check_restock_for_user(user_id: int) -> Optional[str]:
    """Check if any items are predicted to run out soon for this user.

    Returns the reminder text if items found, None otherwise.
    """
    rates = compute_all_rates(user_id)
    if not rates:
        return None

    today = date.today()
    horizon = today + timedelta(days=2)
    items_running_out = []

    for rate in rates:
        if rate["confidence"] not in ("medium", "high"):
            continue
        runout = rate["estimated_runout_date"]
        if runout > horizon:
            continue

        # Dedup: skip if already notified for this runout date
        was_new = insert_restock_notification(user_id, rate["normalized_name"], runout)
        if not was_new:
            continue

        days_until = (runout - today).days
        if days_until <= 0:
            timing = "likely needed now"
        elif days_until == 1:
            timing = "likely need tomorrow"
        else:
            timing = f"likely need in {days_until} days"

        items_running_out.append(f"  - {rate['normalized_name'].title()}: {timing}")

    if not items_running_out:
        return None

    lines = ["Restock heads-up:"]
    lines.extend(items_running_out)

    # Add preferred shopping day hint if within 4 days
    profile = get_user_profile(user_id)
    pref_day = profile.get("preferred_shopping_day")
    if pref_day is not None:
        for offset in range(1, 5):
            check_date = today + timedelta(days=offset)
            if check_date.weekday() == pref_day:
                lines.append(f"\nYour usual shopping day ({_DAY_NAMES[pref_day]}) is coming up!")
                break

    return "\n".join(lines)


def run_daily_restock_check():
    """Loop all users and create restock reminders where needed."""
    user_ids = get_all_user_ids()
    count = 0
    for user_id in user_ids:
        try:
            reminder_text = check_restock_for_user(user_id)
            if reminder_text:
                # Insert as an immediately-due reminder (picked up by _check_reminders)
                insert_reminder(user_id, reminder_text, datetime.now(timezone.utc).isoformat())
                count += 1
                logger.info("Restock reminder created for user %d", user_id)
        except Exception as exc:
            logger.error("Restock check failed for user %d: %s", user_id, exc)

    logger.info("Daily restock check complete: %d reminders created for %d users", count, len(user_ids))
