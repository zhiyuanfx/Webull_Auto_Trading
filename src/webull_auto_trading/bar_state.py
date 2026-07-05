from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from webull_auto_trading.domain import Bar


@dataclass(slots=True)
class BracketBars:
    previous_day_high: float
    previous_day_low: float
    today_high: float | None = None
    today_low: float | None = None


def previous_completed_daily_bar(bars: list[Bar], now: datetime) -> Bar | None:
    if not bars:
        return None
    completed = [bar for bar in bars if bar.time.date() < now.date()]
    if completed:
        return max(completed, key=lambda bar: bar.time)
    ordered = sorted(bars, key=lambda bar: bar.time)
    return ordered[-2] if len(ordered) >= 2 else None


def current_day_range(minute_bars: list[Bar], now: datetime) -> tuple[float | None, float | None]:
    completed = [bar for bar in minute_bars if bar.time.date() == now.date() and bar.time < now]
    if not completed:
        return None, None
    return max(bar.high for bar in completed), min(bar.low for bar in completed)


def bootstrap_bracket_bars(
    daily_bars: list[Bar],
    minute_bars: list[Bar],
    now: datetime,
) -> BracketBars | None:
    previous = previous_completed_daily_bar(daily_bars, now)
    if previous is None or previous.high <= previous.low:
        return None
    today_high, today_low = current_day_range(minute_bars, now)
    return BracketBars(
        previous_day_high=previous.high,
        previous_day_low=previous.low,
        today_high=today_high,
        today_low=today_low,
    )
