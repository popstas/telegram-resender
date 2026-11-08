"""Per-instance monitoring windows: watch an instance only at given times.

A window is a set of weekdays plus a ``from``–``to`` time of day. ``from`` is
inclusive, ``to`` exclusive. A window with ``to`` earlier than ``from`` runs past
midnight and belongs to the day it starts on.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ALL_DAYS = frozenset(range(7))


@dataclass(frozen=True)
class ScheduleWindow:
    days: frozenset  # weekday numbers, Monday = 0
    start: time
    end: time


def _parse_time(value, where: str) -> time:
    try:
        hours, minutes = str(value).split(":")
        if not isinstance(value, str) or len(minutes) != 2:
            raise ValueError
        return time(int(hours), int(minutes))
    except ValueError:
        raise ValueError(
            f"{where} must be a time like '20:55', got {value!r}"
        ) from None


def _parse_days(value, where: str) -> frozenset:
    if value is None:
        return ALL_DAYS
    if not isinstance(value, list) or not value:
        raise ValueError(f"{where} must be a non-empty list like [sat], got {value!r}")
    days = set()
    for day in value:
        name = str(day).lower()
        if name not in DAYS:
            raise ValueError(f"{where} entries must be one of {DAYS}, got {day!r}")
        days.add(DAYS.index(name))
    return frozenset(days)


def parse_schedule(value) -> list[ScheduleWindow]:
    """Parse the ``schedule`` list of an instance; ``None`` means always on."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"schedule must be a list of windows, got {value!r}")
    windows = []
    for i, item in enumerate(value):
        where = f"schedule[{i}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be a mapping, got {item!r}")
        start = _parse_time(item.get("from"), f"{where}.from")
        end = _parse_time(item.get("to"), f"{where}.to")
        if start == end:
            raise ValueError(f"{where}: from and to must differ, got {start}")
        windows.append(
            ScheduleWindow(
                days=_parse_days(item.get("days"), f"{where}.days"),
                start=start,
                end=end,
            )
        )
    return windows


def parse_timezone(value) -> ZoneInfo | None:
    """Return the IANA zone for ``value``; ``None`` keeps the process local time."""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"timezone must be an IANA name, got {value!r}")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"timezone {value!r} is unknown") from None


def is_active(
    windows: list[ScheduleWindow], tz: tzinfo | None, now: datetime | None = None
) -> bool:
    """True when ``now`` falls into any window (always true without windows)."""
    if not windows:
        return True
    now = (now or datetime.now().astimezone()).astimezone(tz)
    current = now.time().replace(second=0, microsecond=0)
    today = now.weekday()
    yesterday = (now - timedelta(days=1)).weekday()
    for w in windows:
        if w.start < w.end:
            if today in w.days and w.start <= current < w.end:
                return True
        elif (today in w.days and current >= w.start) or (
            yesterday in w.days and current < w.end
        ):
            return True
    return False
