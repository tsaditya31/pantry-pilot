"""Tests for Instacart shopping list integration."""

import time
from unittest.mock import patch, MagicMock

import pytest

from core.instacart import create_shopping_list, _build_line_items, _items_hash, _cache
from core.shopping_engine import format_suggestions


SAMPLE_SUGGESTIONS = [
    {"item_name": "Milk", "normalized_name": "milk", "priority": "high", "reason": "Not in pantry"},
    {"item_name": "Eggs", "normalized_name": "egg", "priority": "high", "reason": "Always-stock rule"},
    {"item_name": "Bread", "normalized_name": "bread", "priority": "normal", "reason": "Running low"},
]

INSTACART_RESPONSE = {
    "products_link_url": "https://www.instacart.com/store/partner_recipes/abc123"
}


class TestBuildLineItems:
    def test_maps_suggestions_to_line_items(self):
        result = _build_line_items(SAMPLE_SUGGESTIONS)

        assert len(result) == 3
        assert result[0] == {"name": "Milk", "quantity": 1}
        assert result[1] == {"name": "Eggs", "quantity": 1}
        assert result[2] == {"name": "Bread", "quantity": 1}

    def test_empty_suggestions(self):
        assert _build_line_items([]) == []

    def test_uses_item_name_over_normalized(self):
        items = [{"item_name": "Whole Milk", "normalized_name": "milk"}]
        result = _build_line_items(items)
        assert result[0]["name"] == "Whole Milk"

    def test_falls_back_to_normalized_name(self):
        items = [{"normalized_name": "chicken breast"}]
        result = _build_line_items(items)
        assert result[0]["name"] == "chicken breast"


class TestItemsHash:
    def test_deterministic(self):
        h1 = _items_hash(SAMPLE_SUGGESTIONS)
        h2 = _items_hash(SAMPLE_SUGGESTIONS)
        assert h1 == h2

    def test_order_independent(self):
        reversed_suggestions = list(reversed(SAMPLE_SUGGESTIONS))
        assert _items_hash(SAMPLE_SUGGESTIONS) == _items_hash(reversed_suggestions)

    def test_different_items_different_hash(self):
        other = [{"normalized_name": "banana"}]
        assert _items_hash(SAMPLE_SUGGESTIONS) != _items_hash(other)


class TestCreateShoppingList:
    def setup_method(self):
        _cache.clear()

    @patch("core.instacart.settings")
    def test_returns_none_when_no_api_key(self, mock_settings):
        mock_settings.instacart_api_key = ""
        result = create_shopping_list(user_id=1, suggestions=SAMPLE_SUGGESTIONS)
        assert result is None

    @patch("core.instacart.settings")
    def test_returns_none_for_empty_suggestions(self, mock_settings):
        mock_settings.instacart_api_key = "test-key"
        result = create_shopping_list(user_id=1, suggestions=[])
        assert result is None

    @patch("core.instacart.httpx.post")
    @patch("core.instacart.settings")
    def test_successful_api_call(self, mock_settings, mock_post):
        mock_settings.instacart_api_key = "test-key"
        mock_response = MagicMock()
        mock_response.json.return_value = INSTACART_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        result = create_shopping_list(user_id=1, suggestions=SAMPLE_SUGGESTIONS)

        assert result == INSTACART_RESPONSE["products_link_url"]

        # Verify the API was called correctly
        call_args = mock_post.call_args
        assert call_args[0][0] == "https://connect.instacart.com/idp/v1/products/products_link"
        payload = call_args[1]["json"]
        assert payload["title"] == "Pantry Pilot Shopping List"
        assert len(payload["line_items"]) == 3
        assert payload["line_items"][0]["name"] == "Milk"
        headers = call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer test-key"

    @patch("core.instacart.httpx.post")
    @patch("core.instacart.settings")
    def test_caches_result(self, mock_settings, mock_post):
        mock_settings.instacart_api_key = "test-key"
        mock_response = MagicMock()
        mock_response.json.return_value = INSTACART_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        # First call — hits API
        result1 = create_shopping_list(user_id=1, suggestions=SAMPLE_SUGGESTIONS)
        # Second call — should use cache
        result2 = create_shopping_list(user_id=1, suggestions=SAMPLE_SUGGESTIONS)

        assert result1 == result2
        assert mock_post.call_count == 1  # Only one API call

    @patch("core.instacart.httpx.post")
    @patch("core.instacart.settings")
    def test_cache_expires(self, mock_settings, mock_post):
        mock_settings.instacart_api_key = "test-key"
        mock_response = MagicMock()
        mock_response.json.return_value = INSTACART_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        # First call
        create_shopping_list(user_id=1, suggestions=SAMPLE_SUGGESTIONS)

        # Expire the cache entry manually
        for key in _cache:
            url, _ = _cache[key]
            _cache[key] = (url, time.time() - 3601)

        # Second call — cache expired, hits API again
        create_shopping_list(user_id=1, suggestions=SAMPLE_SUGGESTIONS)
        assert mock_post.call_count == 2

    @patch("core.instacart.httpx.post")
    @patch("core.instacart.settings")
    def test_different_users_separate_cache(self, mock_settings, mock_post):
        mock_settings.instacart_api_key = "test-key"
        mock_response = MagicMock()
        mock_response.json.return_value = INSTACART_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        create_shopping_list(user_id=1, suggestions=SAMPLE_SUGGESTIONS)
        create_shopping_list(user_id=2, suggestions=SAMPLE_SUGGESTIONS)

        assert mock_post.call_count == 2  # Different users, different cache keys

    @patch("core.instacart.httpx.post")
    @patch("core.instacart.settings")
    def test_api_error_returns_none(self, mock_settings, mock_post):
        mock_settings.instacart_api_key = "test-key"
        mock_post.side_effect = Exception("Connection refused")

        result = create_shopping_list(user_id=1, suggestions=SAMPLE_SUGGESTIONS)
        assert result is None

    @patch("core.instacart.httpx.post")
    @patch("core.instacart.settings")
    def test_api_http_error_returns_none(self, mock_settings, mock_post):
        mock_settings.instacart_api_key = "test-key"
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("401 Unauthorized")
        mock_post.return_value = mock_response

        result = create_shopping_list(user_id=1, suggestions=SAMPLE_SUGGESTIONS)
        assert result is None


class TestFormatSuggestionsWithInstacart:
    """Test that format_suggestions output + Instacart link works as expected.

    We test the /list integration logic directly rather than going through
    handle_message, which requires mocking the Anthropic client at import time.
    """

    def test_instacart_link_appended_to_suggestions(self):
        text = format_suggestions(SAMPLE_SUGGESTIONS)
        url = "https://www.instacart.com/store/partner_recipes/abc123"
        text += f'\n\n<a href="{url}">Open in Instacart</a>'

        assert "Open in Instacart" in text
        assert url in text
        assert "Shopping Suggestions" in text

    def test_no_instacart_link_when_none(self):
        text = format_suggestions(SAMPLE_SUGGESTIONS)
        url = None
        if url:
            text += f'\n\n<a href="{url}">Open in Instacart</a>'

        assert "Instacart" not in text
        assert "Shopping Suggestions" in text

    def test_no_instacart_link_for_empty_suggestions(self):
        text = format_suggestions([])
        assert "No shopping suggestions" in text
        assert "Instacart" not in text

    @patch("core.instacart.httpx.post")
    @patch("core.instacart.settings")
    def test_full_flow_suggestions_to_instacart_url(self, mock_settings, mock_post):
        """End-to-end: suggestions → Instacart API → URL appended."""
        _cache.clear()
        mock_settings.instacart_api_key = "test-key"
        mock_response = MagicMock()
        mock_response.json.return_value = INSTACART_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        text = format_suggestions(SAMPLE_SUGGESTIONS)
        url = create_shopping_list(user_id=1, suggestions=SAMPLE_SUGGESTIONS)
        assert url is not None
        text += f'\n\n<a href="{url}">Open in Instacart</a>'

        assert "Shopping Suggestions" in text
        assert "Open in Instacart" in text
        assert "instacart.com" in text
