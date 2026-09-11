from __future__ import annotations

from datetime import date, datetime

from shared.market_calendar import (
    START_AT,
    STOP_AT,
    calendar_configured,
    is_trading_day,
    next_trading_day,
    next_transition,
    should_be_running,
)

HOLIDAYS = {"2026-10-21", "2026-10-22"}


def test_weekday_is_a_trading_day():
    assert is_trading_day(date(2026, 9, 10), set())  # Thursday


def test_saturday_is_not_a_trading_day():
    assert not is_trading_day(date(2026, 9, 12), set())


def test_sunday_is_not_a_trading_day():
    assert not is_trading_day(date(2026, 9, 13), set())


def test_listed_holiday_is_not_a_trading_day():
    assert not is_trading_day(date(2026, 10, 21), HOLIDAYS)


def test_engine_runs_inside_the_window():
    assert should_be_running(datetime(2026, 9, 10, 10, 0), set())


def test_engine_is_down_before_the_window():
    assert not should_be_running(datetime(2026, 9, 10, 8, 59), set())


def test_engine_is_down_after_the_window():
    assert not should_be_running(datetime(2026, 9, 10, 15, 26), set())


def test_window_starts_before_market_open():
    assert START_AT < (9, 15), "engine needs warm-up time before the first tick"


def test_window_stops_after_force_close():
    assert STOP_AT > (15, 10), "engine must outlive the forced square-off"


def test_engine_is_down_all_day_on_a_holiday():
    assert not should_be_running(datetime(2026, 10, 21, 11, 0), HOLIDAYS)


def test_next_trading_day_skips_the_weekend():
    assert next_trading_day(date(2026, 9, 11), set()) == date(2026, 9, 14)


def test_next_trading_day_skips_holidays():
    assert next_trading_day(date(2026, 10, 20), HOLIDAYS) == date(2026, 10, 23)


def test_next_transition_before_open_is_a_start():
    t = next_transition(datetime(2026, 9, 10, 7, 0), set())
    assert t["action"] == "start" and t["day"] == "2026-09-10"


def test_next_transition_during_session_is_a_stop():
    t = next_transition(datetime(2026, 9, 10, 11, 0), set())
    assert t["action"] == "stop" and t["day"] == "2026-09-10"


def test_next_transition_after_close_rolls_to_next_day():
    t = next_transition(datetime(2026, 9, 11, 18, 0), set())
    assert t["action"] == "start" and t["day"] == "2026-09-14"


def test_calendar_configured_detects_year():
    assert calendar_configured(HOLIDAYS, 2026)
    assert not calendar_configured(HOLIDAYS, 2027)
