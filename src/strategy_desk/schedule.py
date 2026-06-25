from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator


class TradingWindow(BaseModel):
    start: time
    end: time

    def contains(self, current: time) -> bool:
        if self.start <= self.end:
            return self.start <= current < self.end
        return current >= self.start or current < self.end


class TradingSchedule(BaseModel):
    timezone: str = "America/New_York"
    windows: list[TradingWindow] = Field(default_factory=list)
    weekdays: set[int] = Field(default_factory=lambda: {0, 1, 2, 3, 4})
    holidays: set[date] = Field(default_factory=set)

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    def is_open(self, now: datetime) -> bool:
        local = now.astimezone(ZoneInfo(self.timezone))
        if local.weekday() not in self.weekdays or local.date() in self.holidays:
            return False
        return not self.windows or any(window.contains(local.time()) for window in self.windows)

    @classmethod
    def from_pairs(
        cls,
        windows: list[tuple[str, str]],
        timezone: str = "America/New_York",
    ) -> TradingSchedule:
        return cls(
            timezone=timezone,
            windows=[
                TradingWindow(start=time.fromisoformat(start), end=time.fromisoformat(end))
                for start, end in windows
            ],
        )
