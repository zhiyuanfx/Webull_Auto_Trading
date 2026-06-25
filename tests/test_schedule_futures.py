from datetime import UTC, date, datetime
from decimal import Decimal

from strategy_desk.futures import (
    ExpiryGuard,
    ExpiryState,
    FuturesContract,
    SettlementType,
)
from strategy_desk.schedule import TradingSchedule


def contract(symbol: str, month: str, critical: date) -> FuturesContract:
    return FuturesContract(
        symbol=symbol,
        code="MGC",
        instrument_id=symbol,
        contract_month=month,
        settlement=SettlementType.PHYSICAL,
        first_notice_date=critical,
        last_trading_date=critical,
        status="OC",
        size=Decimal("10"),
        min_tick=Decimal("0.1"),
    )


def test_schedule_handles_timezone_and_overnight_windows():
    daytime = TradingSchedule.from_pairs([("08:00", "11:00")])
    assert daytime.is_open(datetime(2026, 6, 22, 13, 0, tzinfo=UTC))
    assert not daytime.is_open(datetime(2026, 6, 22, 16, 0, tzinfo=UTC))
    overnight = TradingSchedule.from_pairs([("22:00", "02:00")], timezone="UTC")
    assert overnight.is_open(datetime(2026, 6, 22, 23, 0, tzinfo=UTC))
    holiday = TradingSchedule.from_pairs([("08:00", "11:00")]).model_copy(
        update={"holidays": {date(2026, 6, 22)}}
    )
    assert not holiday.is_open(datetime(2026, 6, 22, 13, 0, tzinfo=UTC))


def test_expiry_guard_and_next_contract():
    guard = ExpiryGuard()
    today = date(2026, 6, 1)
    current = contract("MGCM6", "202606", date(2026, 6, 4))
    next_month = contract("MGCQ6", "202608", date(2026, 8, 25))
    assert guard.state(current, today) == ExpiryState.AUTO_FLATTEN
    assert guard.next_contract(current, [current, next_month], today) == next_month
