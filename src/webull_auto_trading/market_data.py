from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import count
from typing import Any, Literal

from webull_auto_trading.domain import MarketStreamMessage, QuoteState, utc_now

LIVE_ENDPOINT = "wss://realtime.insightsentry.com/live"
FATAL_ERRORS = {
    "max_connections_exceeded",
    "connection_evicted",
    "server_busy",
    "internal_server_error",
}
QUOTE_REQUIRED_FIELDS = {"ask", "bid", "last_price", "delay_seconds"}
QUOTE_PATCH_FIELDS = {
    "ask",
    "ask_size",
    "bid",
    "bid_size",
    "change",
    "change_percent",
    "currency_code",
    "delay_seconds",
    "high_price",
    "last_price",
    "last_update",
    "low_price",
    "lp_time",
    "market_cap",
    "open_price",
    "prev_close_price",
    "status",
    "volume",
}


@dataclass(slots=True)
class QuoteValidation:
    ok: bool
    reason: str = ""


@dataclass(slots=True)
class ParsedMarketMessage:
    kind: Literal["quote", "series", "heartbeat", "info", "error", "pong", "unknown"]
    payload: dict[str, Any] | list[dict[str, Any]] | str | None = None
    fatal: bool = False


class QuoteBook:
    def __init__(self) -> None:
        self._quotes: dict[str, QuoteState] = {}

    def merge_quote_item(
        self,
        item: dict[str, Any],
        *,
        received_at: datetime | None = None,
    ) -> QuoteState:
        symbol = str(item.get("code") or item.get("symbol") or "").strip()
        if not symbol:
            raise ValueError("quote item is missing code")
        current = self._quotes.get(symbol)
        merged = dict(current.fields) if current else {"code": symbol}
        for key, value in item.items():
            if key == "code" or key in QUOTE_PATCH_FIELDS:
                merged[key] = value
        quote = QuoteState(symbol=symbol, fields=merged, received_at=received_at or utc_now())
        self._quotes[symbol] = quote
        return quote

    def get(self, symbol: str) -> QuoteState | None:
        return self._quotes.get(symbol)

    def all(self) -> list[QuoteState]:
        return list(self._quotes.values())


class MarketStreamBuffer:
    def __init__(self, max_messages: int = 20) -> None:
        self.max_messages = max_messages
        self._sequence = count(1)
        self._messages: dict[str, deque[MarketStreamMessage]] = defaultdict(
            lambda: deque(maxlen=max_messages)
        )
        self._symbol_messages: dict[str, deque[MarketStreamMessage]] = defaultdict(
            lambda: deque(maxlen=max_messages)
        )

    def append(
        self,
        *,
        strategy_instance_id: str,
        symbol: str,
        message_type: str,
        raw: dict[str, Any],
        timestamp: datetime | None = None,
    ) -> MarketStreamMessage:
        message = MarketStreamMessage(
            sequence=next(self._sequence),
            timestamp=timestamp or utc_now(),
            strategy_instance_id=strategy_instance_id,
            symbol=symbol,
            type=message_type,
            raw=raw,
        )
        self._messages[strategy_instance_id].append(message)
        return message

    def append_symbol(
        self,
        *,
        symbol: str,
        message_type: str,
        raw: dict[str, Any],
        timestamp: datetime | None = None,
    ) -> MarketStreamMessage:
        message = MarketStreamMessage(
            sequence=next(self._sequence),
            timestamp=timestamp or utc_now(),
            strategy_instance_id="",
            symbol=symbol,
            type=message_type,
            raw=raw,
        )
        self._symbol_messages[symbol].append(message)
        return message

    def list_for_strategy(
        self,
        strategy_instance_id: str,
        *,
        since: int | None = None,
    ) -> list[MarketStreamMessage]:
        messages = list(self._messages.get(strategy_instance_id, ()))
        if since is None:
            return messages
        return [message for message in messages if message.sequence > since]

    def list_for_symbol(
        self,
        symbol: str,
        *,
        since: int | None = None,
    ) -> list[MarketStreamMessage]:
        messages = list(self._symbol_messages.get(symbol, ()))
        if since is None:
            return messages
        return [message for message in messages if message.sequence > since]


def merge_quote_fields(existing: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    symbol = str(update.get("code") or existing.get("code") or "").strip()
    if not symbol:
        raise ValueError("quote update is missing code")
    merged = dict(existing)
    merged["code"] = symbol
    for key, value in update.items():
        if key == "code" or key in QUOTE_PATCH_FIELDS:
            merged[key] = value
    return merged


def validate_quote(
    quote: QuoteState,
    *,
    now: datetime | None = None,
    max_staleness_seconds: int = 30,
    allow_delayed: bool = False,
) -> QuoteValidation:
    missing = sorted(field for field in QUOTE_REQUIRED_FIELDS if quote.fields.get(field) is None)
    if missing:
        return QuoteValidation(False, f"incomplete quote: missing {', '.join(missing)}")

    delay_seconds = _float_or_none(quote.fields.get("delay_seconds"))
    if delay_seconds is None:
        return QuoteValidation(False, "incomplete quote: delay_seconds is invalid")
    if delay_seconds != 0 and not allow_delayed:
        if delay_seconds == -1:
            return QuoteValidation(False, "quote is end-of-day only")
        return QuoteValidation(False, f"quote is delayed by {delay_seconds:g} seconds")

    if quote.bid is None or quote.ask is None or quote.last_price is None:
        return QuoteValidation(False, "incomplete quote: bid, ask, or last_price is invalid")
    if quote.bid <= 0 or quote.ask <= 0 or quote.last_price <= 0:
        return QuoteValidation(False, "incomplete quote: bid, ask, or last_price is non-positive")
    if quote.bid > quote.ask:
        return QuoteValidation(False, "incomplete quote: bid is greater than ask")

    reference = now or utc_now()
    quote_time = quote_time_utc(quote)
    age = (reference - quote_time).total_seconds()
    if age > max_staleness_seconds:
        return QuoteValidation(False, f"quote is stale by {age:.1f} seconds")
    if age < -max_staleness_seconds:
        return QuoteValidation(False, "quote timestamp is unexpectedly in the future")
    return QuoteValidation(True)


def quote_time_utc(quote: QuoteState) -> datetime:
    lp_time = _float_or_none(quote.fields.get("lp_time"))
    if lp_time is not None and lp_time > 0:
        return datetime.fromtimestamp(lp_time, tz=UTC)
    last_update = _float_or_none(quote.fields.get("last_update"))
    if last_update is not None and last_update > 0:
        if last_update > 10_000_000_000:
            last_update = last_update / 1000
        return datetime.fromtimestamp(last_update, tz=UTC)
    return quote.received_at


def build_subscription_payload(
    api_key: str,
    symbols: list[str],
    *,
    include_minute_series: bool = False,
    daily_max_dp: int = 5,
    minute_max_dp: int = 200,
) -> dict[str, Any]:
    subscriptions: list[dict[str, Any]] = []
    for symbol in sorted(set(symbols)):
        subscriptions.append({"code": symbol, "type": "quote"})
        subscriptions.append(
            {
                "code": symbol,
                "type": "series",
                "bar_type": "day",
                "bar_interval": 1,
                "max_dp": max(daily_max_dp, 5),
            }
        )
        if include_minute_series:
            subscriptions.append(
                {
                    "code": symbol,
                    "type": "series",
                    "bar_type": "minute",
                    "bar_interval": 1,
                    "max_dp": minute_max_dp,
                }
            )
    if not subscriptions:
        raise ValueError("InsightSentry subscriptions cannot be empty")
    return {"api_key": api_key, "subscriptions": subscriptions}


def build_quote_subscription_payload(api_key: str, symbols: list[str]) -> dict[str, Any]:
    subscriptions = [
        {"code": symbol, "type": "quote"}
        for symbol in sorted({symbol for symbol in symbols if symbol.strip()})
    ]
    if not subscriptions:
        raise ValueError("InsightSentry subscriptions cannot be empty")
    return {"api_key": api_key, "subscriptions": subscriptions}


def parse_market_message(message: str) -> ParsedMarketMessage:
    if message == "pong":
        return ParsedMarketMessage("pong", "pong")
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return ParsedMarketMessage("unknown", message)

    if isinstance(payload, dict) and set(payload) == {"server_time"}:
        return ParsedMarketMessage("heartbeat", payload)
    if isinstance(payload, dict) and "error" in payload:
        return ParsedMarketMessage(
            "error",
            payload,
            fatal=str(payload.get("error")) in FATAL_ERRORS,
        )
    if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], list):
        return ParsedMarketMessage("quote", payload)
    if (
        isinstance(payload, dict)
        and str(payload.get("code") or payload.get("symbol") or "").strip()
        and any(field in payload for field in QUOTE_PATCH_FIELDS)
    ):
        return ParsedMarketMessage("quote", payload)
    if isinstance(payload, dict) and "series" in payload:
        return ParsedMarketMessage("series", payload)
    if isinstance(payload, dict) and "message" in payload:
        return ParsedMarketMessage("info", payload)
    return ParsedMarketMessage("unknown", payload)


class InsightSentryClient:
    """Small reusable WebSocket client; tests exercise parsing without opening sockets."""

    def __init__(self, api_key: str, symbols: list[str]) -> None:
        self.api_key = api_key
        self.symbols = symbols

    def subscription_payload(self) -> dict[str, Any]:
        return build_subscription_payload(self.api_key, self.symbols)

    async def stream(self):
        try:
            from websockets import connect
        except ImportError as exc:
            raise RuntimeError("Install the sanity extra to use InsightSentry streaming") from exc

        async with connect(LIVE_ENDPOINT, ping_interval=20, ping_timeout=12) as websocket:
            await websocket.send(json.dumps(self.subscription_payload()))
            async for message in websocket:
                yield parse_market_message(message)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
