"""Controller-bound display names must remain safe, bounded plain text."""

import pytest

from custom_components.omada_guest_access.client_names import format_client_name, sanitize_client_name


@pytest.mark.parametrize(("raw", "expected"), [
    ("  José\t李\nGuest  ", "José 李 Guest"),
    ("<b>Alice</b>\x00\u202e\u200b", "Alice"),
    ('Name "quoted" & <img src=x onerror=x> / \\ ; $`', "Name quoted"),
    ("Ａｌｉｃｅ", "Alice"),
    ("e\u0301", "é"),
    ("O'Neil (phone)-1_2.3", "O'Neil (phone)-1_2.3"),
    ("<b></b>😀\u202e", ""),
    ("A" * 121, "A" * 120),
])
def test_sanitized_names(raw, expected):
    assert sanitize_client_name(raw) == expected
    assert sanitize_client_name(expected) == expected


def test_affixes_are_sanitized_and_preserved_when_name_is_shortened():
    assert format_client_name("<b>Alice</b>", "Guest - ", " - WiFi") == "Guest - Alice - WiFi"
    name = format_client_name("A" * 200, "P" * 32, "S" * 32)
    assert name == "P" * 32 + "A" * 56 + "S" * 32
    assert format_client_name("Alice", "<b>Guest</b> - ", "\u202e - WiFi") == "Guest - Alice - WiFi"
    assert format_client_name("<b></b>", "Guest - ", " - WiFi") == ""
