from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class ExecutionMode(StrEnum):
    PAPER = "paper"


class RuntimeMode(StrEnum):
    TEST = "test"
    LIVE = "live"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


class OrderRole(StrEnum):
    MAIN = "MAIN"
    ADD_ON = "ADD_ON"


class IntentType(StrEnum):
    PLACE_VIRTUAL_STOP = "PlaceVirtualStop"
    CANCEL_VIRTUAL_ORDER = "CancelVirtualOrder"
    PAPER_FILL = "PaperFill"
    MOVE_STOP = "MoveStop"
    OPEN_ADD_ON = "OpenAddOn"
    CLOSE_CYCLE = "CloseCycle"
    FLATTEN_PAPER_POSITION = "FlattenPaperPosition"
    LOCK_INSTANCE = "LockInstance"
    LOCK_GLOBAL = "LockGlobal"


@dataclass(slots=True)
class StrategyInstance:
    id: str
    strategy_name: str
    symbol: str
    account_id: str = ""
    enabled: bool = True
    mode: ExecutionMode = ExecutionMode.PAPER
    params: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class Bar:
    symbol: str
    bar_type: str
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    bar_interval: int = 1


@dataclass(slots=True)
class QuoteState:
    symbol: str
    fields: dict[str, Any] = field(default_factory=dict)
    received_at: datetime = field(default_factory=utc_now)

    @property
    def bid(self) -> float | None:
        return _float_or_none(self.fields.get("bid"))

    @property
    def ask(self) -> float | None:
        return _float_or_none(self.fields.get("ask"))

    @property
    def last_price(self) -> float | None:
        return _float_or_none(self.fields.get("last_price"))

    @property
    def mid_price(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2.0


@dataclass(slots=True)
class PaperOrder:
    id: str
    strategy_instance_id: str
    cycle_id: str
    symbol: str
    side: OrderSide
    role: OrderRole
    quantity: float
    status: OrderStatus = OrderStatus.PENDING
    stop_price: float | None = None
    fill_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    parent_order_id: str | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PaperFill:
    id: str
    order_id: str
    strategy_instance_id: str
    cycle_id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    filled_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class PaperAccount:
    id: str
    starting_balance: float = 10_000.0
    cash_balance: float = 10_000.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    current_equity: float = 10_000.0
    peak_equity: float = 10_000.0
    max_drawdown: float = 0.0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class PaperPosition:
    id: str
    account_id: str
    strategy_instance_id: str
    symbol: str
    side: OrderSide
    quantity: float
    average_price: float
    market_price: float | None = None
    unrealized_pnl: float = 0.0
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class PaperAccountEvent:
    id: str
    account_id: str
    event_type: str
    amount: float = 0.0
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class MarketStreamMessage:
    sequence: int
    timestamp: datetime
    strategy_instance_id: str
    symbol: str
    type: str
    raw: dict[str, Any]


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
