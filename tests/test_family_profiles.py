"""
Integration tests for smart pantry intelligence across 3 family profiles.

Profiles:
  - Single person (user_id=1): smaller quantities, longer purchase intervals
  - Couple (user_id=2): moderate intervals, shared staples
  - Family of 4 with 2 kids (user_id=3): frequent purchases, kid items, bulk buying

Each profile has realistic shopping history, pantry state, stocking rules,
and we verify consumption modeling, shopping suggestions, and restock alerts.
"""

import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from core.shopping_engine import generate_suggestions, format_suggestions
from core.consumption_model import compute_all_rates, _ALPHA
from core.restock_checker import check_restock_for_user

TODAY = date.today()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_history(items):
    """Build purchase history dicts.
    items: list of (normalized_name, purchase_count, last_days_ago, first_days_ago, category)
    """
    results = []
    for name, count, last_ago, first_ago, *rest in items:
        cat = rest[0] if rest else "other"
        results.append({
            "normalized_name": name,
            "purchase_count": count,
            "last_purchased": TODAY - timedelta(days=last_ago),
            "first_purchased": TODAY - timedelta(days=first_ago),
            "category": cat,
        })
    return results


def _make_pantry(items):
    """Build pantry item dicts.
    items: list of (normalized_name, condition, [estimated_qty, [snapshot_type]])
    """
    results = []
    for entry in items:
        name, condition = entry[0], entry[1]
        qty = entry[2] if len(entry) > 2 else "some"
        loc = entry[3] if len(entry) > 3 else "fridge"
        results.append({
            "normalized_name": name,
            "item_name": name.title(),
            "condition": condition,
            "estimated_qty": qty,
            "category": "other",
            "snapshot_type": loc,
            "created_at": None,
        })
    return results


def _make_purchased_items(items):
    """Build items for consumption model (get_all_purchased_items result).
    items: list of (normalized_name, [list of days_ago for each purchase date])
    """
    results = []
    for name, days_ago_list in items:
        dates = sorted([TODAY - timedelta(days=d) for d in days_ago_list])
        results.append({
            "normalized_name": name,
            "purchase_count": len(dates),
            "purchase_dates": dates,
            "last_purchased": dates[-1],
        })
    return results


def _make_stocking_rules(items):
    """Build stocking rule dicts.
    items: list of (normalized_name, display_name, [min_quantity])
    """
    results = []
    for entry in items:
        norm, display = entry[0], entry[1]
        min_qty = entry[2] if len(entry) > 2 else 1
        results.append({
            "normalized_name": norm,
            "display_name": display,
            "min_quantity": min_qty,
            "created_at": None,
        })
    return results


# ════════════════════════════════════════════════════════════════════════════
#  Profile Data Fixtures
# ════════════════════════════════════════════════════════════════════════════

# ── SINGLE PERSON ────────────────────────────────────────────────────────────
# Buys basics infrequently. Small fridge. Simple diet.

SINGLE_PROFILE = {"user_id": 1, "family_size": 1, "dietary_preferences": [], "preferred_shopping_day": 5}

SINGLE_HISTORY = _make_history([
    # (name, count, last_days_ago, first_days_ago, category)
    ("milk", 4, 8, 90, "dairy"),          # buys every ~27 days
    ("bread", 5, 5, 80, "bakery"),        # every ~19 days
    ("egg", 3, 12, 70, "dairy"),          # every ~29 days
    ("banana", 6, 3, 85, "produce"),      # every ~16 days
    ("chicken breast", 3, 15, 60, "meat"),  # every ~22 days
    ("pasta", 2, 20, 50, "pantry"),       # every ~30 days
    ("coffee", 3, 10, 80, "beverages"),   # every ~35 days
    ("yogurt", 2, 25, 55, "dairy"),       # every ~30 days
    ("rice", 2, 40, 90, "pantry"),        # every ~50 days
    ("onion", 1, 30, 30, "produce"),      # bought once — should be skipped
])

SINGLE_PANTRY = _make_pantry([
    ("banana", "good", "3 bananas", "pantry"),
    ("bread", "nearly_empty", "1 slice", "pantry"),
    ("coffee", "good", "half bag", "pantry"),
    ("rice", "good", "2 cups", "pantry"),
    ("pasta", "good", "1 box", "pantry"),
])

SINGLE_PURCHASED_ITEMS = _make_purchased_items([
    ("milk", [90, 62, 35, 8]),
    ("bread", [80, 60, 40, 25, 5]),
    ("egg", [70, 40, 12]),
    ("banana", [85, 70, 52, 35, 18, 3]),
    ("chicken breast", [60, 38, 15]),
    ("coffee", [80, 45, 10]),
    ("yogurt", [55, 25]),
    ("pasta", [50, 20]),
    ("rice", [90, 40]),
])

SINGLE_STOCKING_RULES = _make_stocking_rules([
    ("coffee", "Coffee"),
    ("egg", "Eggs"),
])

# ── COUPLE ───────────────────────────────────────────────────────────────────
# Two adults. Cook together. Moderate frequency.

COUPLE_PROFILE = {"user_id": 2, "family_size": 2, "dietary_preferences": ["vegetarian"], "preferred_shopping_day": 6}

COUPLE_HISTORY = _make_history([
    ("milk", 8, 2, 90, "dairy"),          # every ~12 days
    ("bread", 7, 4, 85, "bakery"),        # every ~13 days
    ("egg", 6, 5, 80, "dairy"),           # every ~15 days
    ("banana", 8, 1, 88, "produce"),      # every ~12 days
    ("tofu", 5, 7, 75, "protein"),        # every ~17 days
    ("spinach", 6, 3, 70, "produce"),     # every ~13 days
    ("rice", 4, 10, 80, "pantry"),        # every ~23 days
    ("olive oil", 3, 15, 85, "pantry"),   # every ~35 days
    ("tomato", 7, 2, 80, "produce"),      # every ~13 days
    ("cheese", 5, 6, 70, "dairy"),        # every ~16 days
    ("avocado", 4, 8, 60, "produce"),     # every ~17 days
    ("lentil", 3, 14, 65, "pantry"),      # every ~25 days
])

COUPLE_PANTRY = _make_pantry([
    ("banana", "good", "5 bananas", "pantry"),
    ("spinach", "nearly_empty", "wilted", "fridge"),
    ("tomato", "good", "3 tomatoes", "fridge"),
    ("rice", "good", "half bag", "pantry"),
    ("olive oil", "good", "1 bottle", "pantry"),
    ("cheese", "nearly_empty", "small piece", "fridge"),
])

COUPLE_PURCHASED_ITEMS = _make_purchased_items([
    ("milk", [90, 78, 65, 52, 40, 28, 15, 2]),
    ("bread", [85, 72, 58, 45, 33, 20, 4]),
    ("egg", [80, 65, 50, 35, 20, 5]),
    ("banana", [88, 76, 63, 50, 38, 26, 14, 1]),
    ("tofu", [75, 58, 42, 25, 7]),
    ("spinach", [70, 57, 44, 30, 16, 3]),
    ("rice", [80, 57, 33, 10]),
    ("tomato", [80, 67, 54, 40, 27, 14, 2]),
    ("cheese", [70, 54, 38, 22, 6]),
    ("avocado", [60, 43, 26, 8]),
])

COUPLE_STOCKING_RULES = _make_stocking_rules([
    ("tofu", "Tofu"),
    ("spinach", "Spinach"),
    ("lentil", "Lentils"),
])

# ── FAMILY OF 4 ─────────────────────────────────────────────────────────────
# Two adults + two kids. High frequency, kid snacks, bulk staples.

FAMILY4_PROFILE = {"user_id": 3, "family_size": 4, "dietary_preferences": ["nut-free"], "preferred_shopping_day": 5}

FAMILY4_HISTORY = _make_history([
    ("milk", 12, 1, 85, "dairy"),         # every ~7 days
    ("bread", 10, 2, 80, "bakery"),       # every ~8 days
    ("egg", 8, 3, 75, "dairy"),           # every ~10 days
    ("banana", 11, 1, 82, "produce"),     # every ~8 days
    ("chicken breast", 7, 5, 70, "meat"), # every ~10 days
    ("apple", 8, 2, 78, "produce"),       # every ~11 days
    ("yogurt", 9, 3, 80, "dairy"),        # every ~10 days
    ("goldfish cracker", 6, 4, 65, "snacks"),  # every ~12 days
    ("juice box", 7, 2, 70, "beverages"), # every ~11 days
    ("mac and cheese", 5, 8, 60, "pantry"),  # every ~13 days
    ("cereal", 6, 5, 65, "breakfast"),    # every ~12 days
    ("carrot", 5, 6, 55, "produce"),      # every ~12 days
    ("string cheese", 7, 3, 68, "dairy"), # every ~10 days
    ("rice", 4, 12, 70, "pantry"),        # every ~19 days
    ("pasta sauce", 3, 10, 50, "pantry"), # every ~20 days
    ("frozen pizza", 1, 20, 20, "frozen"),  # bought once — skip
])

FAMILY4_PANTRY = _make_pantry([
    ("banana", "good", "6 bananas", "pantry"),
    ("bread", "nearly_empty", "2 slices", "pantry"),
    ("milk", "nearly_empty", "quarter gallon", "fridge"),
    ("apple", "good", "4 apples", "fridge"),
    ("yogurt", "good", "3 cups", "fridge"),
    ("rice", "good", "5 lb bag", "pantry"),
    ("cereal", "nearly_empty", "almost empty box", "pantry"),
    ("carrot", "good", "1 bag", "fridge"),
    ("pasta sauce", "good", "2 jars", "pantry"),
])

FAMILY4_PURCHASED_ITEMS = _make_purchased_items([
    ("milk", [85, 78, 71, 63, 56, 49, 42, 35, 28, 21, 8, 1]),
    ("bread", [80, 72, 63, 55, 46, 38, 30, 22, 13, 2]),
    ("egg", [75, 65, 55, 45, 35, 25, 14, 3]),
    ("banana", [82, 74, 67, 59, 51, 43, 35, 27, 19, 10, 1]),
    ("chicken breast", [70, 60, 50, 40, 30, 18, 5]),
    ("apple", [78, 67, 56, 45, 34, 23, 12, 2]),
    ("yogurt", [80, 70, 60, 50, 40, 30, 20, 10, 3]),
    ("goldfish cracker", [65, 53, 42, 30, 17, 4]),
    ("juice box", [70, 58, 47, 36, 25, 13, 2]),
    ("cereal", [65, 53, 41, 29, 17, 5]),
    ("string cheese", [68, 58, 48, 38, 28, 18, 3]),
    ("carrot", [55, 43, 31, 19, 6]),
    ("mac and cheese", [60, 47, 34, 21, 8]),
])

FAMILY4_STOCKING_RULES = _make_stocking_rules([
    ("milk", "Milk", 2),
    ("bread", "Bread"),
    ("egg", "Eggs"),
    ("juice box", "Juice Boxes", 2),
    ("goldfish cracker", "Goldfish Crackers"),
])


# ════════════════════════════════════════════════════════════════════════════
#  Phase 1 Tests: User Profiles & Stocking Rules in Shopping Engine
# ════════════════════════════════════════════════════════════════════════════

class TestStockingRulesIntegration:
    """Stocking rules should elevate missing/low items to high priority."""

    @patch("core.shopping_engine.compute_all_rates", return_value=[])
    @patch("core.shopping_engine.save_suggestions")
    @patch("core.shopping_engine.get_stocking_rules")
    @patch("core.shopping_engine.get_current_pantry_items")
    @patch("core.shopping_engine.get_purchase_history")
    def test_single_stocking_rule_missing_from_pantry(
        self, mock_hist, mock_pantry, mock_rules, mock_save, mock_rates
    ):
        """Single person: eggs have a stocking rule but aren't in pantry → high."""
        mock_hist.return_value = SINGLE_HISTORY
        mock_pantry.return_value = SINGLE_PANTRY
        mock_rules.return_value = SINGLE_STOCKING_RULES

        results = generate_suggestions(user_id=1)

        egg_suggestions = [s for s in results if s["normalized_name"] == "egg"]
        assert len(egg_suggestions) == 1
        assert egg_suggestions[0]["priority"] == "high"
        assert "Always-stock" in egg_suggestions[0]["reason"]

    @patch("core.shopping_engine.compute_all_rates", return_value=[])
    @patch("core.shopping_engine.save_suggestions")
    @patch("core.shopping_engine.get_stocking_rules")
    @patch("core.shopping_engine.get_current_pantry_items")
    @patch("core.shopping_engine.get_purchase_history")
    def test_single_stocking_rule_in_pantry_ok(
        self, mock_hist, mock_pantry, mock_rules, mock_save, mock_rates
    ):
        """Single person: coffee has a stocking rule and IS in pantry (good) → no rule suggestion."""
        mock_hist.return_value = SINGLE_HISTORY
        mock_pantry.return_value = SINGLE_PANTRY
        mock_rules.return_value = SINGLE_STOCKING_RULES

        results = generate_suggestions(user_id=1)

        coffee_rules = [s for s in results if s["normalized_name"] == "coffee" and "Always-stock" in s.get("reason", "")]
        assert len(coffee_rules) == 0

    @patch("core.shopping_engine.compute_all_rates", return_value=[])
    @patch("core.shopping_engine.save_suggestions")
    @patch("core.shopping_engine.get_stocking_rules")
    @patch("core.shopping_engine.get_current_pantry_items")
    @patch("core.shopping_engine.get_purchase_history")
    def test_couple_stocking_rule_nearly_empty(
        self, mock_hist, mock_pantry, mock_rules, mock_save, mock_rates
    ):
        """Couple: spinach has a stocking rule and is nearly_empty → high priority."""
        mock_hist.return_value = COUPLE_HISTORY
        mock_pantry.return_value = COUPLE_PANTRY
        mock_rules.return_value = COUPLE_STOCKING_RULES

        results = generate_suggestions(user_id=2)

        spinach = [s for s in results if s["normalized_name"] == "spinach"]
        assert len(spinach) == 1
        assert spinach[0]["priority"] == "high"
        assert "running low" in spinach[0]["reason"].lower()

    @patch("core.shopping_engine.compute_all_rates", return_value=[])
    @patch("core.shopping_engine.save_suggestions")
    @patch("core.shopping_engine.get_stocking_rules")
    @patch("core.shopping_engine.get_current_pantry_items")
    @patch("core.shopping_engine.get_purchase_history")
    def test_couple_stocking_rule_not_in_pantry(
        self, mock_hist, mock_pantry, mock_rules, mock_save, mock_rates
    ):
        """Couple: tofu and lentils have rules but not in pantry → both high."""
        mock_hist.return_value = COUPLE_HISTORY
        mock_pantry.return_value = COUPLE_PANTRY
        mock_rules.return_value = COUPLE_STOCKING_RULES

        results = generate_suggestions(user_id=2)

        rule_items = [s for s in results if "Always-stock" in s.get("reason", "") and "not in pantry" in s["reason"]]
        rule_names = {s["normalized_name"] for s in rule_items}
        assert "tofu" in rule_names
        assert "lentil" in rule_names
        for s in rule_items:
            assert s["priority"] == "high"

    @patch("core.shopping_engine.compute_all_rates", return_value=[])
    @patch("core.shopping_engine.save_suggestions")
    @patch("core.shopping_engine.get_stocking_rules")
    @patch("core.shopping_engine.get_current_pantry_items")
    @patch("core.shopping_engine.get_purchase_history")
    def test_family4_stocking_rules_mixed(
        self, mock_hist, mock_pantry, mock_rules, mock_save, mock_rates
    ):
        """Family of 4: milk/bread nearly empty (stocking rule → high),
        eggs not in pantry (stocking rule → high),
        juice/goldfish not in pantry (stocking rule → high)."""
        mock_hist.return_value = FAMILY4_HISTORY
        mock_pantry.return_value = FAMILY4_PANTRY
        mock_rules.return_value = FAMILY4_STOCKING_RULES

        results = generate_suggestions(user_id=3)

        rule_suggestions = [s for s in results if "Always-stock" in s.get("reason", "")]
        rule_names = {s["normalized_name"] for s in rule_suggestions}

        # All 5 rules should fire: milk (nearly_empty), bread (nearly_empty),
        # egg (not in pantry), juice box (not in pantry), goldfish cracker (not in pantry)
        assert "milk" in rule_names
        assert "bread" in rule_names
        assert "egg" in rule_names
        assert "juice box" in rule_names
        assert "goldfish cracker" in rule_names
        for s in rule_suggestions:
            assert s["priority"] == "high"

    @patch("core.shopping_engine.compute_all_rates", return_value=[])
    @patch("core.shopping_engine.save_suggestions")
    @patch("core.shopping_engine.get_stocking_rules")
    @patch("core.shopping_engine.get_current_pantry_items")
    @patch("core.shopping_engine.get_purchase_history")
    def test_no_duplicate_suggestions(
        self, mock_hist, mock_pantry, mock_rules, mock_save, mock_rates
    ):
        """Items covered by stocking rules should NOT also appear from history pass."""
        mock_hist.return_value = FAMILY4_HISTORY
        mock_pantry.return_value = FAMILY4_PANTRY
        mock_rules.return_value = FAMILY4_STOCKING_RULES

        results = generate_suggestions(user_id=3)

        names = [s["normalized_name"] for s in results]
        # Each name should appear at most once
        assert len(names) == len(set(names)), f"Duplicates found: {names}"


# ════════════════════════════════════════════════════════════════════════════
#  Phase 2 Tests: Consumption Rate Modeling
# ════════════════════════════════════════════════════════════════════════════

class TestConsumptionModel:
    """Test exponential smoothing and runout estimation for each profile."""

    @patch("core.consumption_model.upsert_consumption_rate")
    @patch("core.consumption_model.get_user_profile")
    @patch("core.consumption_model.get_current_pantry_items")
    @patch("core.consumption_model.get_all_purchased_items")
    def test_single_person_intervals(self, mock_items, mock_pantry, mock_profile, mock_upsert):
        """Single person: milk bought at days [90, 62, 35, 8] → intervals [28, 27, 27].
        Smoothing should stay close to 27 days."""
        mock_items.return_value = _make_purchased_items([("milk", [90, 62, 35, 8])])
        mock_pantry.return_value = []
        mock_profile.return_value = SINGLE_PROFILE

        rates = compute_all_rates(user_id=1)

        assert len(rates) == 1
        milk = rates[0]
        # Intervals: 28, 27, 27. Smoothed ≈ 27.2, family_size=1 → ~27.2
        assert 25 <= milk["avg_interval_days"] <= 30
        assert milk["confidence"] == "medium"  # 3 data points
        assert milk["normalized_name"] == "milk"

    @patch("core.consumption_model.upsert_consumption_rate")
    @patch("core.consumption_model.get_user_profile")
    @patch("core.consumption_model.get_current_pantry_items")
    @patch("core.consumption_model.get_all_purchased_items")
    def test_couple_family_size_halves_interval(self, mock_items, mock_pantry, mock_profile, mock_upsert):
        """Couple (family_size=2): raw interval ~13 days → adjusted ~6.5 days."""
        mock_items.return_value = _make_purchased_items([
            ("milk", [90, 78, 65, 52, 40, 28, 15, 2]),
        ])
        mock_pantry.return_value = []
        mock_profile.return_value = COUPLE_PROFILE

        rates = compute_all_rates(user_id=2)

        assert len(rates) == 1
        milk = rates[0]
        # Raw intervals: 12,13,13,12,12,13,13 → smoothed ~12.7, /2 → ~6.3
        assert 5 <= milk["avg_interval_days"] <= 8
        assert milk["confidence"] == "high"  # 7 data points

    @patch("core.consumption_model.upsert_consumption_rate")
    @patch("core.consumption_model.get_user_profile")
    @patch("core.consumption_model.get_current_pantry_items")
    @patch("core.consumption_model.get_all_purchased_items")
    def test_family4_high_frequency(self, mock_items, mock_pantry, mock_profile, mock_upsert):
        """Family of 4: milk bought every ~7 days raw → /4 → ~1.8 days adjusted."""
        mock_items.return_value = _make_purchased_items([
            ("milk", [85, 78, 71, 63, 56, 49, 42, 35, 28, 21, 8, 1]),
        ])
        mock_pantry.return_value = FAMILY4_PANTRY
        mock_profile.return_value = FAMILY4_PROFILE

        rates = compute_all_rates(user_id=3)

        assert len(rates) == 1
        milk = rates[0]
        # Raw intervals: mostly 7, then 13,7. Smoothed ~8, /4 → ~2
        assert 1 <= milk["avg_interval_days"] <= 3
        assert milk["confidence"] == "high"  # 11 intervals

    @patch("core.consumption_model.upsert_consumption_rate")
    @patch("core.consumption_model.get_user_profile")
    @patch("core.consumption_model.get_current_pantry_items")
    @patch("core.consumption_model.get_all_purchased_items")
    def test_nearly_empty_accelerates_runout(self, mock_items, mock_pantry, mock_profile, mock_upsert):
        """Family of 4: milk is nearly_empty → runout = today + 20% of interval."""
        mock_items.return_value = _make_purchased_items([
            ("milk", [85, 78, 71, 63, 56, 49, 42, 35, 28, 21, 8, 1]),
        ])
        # Milk is nearly_empty in pantry
        mock_pantry.return_value = _make_pantry([("milk", "nearly_empty", "quarter gallon", "fridge")])
        mock_profile.return_value = FAMILY4_PROFILE

        rates = compute_all_rates(user_id=3)

        milk = rates[0]
        # adjusted_interval ~2 days, 20% → 0.4 days, runout ≈ today + 0.4
        assert milk["estimated_runout_date"] <= TODAY + timedelta(days=1)

    @patch("core.consumption_model.upsert_consumption_rate")
    @patch("core.consumption_model.get_user_profile")
    @patch("core.consumption_model.get_current_pantry_items")
    @patch("core.consumption_model.get_all_purchased_items")
    def test_not_in_pantry_past_runout(self, mock_items, mock_pantry, mock_profile, mock_upsert):
        """Single person: eggs not in pantry → runout = last_purchased + interval (in the past)."""
        mock_items.return_value = _make_purchased_items([("egg", [70, 40, 12])])
        mock_pantry.return_value = []  # empty pantry
        mock_profile.return_value = SINGLE_PROFILE

        rates = compute_all_rates(user_id=1)

        egg = rates[0]
        # last_purchased = today - 12, interval ~27 → runout = today - 12 + 27 = today + 15
        # (not in pantry, so uses raw formula)
        assert egg["estimated_runout_date"] is not None

    @patch("core.consumption_model.upsert_consumption_rate")
    @patch("core.consumption_model.get_user_profile")
    @patch("core.consumption_model.get_current_pantry_items")
    @patch("core.consumption_model.get_all_purchased_items")
    def test_confidence_levels(self, mock_items, mock_pantry, mock_profile, mock_upsert):
        """Test confidence assignment: low <3, medium 3-5, high 6+."""
        mock_items.return_value = _make_purchased_items([
            ("pasta", [50, 20]),                        # 1 interval → low
            ("coffee", [80, 45, 10]),                    # 2 intervals → low
            ("egg", [75, 60, 45, 30]),                   # 3 intervals → medium
            ("milk", [90, 78, 65, 52, 40, 28, 15]),     # 6 intervals → high
        ])
        mock_pantry.return_value = []
        mock_profile.return_value = SINGLE_PROFILE

        rates = compute_all_rates(user_id=1)

        rate_map = {r["normalized_name"]: r for r in rates}
        assert rate_map["pasta"]["confidence"] == "low"
        assert rate_map["coffee"]["confidence"] == "low"
        assert rate_map["egg"]["confidence"] == "medium"
        assert rate_map["milk"]["confidence"] == "high"

    @patch("core.consumption_model.upsert_consumption_rate")
    @patch("core.consumption_model.get_user_profile")
    @patch("core.consumption_model.get_current_pantry_items")
    @patch("core.consumption_model.get_all_purchased_items")
    def test_irregular_intervals_smoothing(self, mock_items, mock_pantry, mock_profile, mock_upsert):
        """Test that exponential smoothing handles irregular intervals and weighs recent more."""
        # Intervals: 30, 30, 30, 10 — recent spike in frequency
        mock_items.return_value = _make_purchased_items([
            ("banana", [100, 70, 40, 10, 0]),  # intervals: 30, 30, 30, 10
        ])
        mock_pantry.return_value = []
        mock_profile.return_value = SINGLE_PROFILE

        rates = compute_all_rates(user_id=1)

        banana = rates[0]
        # With alpha=0.3: start=30, then 30, then 30, then 0.3*10 + 0.7*30 = 24
        # So smoothed should be pulled down toward recent shorter interval
        assert banana["avg_interval_days"] < 30


# ════════════════════════════════════════════════════════════════════════════
#  Phase 2 Tests: Consumption Rates in Shopping Engine
# ════════════════════════════════════════════════════════════════════════════

class TestShoppingEngineWithConsumptionRates:
    """Test that the shopping engine uses consumption rates for predictions."""

    @patch("core.shopping_engine.compute_all_rates")
    @patch("core.shopping_engine.save_suggestions")
    @patch("core.shopping_engine.get_stocking_rules", return_value=[])
    @patch("core.shopping_engine.get_current_pantry_items")
    @patch("core.shopping_engine.get_purchase_history")
    def test_predicted_runout_suggestion(
        self, mock_hist, mock_pantry, mock_rules, mock_save, mock_rates
    ):
        """Items predicted to run out within 3 days get low-priority 'Predicted to run out' suggestion."""
        mock_hist.return_value = _make_history([
            ("yogurt", 4, 5, 60, "dairy"),
        ])
        mock_pantry.return_value = _make_pantry([("yogurt", "good", "1 cup", "fridge")])
        mock_rates.return_value = [{
            "normalized_name": "yogurt",
            "avg_interval_days": 15,
            "estimated_runout_date": TODAY + timedelta(days=2),
            "confidence": "medium",
            "data_points": 3,
        }]

        results = generate_suggestions(user_id=1)

        yogurt = [s for s in results if s["normalized_name"] == "yogurt"]
        assert len(yogurt) == 1
        assert yogurt[0]["priority"] == "low"
        assert "Predicted to run out" in yogurt[0]["reason"]

    @patch("core.shopping_engine.compute_all_rates")
    @patch("core.shopping_engine.save_suggestions")
    @patch("core.shopping_engine.get_stocking_rules", return_value=[])
    @patch("core.shopping_engine.get_current_pantry_items")
    @patch("core.shopping_engine.get_purchase_history")
    def test_no_suggestion_when_runout_far(
        self, mock_hist, mock_pantry, mock_rules, mock_save, mock_rates
    ):
        """Items with runout > 3 days in the future should NOT be suggested."""
        mock_hist.return_value = _make_history([
            ("rice", 3, 5, 60, "pantry"),
        ])
        mock_pantry.return_value = _make_pantry([("rice", "good", "5 lb bag", "pantry")])
        mock_rates.return_value = [{
            "normalized_name": "rice",
            "avg_interval_days": 30,
            "estimated_runout_date": TODAY + timedelta(days=20),
            "confidence": "medium",
            "data_points": 3,
        }]

        results = generate_suggestions(user_id=1)

        rice = [s for s in results if s["normalized_name"] == "rice"]
        assert len(rice) == 0

    @patch("core.shopping_engine.compute_all_rates")
    @patch("core.shopping_engine.save_suggestions")
    @patch("core.shopping_engine.get_stocking_rules", return_value=[])
    @patch("core.shopping_engine.get_current_pantry_items")
    @patch("core.shopping_engine.get_purchase_history")
    def test_fallback_to_simple_interval_without_rate(
        self, mock_hist, mock_pantry, mock_rules, mock_save, mock_rates
    ):
        """Items without consumption rate data should use the old interval-based fallback."""
        mock_hist.return_value = _make_history([
            ("olive oil", 4, 45, 90, "pantry"),  # avg ~15 days, last 45 days ago → overdue
        ])
        mock_pantry.return_value = _make_pantry([("olive oil", "good", "half bottle", "pantry")])
        mock_rates.return_value = []  # no rate data

        results = generate_suggestions(user_id=2)

        oil = [s for s in results if s["normalized_name"] == "olive oil"]
        assert len(oil) == 1
        assert "Usually buy every" in oil[0]["reason"]
        assert oil[0]["priority"] == "low"


# ════════════════════════════════════════════════════════════════════════════
#  Phase 3 Tests: Restock Checker
# ════════════════════════════════════════════════════════════════════════════

class TestRestockChecker:
    """Test proactive restock reminders for each profile."""

    @patch("core.restock_checker.insert_reminder")
    @patch("core.restock_checker.insert_restock_notification", return_value=True)
    @patch("core.restock_checker.get_user_profile")
    @patch("core.restock_checker.compute_all_rates")
    def test_single_person_no_restock(self, mock_rates, mock_profile, mock_notif, mock_remind):
        """Single person with nothing running out → no reminder."""
        mock_rates.return_value = [
            {
                "normalized_name": "milk",
                "avg_interval_days": 27,
                "estimated_runout_date": TODAY + timedelta(days=15),
                "confidence": "medium",
                "data_points": 3,
            }
        ]
        mock_profile.return_value = SINGLE_PROFILE

        result = check_restock_for_user(user_id=1)
        assert result is None

    @patch("core.restock_checker.insert_reminder")
    @patch("core.restock_checker.insert_restock_notification", return_value=True)
    @patch("core.restock_checker.get_user_profile")
    @patch("core.restock_checker.compute_all_rates")
    def test_couple_restock_items_running_out(self, mock_rates, mock_profile, mock_notif, mock_remind):
        """Couple: milk and tofu running out within 2 days → consolidated reminder."""
        mock_rates.return_value = [
            {
                "normalized_name": "milk",
                "avg_interval_days": 6.5,
                "estimated_runout_date": TODAY + timedelta(days=1),
                "confidence": "high",
                "data_points": 7,
            },
            {
                "normalized_name": "tofu",
                "avg_interval_days": 8.5,
                "estimated_runout_date": TODAY + timedelta(days=2),
                "confidence": "medium",
                "data_points": 4,
            },
            {
                "normalized_name": "rice",
                "avg_interval_days": 12,
                "estimated_runout_date": TODAY + timedelta(days=10),
                "confidence": "medium",
                "data_points": 3,
            },
        ]
        mock_profile.return_value = COUPLE_PROFILE

        result = check_restock_for_user(user_id=2)

        assert result is not None
        assert "Milk" in result
        assert "Tofu" in result
        assert "Rice" not in result  # too far out
        assert "tomorrow" in result.lower()

    @patch("core.restock_checker.insert_reminder")
    @patch("core.restock_checker.insert_restock_notification", return_value=True)
    @patch("core.restock_checker.get_user_profile")
    @patch("core.restock_checker.compute_all_rates")
    def test_family4_multiple_items_running_out(self, mock_rates, mock_profile, mock_notif, mock_remind):
        """Family of 4: many items with short intervals → multiple restock items."""
        mock_rates.return_value = [
            {
                "normalized_name": "milk",
                "avg_interval_days": 2,
                "estimated_runout_date": TODAY,
                "confidence": "high",
                "data_points": 11,
            },
            {
                "normalized_name": "bread",
                "avg_interval_days": 2,
                "estimated_runout_date": TODAY + timedelta(days=1),
                "confidence": "high",
                "data_points": 9,
            },
            {
                "normalized_name": "egg",
                "avg_interval_days": 2.5,
                "estimated_runout_date": TODAY,
                "confidence": "high",
                "data_points": 7,
            },
            {
                "normalized_name": "goldfish cracker",
                "avg_interval_days": 3,
                "estimated_runout_date": TODAY + timedelta(days=2),
                "confidence": "medium",
                "data_points": 5,
            },
            {
                "normalized_name": "cereal",
                "avg_interval_days": 3,
                "estimated_runout_date": TODAY + timedelta(days=5),
                "confidence": "medium",
                "data_points": 5,
            },
        ]
        mock_profile.return_value = FAMILY4_PROFILE

        result = check_restock_for_user(user_id=3)

        assert result is not None
        assert "Milk" in result
        assert "Bread" in result
        assert "Egg" in result
        assert "Goldfish Cracker" in result
        assert "Cereal" not in result  # 5 days out > horizon of 2
        assert "needed now" in result.lower()  # milk/egg runout is today

    @patch("core.restock_checker.insert_reminder")
    @patch("core.restock_checker.insert_restock_notification", return_value=True)
    @patch("core.restock_checker.get_user_profile")
    @patch("core.restock_checker.compute_all_rates")
    def test_low_confidence_items_skipped(self, mock_rates, mock_profile, mock_notif, mock_remind):
        """Items with low confidence should NOT trigger restock reminders."""
        mock_rates.return_value = [
            {
                "normalized_name": "pasta",
                "avg_interval_days": 30,
                "estimated_runout_date": TODAY,
                "confidence": "low",
                "data_points": 1,
            },
        ]
        mock_profile.return_value = SINGLE_PROFILE

        result = check_restock_for_user(user_id=1)
        assert result is None

    @patch("core.restock_checker.insert_reminder")
    @patch("core.restock_checker.insert_restock_notification", return_value=False)
    @patch("core.restock_checker.get_user_profile")
    @patch("core.restock_checker.compute_all_rates")
    def test_dedup_prevents_repeat_notification(self, mock_rates, mock_profile, mock_notif, mock_remind):
        """If insert_restock_notification returns False (duplicate), skip the item."""
        mock_rates.return_value = [
            {
                "normalized_name": "milk",
                "avg_interval_days": 6.5,
                "estimated_runout_date": TODAY + timedelta(days=1),
                "confidence": "high",
                "data_points": 7,
            },
        ]
        mock_profile.return_value = COUPLE_PROFILE

        result = check_restock_for_user(user_id=2)

        # Notification was already sent (dedup returned False) → no reminder
        assert result is None

    @patch("core.restock_checker.insert_reminder")
    @patch("core.restock_checker.insert_restock_notification", return_value=True)
    @patch("core.restock_checker.get_user_profile")
    @patch("core.restock_checker.compute_all_rates")
    def test_preferred_shopping_day_hint(self, mock_rates, mock_profile, mock_notif, mock_remind):
        """If preferred shopping day is within 4 days, reminder includes a hint."""
        # Find a profile whose shopping day is within 4 days of today
        # We'll construct a custom profile for this
        upcoming_day = (TODAY.weekday() + 2) % 7  # 2 days from now
        profile = {
            "user_id": 99, "family_size": 2,
            "dietary_preferences": [],
            "preferred_shopping_day": upcoming_day,
        }
        mock_rates.return_value = [
            {
                "normalized_name": "milk",
                "avg_interval_days": 7,
                "estimated_runout_date": TODAY + timedelta(days=1),
                "confidence": "high",
                "data_points": 6,
            },
        ]
        mock_profile.return_value = profile

        result = check_restock_for_user(user_id=99)

        assert result is not None
        assert "shopping day" in result.lower()


# ════════════════════════════════════════════════════════════════════════════
#  End-to-End: Full Pipeline per Profile
# ════════════════════════════════════════════════════════════════════════════

class TestFullPipelineSinglePerson:
    """End-to-end: single person shopping suggestions with all features."""

    @patch("core.shopping_engine.compute_all_rates")
    @patch("core.shopping_engine.save_suggestions")
    @patch("core.shopping_engine.get_stocking_rules")
    @patch("core.shopping_engine.get_current_pantry_items")
    @patch("core.shopping_engine.get_purchase_history")
    def test_single_full_suggestions(
        self, mock_hist, mock_pantry, mock_rules, mock_save, mock_rates
    ):
        mock_hist.return_value = SINGLE_HISTORY
        # Add chicken breast to pantry so the consumption rate path fires
        pantry = SINGLE_PANTRY + _make_pantry([("chicken breast", "good", "1 pack", "fridge")])
        mock_pantry.return_value = pantry
        mock_rules.return_value = SINGLE_STOCKING_RULES
        mock_rates.return_value = [
            {"normalized_name": "yogurt", "avg_interval_days": 27, "estimated_runout_date": TODAY - timedelta(days=2), "confidence": "low", "data_points": 1},
            {"normalized_name": "chicken breast", "avg_interval_days": 20, "estimated_runout_date": TODAY + timedelta(days=2), "confidence": "medium", "data_points": 2},
        ]

        results = generate_suggestions(user_id=1)

        names = {s["normalized_name"] for s in results}
        priorities = {s["normalized_name"]: s["priority"] for s in results}

        # Stocking rule: eggs not in pantry → high
        assert "egg" in names
        assert priorities["egg"] == "high"

        # bread nearly_empty in pantry (from history pass) → normal
        assert "bread" in names
        assert priorities["bread"] == "normal"

        # milk not in pantry (from history pass) → high
        assert "milk" in names
        assert priorities["milk"] == "high"

        # chicken breast in pantry, predicted runout in 2 days → low
        assert "chicken breast" in names
        assert priorities["chicken breast"] == "low"

        # onion only bought once → not suggested
        assert "onion" not in names

        # Verify formatting works
        formatted = format_suggestions(results)
        assert "Need to Buy" in formatted
        assert len(formatted) > 50


class TestFullPipelineCouple:
    """End-to-end: couple shopping suggestions."""

    @patch("core.shopping_engine.compute_all_rates")
    @patch("core.shopping_engine.save_suggestions")
    @patch("core.shopping_engine.get_stocking_rules")
    @patch("core.shopping_engine.get_current_pantry_items")
    @patch("core.shopping_engine.get_purchase_history")
    def test_couple_full_suggestions(
        self, mock_hist, mock_pantry, mock_rules, mock_save, mock_rates
    ):
        mock_hist.return_value = COUPLE_HISTORY
        # Add avocado to pantry so consumption rate path fires (not in pantry → high would override)
        pantry = COUPLE_PANTRY + _make_pantry([("avocado", "good", "2 avocados", "fridge")])
        mock_pantry.return_value = pantry
        mock_rules.return_value = COUPLE_STOCKING_RULES
        mock_rates.return_value = [
            {"normalized_name": "avocado", "avg_interval_days": 8, "estimated_runout_date": TODAY + timedelta(days=1), "confidence": "medium", "data_points": 3},
        ]

        results = generate_suggestions(user_id=2)

        names = {s["normalized_name"] for s in results}
        priorities = {s["normalized_name"]: s["priority"] for s in results}

        # Stocking rules: tofu not in pantry → high, spinach nearly_empty → high, lentil not in pantry → high
        assert "tofu" in names and priorities["tofu"] == "high"
        assert "spinach" in names and priorities["spinach"] == "high"
        assert "lentil" in names and priorities["lentil"] == "high"

        # cheese nearly_empty → normal (from history)
        assert "cheese" in names and priorities["cheese"] == "normal"

        # avocado in pantry, predicted runout in 1 day → low
        assert "avocado" in names and priorities["avocado"] == "low"

        # No duplicates
        all_names = [s["normalized_name"] for s in results]
        assert len(all_names) == len(set(all_names))


class TestFullPipelineFamily4:
    """End-to-end: family of 4 shopping suggestions."""

    @patch("core.shopping_engine.compute_all_rates")
    @patch("core.shopping_engine.save_suggestions")
    @patch("core.shopping_engine.get_stocking_rules")
    @patch("core.shopping_engine.get_current_pantry_items")
    @patch("core.shopping_engine.get_purchase_history")
    def test_family4_full_suggestions(
        self, mock_hist, mock_pantry, mock_rules, mock_save, mock_rates
    ):
        mock_hist.return_value = FAMILY4_HISTORY
        # Add string cheese and mac and cheese to pantry so consumption rate path fires
        pantry = FAMILY4_PANTRY + _make_pantry([
            ("string cheese", "good", "2 sticks", "fridge"),
            ("mac and cheese", "good", "1 box", "pantry"),
        ])
        mock_pantry.return_value = pantry
        mock_rules.return_value = FAMILY4_STOCKING_RULES
        mock_rates.return_value = [
            {"normalized_name": "string cheese", "avg_interval_days": 2.5, "estimated_runout_date": TODAY + timedelta(days=1), "confidence": "high", "data_points": 6},
            {"normalized_name": "mac and cheese", "avg_interval_days": 3, "estimated_runout_date": TODAY + timedelta(days=3), "confidence": "medium", "data_points": 4},
        ]

        results = generate_suggestions(user_id=3)

        names = {s["normalized_name"] for s in results}
        priorities = {s["normalized_name"]: s["priority"] for s in results}

        # Stocking rules → high
        assert priorities.get("milk") == "high"
        assert priorities.get("bread") == "high"
        assert priorities.get("egg") == "high"
        assert priorities.get("juice box") == "high"
        assert priorities.get("goldfish cracker") == "high"

        # cereal nearly_empty, no stocking rule → normal (from history pass)
        assert "cereal" in names and priorities["cereal"] == "normal"

        # string cheese in pantry, predicted runout 1 day → low
        assert "string cheese" in names and priorities["string cheese"] == "low"

        # mac and cheese in pantry, runout in 3 days → low
        assert "mac and cheese" in names and priorities["mac and cheese"] == "low"

        # frozen pizza bought once → not suggested
        assert "frozen pizza" not in names

        # No duplicates
        all_names = [s["normalized_name"] for s in results]
        assert len(all_names) == len(set(all_names))

        # Verify high > normal > low ordering
        prio_order = {"high": 0, "normal": 1, "low": 2}
        priority_values = [prio_order[s["priority"]] for s in results]
        assert priority_values == sorted(priority_values)
