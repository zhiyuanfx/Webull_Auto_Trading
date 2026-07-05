from datetime import UTC, datetime, timedelta

from webull_auto_trading.bar_state import bootstrap_bracket_bars, previous_completed_daily_bar
from webull_auto_trading.domain import Bar


def test_daily_bar_bootstrap_selects_previous_completed_day() -> None:
    now = datetime(2026, 7, 5, 16, 0, tzinfo=UTC)
    bars = [
        Bar("NASDAQ:AAPL", "day", now - timedelta(days=2), 90, 95, 89, 94),
        Bar("NASDAQ:AAPL", "day", now - timedelta(days=1), 100, 110, 99, 108),
        Bar("NASDAQ:AAPL", "day", now, 108, 112, 104, 111),
    ]

    previous = previous_completed_daily_bar(bars, now)

    assert previous is not None
    assert previous.high == 110
    assert previous.low == 99


def test_bootstrap_includes_current_day_minute_range() -> None:
    now = datetime(2026, 7, 5, 16, 0, tzinfo=UTC)
    daily = [Bar("NASDAQ:AAPL", "day", now - timedelta(days=1), 100, 110, 99, 108)]
    minutes = [
        Bar("NASDAQ:AAPL", "minute", now.replace(hour=15, minute=55), 108, 111, 107, 110),
        Bar("NASDAQ:AAPL", "minute", now.replace(hour=15, minute=56), 110, 113, 109, 112),
    ]

    state = bootstrap_bracket_bars(daily, minutes, now)

    assert state is not None
    assert state.previous_day_high == 110
    assert state.today_high == 113
    assert state.today_low == 107
