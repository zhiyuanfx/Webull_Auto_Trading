from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BridgeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Action(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    CANCEL = "CANCEL"
    REPLACE = "REPLACE"
    FLATTEN = "FLATTEN"


class InstrumentType(StrEnum):
    EQUITY = "EQUITY"
    OPTION = "OPTION"
    FUTURES = "FUTURES"
    CRYPTO = "CRYPTO"
    EVENT = "EVENT"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    STOP_LOSS_LIMIT = "STOP_LOSS_LIMIT"
    TRAILING_STOP_LOSS = "TRAILING_STOP_LOSS"


class TimeInForce(StrEnum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"


class EventStatus(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    PROCESSING = "processing"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    VALIDATION_FAILED = "validation_failed"
    UNKNOWN = "unknown"


TERMINAL_STATUSES = {
    EventStatus.FILLED.value,
    EventStatus.CANCELLED.value,
    EventStatus.FAILED.value,
    EventStatus.REJECTED.value,
    EventStatus.DUPLICATE.value,
    EventStatus.VALIDATION_FAILED.value,
}


class RouteConfig(BaseModel):
    route_id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="TradingView", min_length=1, max_length=80)
    account_id: str = Field(default="", max_length=80)
    secret_hash: str = ""
    enabled: bool = True
    allowed_symbols: list[str] = Field(default_factory=list)
    max_quantity: Decimal = Field(default=Decimal("1000000"), gt=0)
    max_notional: Decimal = Field(default=Decimal("1000000000"), gt=0)
    accepted_order_types: list[OrderType] = Field(
        default_factory=lambda: [
            OrderType.MARKET,
            OrderType.LIMIT,
            OrderType.STOP_LOSS,
            OrderType.STOP_LOSS_LIMIT,
        ]
    )
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("route_id")
    @classmethod
    def clean_route_id(cls, value: str) -> str:
        return value.strip()

    @field_validator("allowed_symbols", mode="before")
    @classmethod
    def clean_symbols(cls, value: Any) -> list[str]:
        if value is None:
            return []
        symbols = value if isinstance(value, list) else [value]
        return sorted({str(item).strip().upper() for item in symbols if str(item).strip()})


class RouteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    account_id: str | None = Field(default=None, max_length=80)
    secret: str | None = Field(default=None, min_length=1, max_length=256)
    enabled: bool | None = None
    allowed_symbols: list[str] | None = None
    max_quantity: Decimal | None = Field(default=None, gt=0)
    max_notional: Decimal | None = Field(default=None, gt=0)
    accepted_order_types: list[OrderType] | None = None


class BridgePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    secret: str = Field(min_length=1)
    event_id: str = Field(min_length=1, max_length=128)
    action: Action
    symbol: str | None = None
    quantity: Decimal | None = Field(default=None, gt=0)
    client_order_id: str | None = Field(default=None, max_length=32)
    target_client_order_id: str | None = Field(default=None, max_length=32)
    instrument_type: InstrumentType = InstrumentType.EQUITY
    market: str = "US"
    order_type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.DAY
    entrust_type: str = "QTY"
    combo_type: str = "NORMAL"
    support_trading_session: str | None = None
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    strategy: str | None = None
    strategy_id: str | None = None
    alert: str | None = None
    ticker: str | None = None
    timeframe: str | None = None
    bar_time: str | None = None
    pine_order_id: str | None = None
    raw_alert_type: str | None = None

    @field_validator("action", "instrument_type", "order_type", "time_in_force", mode="before")
    @classmethod
    def uppercase_enum(cls, value: Any) -> Any:
        return str(value).strip().upper() if value is not None else value

    @field_validator("symbol", "market", "entrust_type", "combo_type", "support_trading_session")
    @classmethod
    def uppercase_optional(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else value

    @field_validator("client_order_id", "target_client_order_id")
    @classmethod
    def clean_client_id(cls, value: str | None) -> str | None:
        return value.strip() if value else value

    @model_validator(mode="after")
    def validate_command_shape(self) -> BridgePayload:
        if self.action in {Action.BUY, Action.SELL, Action.FLATTEN}:
            if not self.symbol:
                raise ValueError("symbol is required for BUY, SELL, and FLATTEN")
            if self.quantity is None:
                raise ValueError("quantity is required for BUY, SELL, and FLATTEN")
        if self.action in {Action.CANCEL, Action.REPLACE} and not self.order_reference:
            raise ValueError("target_client_order_id or client_order_id is required")
        if self.action in {Action.BUY, Action.SELL, Action.FLATTEN, Action.REPLACE}:
            if self.order_type == OrderType.LIMIT and self.limit_price is None:
                raise ValueError("limit_price is required for LIMIT orders")
            if self.order_type == OrderType.STOP_LOSS and self.stop_price is None:
                raise ValueError("stop_price is required for STOP_LOSS orders")
            if self.order_type == OrderType.STOP_LOSS_LIMIT and (
                self.limit_price is None or self.stop_price is None
            ):
                raise ValueError(
                    "limit_price and stop_price are required for STOP_LOSS_LIMIT orders"
                )
        return self

    @property
    def order_reference(self) -> str | None:
        return self.target_client_order_id or self.client_order_id

    def sanitized(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload.pop("secret", None)
        return payload


def now_utc() -> datetime:
    return datetime.now(UTC)


def utc_iso() -> str:
    return now_utc().isoformat()


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def verify_secret(secret: str, expected_hash: str) -> bool:
    if not expected_hash:
        return False
    return hmac.compare_digest(hash_secret(secret), expected_hash)


def make_client_order_id(route_id: str, event_id: str, action: str, suffix: str = "") -> str:
    digest = hashlib.sha256(f"{route_id}:{event_id}:{action}:{suffix}".encode()).hexdigest().upper()
    return f"WB{digest[:30]}"
