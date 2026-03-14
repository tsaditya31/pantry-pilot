"""
Consumption rate modeling — exponential smoothing over purchase intervals.
"""

import logging
from datetime import date, timedelta

from db.store import (
    get_all_purchased_items,
    get_current_pantry_items,
    get_user_profile,
    upsert_consumption_rate,
)

logger = logging.getLogger(__name__)

_ALPHA = 0.3  # exponential smoothing factor — higher = more weight on recent


def compute_all_rates(user_id: int) -> list[dict]:
    """Recompute consumption rates for all items with 2+ purchases.

    Algorithm (per item):
    1. Get distinct purchase dates sorted oldest-first
    2. Compute intervals between consecutive dates
    3. Apply exponential smoothing: smoothed = α * latest + (1-α) * smoothed
    4. Confidence: low (<3 data points), medium (3-5), high (6+)
    5. Estimated runout = last_purchased + smoothed_interval / family_size
    6. Items "nearly_empty" in pantry → accelerated runout
    7. Items not in pantry → already past runout
    """
    items = get_all_purchased_items(user_id, min_purchases=2, days=365)
    if not items:
        return []

    profile = get_user_profile(user_id)
    family_size = max(profile.get("family_size", 1), 1)

    pantry = get_current_pantry_items(user_id)
    pantry_lookup = {p["normalized_name"]: p for p in pantry}

    today = date.today()
    rates = []

    for item in items:
        dates = item["purchase_dates"]
        if len(dates) < 2:
            continue

        # Compute intervals between consecutive purchase dates
        intervals = []
        for i in range(1, len(dates)):
            delta = (dates[i] - dates[i - 1]).days
            if delta > 0:
                intervals.append(delta)

        if not intervals:
            continue

        # Exponential smoothing
        smoothed = intervals[0]
        for interval in intervals[1:]:
            smoothed = _ALPHA * interval + (1 - _ALPHA) * smoothed

        # Adjust for family size
        adjusted_interval = smoothed / family_size

        # Confidence based on data points
        n = len(intervals)
        if n >= 6:
            confidence = "high"
        elif n >= 3:
            confidence = "medium"
        else:
            confidence = "low"

        # Estimate runout date
        last_purchased = item["last_purchased"]
        pantry_item = pantry_lookup.get(item["normalized_name"])

        if pantry_item is None:
            # Not in pantry — already past runout
            estimated_runout = last_purchased + timedelta(days=adjusted_interval)
        elif pantry_item.get("condition") == "nearly_empty":
            # Nearly empty — accelerated: 20% of interval remaining
            remaining = adjusted_interval * 0.2
            estimated_runout = today + timedelta(days=remaining)
        else:
            estimated_runout = last_purchased + timedelta(days=adjusted_interval)

        upsert_consumption_rate(
            user_id=user_id,
            normalized_name=item["normalized_name"],
            avg_interval_days=round(adjusted_interval, 1),
            estimated_runout_date=estimated_runout,
            confidence=confidence,
            data_points=n,
        )

        rates.append({
            "normalized_name": item["normalized_name"],
            "avg_interval_days": round(adjusted_interval, 1),
            "estimated_runout_date": estimated_runout,
            "confidence": confidence,
            "data_points": n,
        })

    logger.info("Computed consumption rates for %d items (user %d)", len(rates), user_id)
    return rates
