"""Shared integration option parsing."""

from .const import DEFAULT_DURATION_OPTIONS


def parse_duration_options(value: str = DEFAULT_DURATION_OPTIONS) -> list[int]:
    """Dropdown presets: 0 means the long-term Forever preset."""
    if not isinstance(value, str) or len(value) > 700:
        raise ValueError("Duration presets must be comma-separated hours")
    parts = value.split(",")
    if not parts or any(not part.strip().isascii() or not part.strip().isdigit() for part in parts):
        raise ValueError("Duration presets must be comma-separated whole hours")
    hours = sorted(set(int(part.strip()) for part in parts))
    if not hours or hours[0] < 0 or hours[-1] > 168:
        raise ValueError("Duration presets must be 0 (Forever) or 1–168 hours")
    return [hour for hour in hours if hour] + ([0] if 0 in hours else [])
