from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Money = Annotated[Decimal, Field(max_digits=28, decimal_places=10)]
Quantity = Annotated[Decimal, Field(gt=0, max_digits=28, decimal_places=10)]


class ExecutionMode(StrEnum):
    LOCAL_SIM = "LOCAL_SIM"
    WEBULL_UAT = "WEBULL_UAT"
    WEBULL_LIVE = "WEBULL_LIVE"


class FeedSource(StrEnum):
    PRODUCTION = "PRODUCTION"
    UAT = "UAT"
    REPLAY = "REPLAY"


class AssetClass(StrEnum):
    EQUITY = "EQUITY"
    FUTURES = "FUTURES"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    SHORT = "SHORT"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    STOP_LOSS_LIMIT = "STOP_LOSS_LIMIT"
    TRAILING_STOP_LOSS = "TRAILING_STOP_LOSS"


class TimeInForce(StrEnum):
    DAY = "DAY"
    GTC = "GTC"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIAL_FILLED = "PARTIAL_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class OrderOrigin(StrEnum):
    STRATEGY = "STRATEGY"
    SYSTEM_ROLL = "SYSTEM_ROLL"
    SYSTEM_EXPIRY = "SYSTEM_EXPIRY"
    SYSTEM_EMERGENCY = "SYSTEM_EMERGENCY"


class StrategyState(StrEnum):
    RUNNING = "RUNNING"
    OUTSIDE_WINDOW = "OUTSIDE_WINDOW"
    PAUSED = "PAUSED"
    DEGRADED = "DEGRADED"
    STOPPED = "STOPPED"


class ContractMode(StrEnum):
    FIXED = "FIXED"
    AUTO_ROLL = "AUTO_ROLL"


class OrderCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_instance_id: str = Field(min_length=1, max_length=64)
    account_id: str = Field(min_length=1, max_length=128)
    symbol: str = Field(min_length=1, max_length=32)
    asset_class: AssetClass
    side: Side
    quantity: Quantity
    order_type: OrderType
    time_in_force: TimeInForce = TimeInForce.DAY
    limit_price: Money | None = None
    stop_price: Money | None = None
    client_order_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=32)
    origin: OrderOrigin = OrderOrigin.STRATEGY
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_prices_and_side(self) -> OrderCommand:
        if (
            self.order_type in {OrderType.LIMIT, OrderType.STOP_LOSS_LIMIT}
            and self.limit_price is None
        ):
            raise ValueError("limit_price is required for limit orders")
        if (
            self.order_type in {OrderType.STOP_LOSS, OrderType.STOP_LOSS_LIMIT}
            and self.stop_price is None
        ):
            raise ValueError("stop_price is required for stop orders")
        if self.asset_class == AssetClass.FUTURES and self.side == Side.SHORT:
            raise ValueError("Webull futures use SELL rather than SHORT")
        return self


class OrderTicket(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    command: OrderCommand
    status: OrderStatus
    broker_order_id: str | None = None
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Decimal | None = None
    rejection_code: str | None = None
    rejection_message: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Fill(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: uuid4().hex)
    ticket_id: str
    strategy_instance_id: str
    symbol: str
    side: Side
    quantity: Quantity
    price: Money
    commission: Decimal = Decimal("0")
    filled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MarketQuote(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    bid: Money
    ask: Money
    bid_size: Decimal = Decimal("0")
    ask_size: Decimal = Decimal("0")
    source_at: datetime
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_spread(self) -> MarketQuote:
        if self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid")
        return self


class VirtualPosition(BaseModel):
    strategy_instance_id: str
    symbol: str
    quantity: Decimal
    average_price: Decimal
    realized_pnl: Decimal = Decimal("0")


class RiskLimits(BaseModel):
    allowed_symbols: set[str] = Field(default_factory=set)
    max_position: Decimal = Decimal("1000000")
    max_notional: Decimal = Decimal("1000000000")
    max_daily_realized_loss: Decimal = Decimal("1000000000")
    max_open_orders: int = Field(default=50, ge=1)
    max_orders_per_minute: int = Field(default=60, ge=1)
