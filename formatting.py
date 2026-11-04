"""Mga maliit na helper para maganda ang display ng date at time sa screen."""
from datetime import datetime


def fmt_date(value):
    """'2026-11-05' -> 'Thu, Nov 5, 2026'."""
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return value  # kung hindi valid, ibalik na lang kung ano yung pumasok
    return f"{value.strftime('%a, %b')} {value.day}, {value.year}"


def fmt_time(value):
    """'14:30' -> '2:30 PM'. Sa database, 24-hour ang laging naka-save."""
    if not value:
        return ""
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError:
        return value
    return parsed.strftime("%I:%M %p").lstrip("0")
