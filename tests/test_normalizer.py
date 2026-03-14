"""Tests for item name normalizer."""

import pytest
from core.item_normalizer import normalize


class TestNormalize:
    def test_lowercase(self):
        assert normalize("WHOLE MILK") == "whole milk"

    def test_strip_whitespace(self):
        assert normalize("  bananas  ") == "banana"

    def test_strip_size_patterns(self):
        assert normalize("Milk 1 gal") == "milk"
        assert normalize("Cheese 8 oz") == "cheese"
        assert normalize("Chicken 2 lb") == "chicken"
        assert normalize("Yogurt 16 fl oz") == "yogurt"

    def test_remove_brand_noise(self):
        assert normalize("Organic Fresh Bananas") == "banana"
        assert normalize("Premium Select Beef") == "beef"

    def test_expand_abbreviations(self):
        assert normalize("chkn brst") == "chicken breast"
        assert normalize("grn beans") == "green bean"
        assert normalize("whl wheat bread") == "whole wheat bread"

    def test_plural_normalization(self):
        assert normalize("apples") == "apple"
        assert normalize("tomatoes") == "tomato"
        assert normalize("berries") == "berry"
        assert normalize("loaves") == "loaf"

    def test_plural_exceptions(self):
        assert normalize("rice") == "rice"
        assert normalize("cheese") == "cheese"
        assert normalize("hummus") == "hummus"
        assert normalize("lettuce") == "lettuce"

    def test_special_chars_removed(self):
        assert normalize("Ben & Jerry's") == "ben jerry"

    def test_empty_string(self):
        assert normalize("") == ""
        assert normalize(None) == ""

    def test_matching_across_formats(self):
        # Receipt might say one thing, pantry another — should match
        receipt_name = normalize("ORG BANA 2 lb")
        pantry_name = normalize("Organic Bananas")
        assert receipt_name == pantry_name

    def test_condensed_whitespace(self):
        assert normalize("whole   wheat   bread") == "whole wheat bread"
