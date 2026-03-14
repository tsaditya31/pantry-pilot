"""Tests for shopping engine suggestion logic."""

import pytest
from datetime import date, timedelta
from unittest.mock import patch

from core.shopping_engine import generate_suggestions, format_suggestions


def _make_history(items):
    """Helper to create purchase history dicts."""
    results = []
    for name, count, last_days_ago, first_days_ago in items:
        today = date.today()
        results.append({
            "normalized_name": name,
            "purchase_count": count,
            "last_purchased": today - timedelta(days=last_days_ago),
            "first_purchased": today - timedelta(days=first_days_ago),
            "category": "other",
        })
    return results


def _make_pantry(items):
    """Helper to create pantry item dicts."""
    results = []
    for name, condition in items:
        results.append({
            "normalized_name": name,
            "item_name": name.title(),
            "condition": condition,
            "estimated_qty": "some",
            "category": "other",
            "snapshot_type": "pantry",
            "created_at": None,
        })
    return results


@patch("core.shopping_engine.compute_all_rates", return_value=[])
@patch("core.shopping_engine.save_suggestions")
@patch("core.shopping_engine.get_stocking_rules", return_value=[])
@patch("core.shopping_engine.get_current_pantry_items")
@patch("core.shopping_engine.get_purchase_history")
class TestGenerateSuggestions:
    def test_item_not_in_pantry_high_priority(self, mock_history, mock_pantry, mock_rules, mock_save, mock_rates):
        mock_history.return_value = _make_history([
            ("milk", 5, 3, 60),
        ])
        mock_pantry.return_value = _make_pantry([])

        results = generate_suggestions(user_id=1)

        assert len(results) == 1
        assert results[0]["priority"] == "high"
        assert results[0]["normalized_name"] == "milk"

    def test_nearly_empty_normal_priority(self, mock_history, mock_pantry, mock_rules, mock_save, mock_rates):
        mock_history.return_value = _make_history([
            ("milk", 5, 3, 60),
        ])
        mock_pantry.return_value = _make_pantry([
            ("milk", "nearly_empty"),
        ])

        results = generate_suggestions(user_id=1)

        assert len(results) == 1
        assert results[0]["priority"] == "normal"

    def test_overdue_low_priority(self, mock_history, mock_pantry, mock_rules, mock_save, mock_rates):
        # Bought 4x over 60 days → avg interval ~20 days
        # Last bought 30 days ago → overdue (30 > 20 * 1.2 = 24)
        mock_history.return_value = _make_history([
            ("eggs", 4, 30, 90),
        ])
        mock_pantry.return_value = _make_pantry([
            ("eggs", "good"),
        ])

        results = generate_suggestions(user_id=1)

        assert len(results) == 1
        assert results[0]["priority"] == "low"

    def test_skip_single_purchase(self, mock_history, mock_pantry, mock_rules, mock_save, mock_rates):
        mock_history.return_value = _make_history([
            ("caviar", 1, 10, 10),
        ])
        mock_pantry.return_value = _make_pantry([])

        results = generate_suggestions(user_id=1)

        assert len(results) == 0

    def test_in_pantry_not_overdue_no_suggestion(self, mock_history, mock_pantry, mock_rules, mock_save, mock_rates):
        # Bought 3x over 60 days → avg interval ~30 days
        # Last bought 5 days ago → not overdue
        mock_history.return_value = _make_history([
            ("butter", 3, 5, 65),
        ])
        mock_pantry.return_value = _make_pantry([
            ("butter", "good"),
        ])

        results = generate_suggestions(user_id=1)

        assert len(results) == 0

    def test_priority_sorting(self, mock_history, mock_pantry, mock_rules, mock_save, mock_rates):
        mock_history.return_value = _make_history([
            ("bread", 3, 3, 60),
            ("milk", 5, 30, 90),
            ("eggs", 4, 3, 60),
        ])
        mock_pantry.return_value = _make_pantry([
            ("milk", "nearly_empty"),
        ])

        results = generate_suggestions(user_id=1)

        priorities = [r["priority"] for r in results]
        assert priorities == ["high", "high", "normal"]

    def test_saves_suggestions(self, mock_history, mock_pantry, mock_rules, mock_save, mock_rates):
        mock_history.return_value = _make_history([
            ("milk", 3, 3, 60),
        ])
        mock_pantry.return_value = _make_pantry([])

        generate_suggestions(user_id=1)

        mock_save.assert_called_once()


class TestFormatSuggestions:
    def test_empty_suggestions(self):
        result = format_suggestions([])
        assert "No shopping suggestions" in result

    def test_formats_with_priorities(self):
        suggestions = [
            {"item_name": "Milk", "priority": "high", "reason": "Not in pantry", "normalized_name": "milk"},
            {"item_name": "Eggs", "priority": "normal", "reason": "Running low", "normalized_name": "eggs"},
        ]
        result = format_suggestions(suggestions)
        assert "Milk" in result
        assert "Eggs" in result
        assert "Need to Buy" in result
        assert "Running Low" in result
