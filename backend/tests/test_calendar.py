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


# ── the shipped NSE list and the deck's closure notice ───────────────────────
from shared.market_calendar import closures  # noqa: E402
from shared.nse_holidays import NSE_HOLIDAYS, YEARS_COVERED  # noqa: E402


def test_the_shipped_list_is_well_formed():
    assert 2026 in YEARS_COVERED
    for day, label in NSE_HOLIDAYS.items():
        d = date.fromisoformat(day)
        assert d.weekday() < 5, f"{day} is a weekend — the market is closed anyway"
        assert label.strip()
    assert NSE_HOLIDAYS["2026-10-02"] == "Mahatma Gandhi Jayanti"


def test_the_day_before_a_holiday_announces_it():
    c = closures(date(2026, 10, 1), NSE_HOLIDAYS)  # Thursday
    assert c["today"] is None
    assert c["ahead"] == [{"day": "2026-10-02", "label": "Mahatma Gandhi Jayanti"}]
    assert c["next_session"] == "2026-10-05"


def test_the_holiday_itself_says_so():
    c = closures(date(2026, 10, 2), NSE_HOLIDAYS)  # Friday, Gandhi Jayanti
    assert c["today"] == {"day": "2026-10-02", "label": "Mahatma Gandhi Jayanti"}
    assert c["ahead"] == [] and c["next_session"] == "2026-10-05"
    assert c["next_holiday"] == {"day": "2026-10-20", "label": "Dussehra"}


def test_friday_and_the_weekend_announce_a_monday_holiday():
    for today in (date(2026, 9, 11), date(2026, 9, 12), date(2026, 9, 13)):  # Fri, Sat, Sun
        c = closures(today, NSE_HOLIDAYS)
        assert c["ahead"] == [{"day": "2026-09-14", "label": "Ganesh Chaturthi"}], today
        assert c["next_session"] == "2026-09-15"


def test_an_ordinary_day_announces_nothing():
    c = closures(date(2026, 10, 6), NSE_HOLIDAYS)
    assert c["today"] is None and c["ahead"] == []
    assert c["next_session"] == "2026-10-07"


def test_back_to_back_closures_are_all_announced():
    c = closures(date(2026, 10, 20), {"2026-10-20": "A", "2026-10-21": "", "2026-10-22": "C"})
    assert c["today"]["label"] == "A"
    assert [h["day"] for h in c["ahead"]] == ["2026-10-21", "2026-10-22"]
    assert c["ahead"][0]["label"] == "Exchange holiday"
    assert c["next_session"] == "2026-10-23"


def test_the_scheduler_will_not_start_anything_on_a_shipped_holiday():
    holidays = set(NSE_HOLIDAYS)
    assert not should_be_running(datetime(2026, 10, 2, 10, 0), holidays)
    assert should_be_running(datetime(2026, 10, 5, 10, 0), holidays)
    assert next_transition(datetime(2026, 10, 1, 16, 0), holidays)["day"] == "2026-10-05"
