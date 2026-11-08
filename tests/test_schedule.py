from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from src import schedule
from src.schedule import ScheduleWindow

SAT = 5
BELGRADE = ZoneInfo("Europe/Belgrade")
RADIOT = [ScheduleWindow(days=frozenset({SAT}), start=time(20, 55), end=time(21, 30))]


def _at(y, mo, d, h, mi, tz=BELGRADE):
    return datetime(y, mo, d, h, mi, tzinfo=tz)


def test_empty_schedule_is_always_active():
    assert schedule.is_active([], None, _at(2026, 11, 9, 3, 0))


@pytest.mark.parametrize(
    "now,expected",
    [
        (_at(2026, 11, 7, 20, 54), False),  # Saturday, before
        (_at(2026, 11, 7, 20, 55), True),  # start is inclusive
        (_at(2026, 11, 7, 21, 29), True),
        (_at(2026, 11, 7, 21, 30), False),  # end is exclusive
        (_at(2026, 11, 8, 21, 0), False),  # Sunday
    ],
)
def test_saturday_window(now, expected):
    assert schedule.is_active(RADIOT, BELGRADE, now) is expected


def test_now_is_converted_to_the_window_timezone():
    # 20:00 UTC on Saturday = 21:00 in Belgrade (CET)
    now = datetime(2026, 11, 7, 20, 0, tzinfo=timezone.utc)
    assert schedule.is_active(RADIOT, BELGRADE, now)
    assert not schedule.is_active(RADIOT, timezone.utc, now)


def test_overnight_window_belongs_to_start_day():
    windows = [ScheduleWindow(days=frozenset({SAT}), start=time(23, 0), end=time(1, 0))]
    assert schedule.is_active(windows, BELGRADE, _at(2026, 11, 7, 23, 30))
    assert schedule.is_active(windows, BELGRADE, _at(2026, 11, 8, 0, 30))  # Sun night
    assert not schedule.is_active(windows, BELGRADE, _at(2026, 11, 8, 23, 30))
    assert not schedule.is_active(windows, BELGRADE, _at(2026, 11, 7, 0, 30))


def test_any_window_matches():
    windows = RADIOT + [
        ScheduleWindow(days=frozenset(range(7)), start=time(9, 0), end=time(10, 0))
    ]
    assert schedule.is_active(windows, BELGRADE, _at(2026, 11, 9, 9, 30))


def test_parse_schedule():
    parsed = schedule.parse_schedule(
        [
            {"days": ["sat"], "from": "20:55", "to": "21:30"},
            {"from": "9:00", "to": "10:00"},
        ]
    )
    assert parsed == [
        RADIOT[0],
        ScheduleWindow(days=frozenset(range(7)), start=time(9, 0), end=time(10, 0)),
    ]


def test_parse_schedule_none_is_empty():
    assert schedule.parse_schedule(None) == []


@pytest.mark.parametrize(
    "value,match",
    [
        ("sat 20:55", "schedule must be a list"),
        (["x"], r"schedule\[0\] must be a mapping"),
        ([{"to": "21:30"}], r"schedule\[0\].from"),
        ([{"from": "20:55"}], r"schedule\[0\].to"),
        ([{"from": "25:00", "to": "26:00"}], r"schedule\[0\].from"),
        ([{"from": 2055, "to": "21:30"}], r"schedule\[0\].from"),
        ([{"from": "20:55", "to": "20:55"}], "must differ"),
        ([{"days": ["saturday"], "from": "20:55", "to": "21:30"}], "days"),
        ([{"days": [], "from": "20:55", "to": "21:30"}], "days"),
        ([{"days": "sat", "from": "20:55", "to": "21:30"}], "days"),
    ],
)
def test_parse_schedule_rejects(value, match):
    with pytest.raises(ValueError, match=match):
        schedule.parse_schedule(value)


def test_parse_timezone():
    assert schedule.parse_timezone("Europe/Belgrade") == BELGRADE
    assert schedule.parse_timezone(None) is None


@pytest.mark.parametrize("value", ["Mars/Olympus", 5, ""])
def test_parse_timezone_rejects(value):
    with pytest.raises(ValueError, match="timezone"):
        schedule.parse_timezone(value)
