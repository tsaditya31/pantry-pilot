"""
Item name normalizer — canonicalize item names for matching across
receipts and pantry snapshots.
"""

import re

# Common brand words to strip
_BRAND_NOISE = {
    "organic", "natural", "fresh", "premium", "grade", "fancy",
    "select", "choice", "value", "great", "best", "classic",
}

# Unit/size patterns to strip
_SIZE_RE = re.compile(
    r"\b\d+(\.\d+)?\s*(oz|fl\s*oz|lb|lbs|kg|g|ml|l|ct|pk|pack|count|ea|gal|qt|pt)\b",
    re.IGNORECASE,
)

# Common abbreviation mappings
_ABBREVS = {
    "chkn": "chicken",
    "brst": "breast",
    "brsts": "breasts",
    "org": "organic",
    "whl": "whole",
    "grn": "green",
    "blk": "black",
    "wht": "white",
    "brn": "brown",
    "bana": "banana",
    "straw": "strawberry",
    "tom": "tomato",
    "pot": "potato",
    "sm": "small",
    "md": "medium",
    "lg": "large",
    "xl": "extra large",
    "pnt": "pint",
    "btl": "bottle",
    "pkt": "packet",
    "doz": "dozen",
    "veg": "vegetable",
    "vegs": "vegetables",
}

# Plural normalization (simple suffix rules)
_PLURAL_SUFFIXES = [
    ("ies", "y"),      # berries → berry
    ("ves", "f"),      # loaves → loaf
    ("oes", "o"),      # tomatoes → tomato, potatoes → potato
    ("ses", "s"),      # sauces → sauce (keep trailing s for words ending in s)
    ("es", "e"),       # oranges → orange
    ("s", ""),         # apples → apple
]

# Words that should NOT be de-pluralized
_PLURAL_EXCEPTIONS = {
    "hummus", "couscous", "asparagus", "citrus", "plus",
    "swiss", "molasses", "lettuce", "rice", "cheese", "juice",
    "sauce", "grapes", "peas", "oats", "herbs", "spices",
}


def normalize(name: str) -> str:
    """Normalize an item name to a canonical form for matching."""
    if not name:
        return ""

    s = name.lower().strip()

    # Remove size/quantity patterns (e.g., "16 oz", "2 lb")
    s = _SIZE_RE.sub("", s)

    # Remove special characters except hyphens and spaces
    s = re.sub(r"[^\w\s-]", "", s)

    # Expand abbreviations
    words = s.split()
    words = [_ABBREVS.get(w, w) for w in words]

    # Remove brand noise words
    words = [w for w in words if w not in _BRAND_NOISE]

    # Rejoin and clean up whitespace
    s = " ".join(words).strip()
    s = re.sub(r"\s+", " ", s)

    # Simple plural normalization
    if s and s not in _PLURAL_EXCEPTIONS:
        for suffix, replacement in _PLURAL_SUFFIXES:
            if s.endswith(suffix) and len(s) > len(suffix) + 1:
                candidate = s[: -len(suffix)] + replacement
                # Avoid over-stripping (e.g., "gas" → "ga")
                if len(candidate) >= 3:
                    s = candidate
                    break

    return s
