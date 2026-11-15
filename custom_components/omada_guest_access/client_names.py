"""Plain-text display names for the controller (never modify local guest labels)."""

from __future__ import annotations

import re
import unicodedata

MAX_CLIENT_NAME_LENGTH = 120
MAX_AFFIX_LENGTH = 32


def _clean(value: str) -> str:
    """Keep Unicode letters/numbers/marks and conservative display punctuation."""
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"<[^>]*>", "", value)
    value = "".join(
        " " if char.isspace() else char
        for char in value
        if char.isspace() or unicodedata.category(char)[0] in "LNM" or char in "-_.'()"
    )
    return re.sub(r" +", " ", value)


def sanitize_client_name(value: str) -> str:
    """Drop markup, controls, bidi/zero-width characters and unsafe punctuation."""
    return _clean(value).strip()[:MAX_CLIENT_NAME_LENGTH].rstrip()


def format_client_name(name: str, prefix: str = "", suffix: str = "") -> str:
    """Reserve space for both affixes; never send an affix-only name."""
    name = sanitize_client_name(name)
    if not name:
        return ""
    prefix = _clean(prefix)[:MAX_AFFIX_LENGTH]
    suffix = _clean(suffix)[:MAX_AFFIX_LENGTH]
    budget = MAX_CLIENT_NAME_LENGTH - len(prefix) - len(suffix)
    return sanitize_client_name(prefix + name[:budget].rstrip() + suffix)
