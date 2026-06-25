from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from strategy_desk.domain import ContractMode


class SettlementType(StrEnum):
    CASH = "Cash"
    PHYSICAL = "Physical"


class ExpiryState(StrEnum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CLOSE_ONLY = "CLOSE_ONLY"
    AUTO_FLATTEN = "AUTO_FLATTEN"
    LOCKED = "LOCKED"
    INVALID = "INVALID"


class FuturesContract(BaseModel):
    symbol: str
    code: str
    instrument_id: str
    contract_month: str
    settlement: SettlementType
    first_notice_date: date | None = None
    last_trading_date: date | None = None
    settlement_date: date | None = None
    status: str
    contract_type: str = "MONTHLY"
    size: Decimal
    min_tick: Decimal

    @property
    def critical_date(self) -> date | None:
        if self.settlement == SettlementType.PHYSICAL:
            return self.first_notice_date
        return self.last_trading_date


class ExpiryPolicy(BaseModel):
    warning_trading_days: int = Field(default=10, ge=1)
    close_only_trading_days: int = Field(default=5, ge=1)
    auto_flatten_trading_days: int = Field(default=3, ge=1)
    flatten_time_et: str = "14:30"
    holidays: set[date] = Field(default_factory=set)

    @model_validator(mode="after")
    def ordered(self) -> ExpiryPolicy:
        if (
            not self.warning_trading_days
            >= self.close_only_trading_days
            >= self.auto_flatten_trading_days
        ):
            raise ValueError("expiry thresholds must be ordered warning >= close-only >= flatten")
        return self


class ContractBinding(BaseModel):
    mode: ContractMode
    product_code: str
    current_symbol: str


def trading_days_between(start: date, end: date, holidays: set[date] | None = None) -> int:
    if start >= end:
        return 0
    current = start
    count = 0
    while current < end:
        current += timedelta(days=1)
        if current.weekday() < 5 and current not in (holidays or set()):
            count += 1
    return count


class ExpiryGuard:
    def __init__(self, policy: ExpiryPolicy | None = None) -> None:
        self.policy = policy or ExpiryPolicy()

    def state(self, contract: FuturesContract, today: date) -> ExpiryState:
        critical = contract.critical_date
        if critical is None or contract.status == "NT":
            return ExpiryState.INVALID
        if today >= critical:
            return ExpiryState.LOCKED
        days = trading_days_between(today, critical, self.policy.holidays)
        if days <= self.policy.auto_flatten_trading_days:
            return ExpiryState.AUTO_FLATTEN
        if days <= self.policy.close_only_trading_days:
            return ExpiryState.CLOSE_ONLY
        if days <= self.policy.warning_trading_days:
            return ExpiryState.WARNING
        return ExpiryState.NORMAL

    def next_contract(
        self,
        current: FuturesContract,
        contracts: list[FuturesContract],
        today: date,
    ) -> FuturesContract | None:
        eligible = [
            contract
            for contract in contracts
            if contract.code == current.code
            and contract.contract_type == "MONTHLY"
            and contract.status == "OC"
            and contract.contract_month > current.contract_month
            and self.state(contract, today) == ExpiryState.NORMAL
        ]
        return min(eligible, key=lambda item: item.contract_month, default=None)
