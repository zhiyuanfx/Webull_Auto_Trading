from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from webull_auto_trading.config import Settings
from webull_auto_trading.domain import utc_now
from webull_auto_trading.market_data import (
    LIVE_ENDPOINT,
    ParsedMarketMessage,
    build_complete_subscription_payload,
    parse_market_message,
)
from webull_auto_trading.runtime import RuntimeService

LOGGER = logging.getLogger(__name__)
STREAM_STATES = Literal[
    "disabled_missing_credentials",
    "idle_no_symbols",
    "connecting",
    "connected",
    "reconnecting",
    "error",
]


class WebSocketLike(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


class WebSocketContext(Protocol):
    async def __aenter__(self) -> WebSocketLike: ...
    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...


ConnectFactory = Callable[..., WebSocketContext]
SleepFactory = Callable[[float], Awaitable[None]]


@dataclass(slots=True)
class StreamStatus:
    state: STREAM_STATES = "connecting"
    connected: bool = False
    desired_symbols: list[str] = field(default_factory=list)
    subscribed_symbols: list[str] = field(default_factory=list)
    last_message_at: str | None = None
    last_error: str | None = None
    reconnect_attempt: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InsightSentryQuoteStreamService:
    def __init__(
        self,
        runtime: RuntimeService,
        *,
        connect_factory: ConnectFactory | None = None,
        sleep: SleepFactory = asyncio.sleep,
        poll_seconds: float = 2.0,
        reconnect_base_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
    ) -> None:
        self.runtime = runtime
        self.settings: Settings = runtime.settings
        self.connect_factory = connect_factory
        self.sleep = sleep
        self.poll_seconds = poll_seconds
        self.reconnect_base_seconds = reconnect_base_seconds
        self.reconnect_max_seconds = reconnect_max_seconds
        self._status = StreamStatus()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._socket: WebSocketLike | None = None
        self._last_logged_event: str | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self.run(), name="insightsentry-quote-stream")

    async def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            await self._close_socket()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._set_status("idle_no_symbols", connected=False, subscribed_symbols=[])

    def status(self) -> dict[str, Any]:
        return self._status.to_dict()

    async def run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            api_key, credential_error = self._resolve_api_key()
            desired_subscriptions = self.runtime.market_subscription_requirements()
            desired_symbols = sorted(
                {
                    str(item.get("code") or "")
                    for item in desired_subscriptions
                    if str(item.get("code") or "")
                }
            )
            self._status.desired_symbols = desired_symbols
            if not api_key:
                self._set_status(
                    "disabled_missing_credentials",
                    connected=False,
                    subscribed_symbols=[],
                    last_error=credential_error,
                    reconnect_attempt=0,
                )
                self._log_activity_once(
                    "InsightSentryStreamDisabled",
                    credential_error or "InsightSentry stream credentials are missing",
                    level="warning",
                )
                await self._wait_or_stop(self.poll_seconds)
                continue
            if not desired_symbols:
                self._set_status(
                    "idle_no_symbols",
                    connected=False,
                    subscribed_symbols=[],
                    last_error=None,
                    reconnect_attempt=0,
                )
                self._log_activity_once(
                    "InsightSentryStreamIdle",
                    "InsightSentry quote stream idle: no enabled strategy symbols",
                )
                await self._wait_or_stop(self.poll_seconds)
                continue

            try:
                state: STREAM_STATES = "connecting" if attempt == 0 else "reconnecting"
                self._set_status(state, connected=False, reconnect_attempt=attempt)
                await self._connect_and_consume(
                    api_key,
                    desired_symbols,
                    desired_subscriptions,
                )
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt += 1
                message = _safe_error_message(exc)
                self._set_status(
                    "reconnecting",
                    connected=False,
                    subscribed_symbols=[],
                    last_error=message,
                    reconnect_attempt=attempt,
                )
                self._log_activity_once(
                    "InsightSentryStreamReconnecting",
                    f"InsightSentry quote stream reconnecting: {message}",
                    level="warning",
                )
                delay = min(
                    self.reconnect_base_seconds * (2 ** max(attempt - 1, 0)),
                    self.reconnect_max_seconds,
                )
                await self._wait_or_stop(delay)

    async def _connect_and_consume(
        self,
        api_key: str,
        symbols: list[str],
        subscriptions: list[dict[str, Any]],
    ) -> None:
        payload = build_complete_subscription_payload(api_key, subscriptions)
        async with self._connect() as websocket:
            self._socket = websocket
            await websocket.send(json.dumps(payload, separators=(",", ":")))
            self._set_status(
                "connected",
                connected=True,
                subscribed_symbols=symbols,
                last_error=None,
                reconnect_attempt=0,
            )
            self._log_activity_once(
                "InsightSentryStreamConnected",
                f"InsightSentry quote stream connected for {len(symbols)} symbol(s)",
                payload={"symbols": symbols},
            )
            while not self._stop.is_set():
                if self._should_replace_connection(api_key, subscriptions):
                    self._log_activity_once(
                        "InsightSentryStreamReplacing",
                        "InsightSentry quote stream reconnecting to replace subscriptions",
                    )
                    return
                try:
                    raw_message = await asyncio.wait_for(
                        websocket.recv(),
                        timeout=self.poll_seconds,
                    )
                except TimeoutError:
                    continue
                await self._handle_message(raw_message)

    def _connect(self) -> WebSocketContext:
        if self.connect_factory is not None:
            return self.connect_factory(LIVE_ENDPOINT, ping_interval=20, ping_timeout=12)
        try:
            from websockets import connect
        except ImportError as exc:
            raise RuntimeError("Install websockets to use InsightSentry streaming") from exc
        return connect(LIVE_ENDPOINT, ping_interval=20, ping_timeout=12)

    async def _handle_message(self, raw_message: str | bytes) -> None:
        message = (
            raw_message.decode("utf-8", errors="replace")
            if isinstance(raw_message, bytes)
            else raw_message
        )
        parsed = parse_market_message(message)
        if parsed.kind in {"pong", "heartbeat", "info"}:
            return
        received_at = utc_now()
        self._status.last_message_at = received_at.isoformat()
        if parsed.kind == "error":
            self._handle_error(parsed)
            if parsed.fatal:
                raise RuntimeError(self._status.last_error or "fatal InsightSentry stream error")
            return
        if parsed.kind in {"quote", "series"}:
            self.runtime.ingest_market_message(message, received_at=received_at)
            return
        if parsed.kind == "unknown":
            self._status.last_error = "Unknown InsightSentry message ignored"

    def _handle_error(self, parsed: ParsedMarketMessage) -> None:
        error = "InsightSentry stream error"
        payload: dict[str, Any] = {}
        if isinstance(parsed.payload, dict):
            payload = _safe_payload(parsed.payload)
            code = str(payload.get("error") or "error")
            message = str(payload.get("message") or "").strip()
            symbol = str(payload.get("symbol") or "").strip()
            error = f"{code}: {message}" if message else code
            if symbol:
                error = f"{error} ({symbol})"
        self._set_status("error", connected=self._status.connected, last_error=error)
        self.runtime.repository.log_activity(
            "InsightSentryStreamError",
            error,
            level="error" if parsed.fatal else "warning",
            payload=payload,
        )

    def _should_replace_connection(
        self,
        api_key: str,
        subscriptions: list[dict[str, Any]],
    ) -> bool:
        current_key, _credential_error = self._resolve_api_key()
        return (
            current_key != api_key
            or self.runtime.market_subscription_requirements() != subscriptions
        )

    def _desired_symbols(self) -> list[str]:
        return sorted(
            {
                instance.symbol
                for instance in self.runtime.repository.list_strategy_instances()
                if instance.enabled and instance.symbol.strip()
            }
        )

    def _resolve_api_key(self) -> tuple[str | None, str | None]:
        if not self.settings.insightsentry_stream_enabled:
            return None, "InsightSentry streaming is disabled"
        direct_key = self.settings.insightsentry_api_key.strip()
        if direct_key:
            return direct_key, None
        websocket_key = self.settings.insightsentry_websocket_key.strip()
        if not websocket_key:
            return None, "InsightSentry stream credentials are missing"
        expiration = self.settings.insightsentry_websocket_key_expiration.strip()
        if not expiration:
            return websocket_key, None
        expires_at = _parse_expiration(expiration)
        if expires_at is None:
            return None, "INSIGHTSENTRY_WEBSOCKET_KEY_EXPIRATION is invalid"
        if expires_at <= utc_now():
            return None, "INSIGHTSENTRY_WEBSOCKET_KEY is expired"
        return websocket_key, None

    async def _wait_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _close_socket(self) -> None:
        socket = self._socket
        self._socket = None
        if socket is None:
            return
        try:
            await socket.close()
        except Exception as exc:
            LOGGER.debug("InsightSentry socket close failed: %s", _safe_error_message(exc))

    def _set_status(
        self,
        state: STREAM_STATES,
        *,
        connected: bool,
        desired_symbols: list[str] | None = None,
        subscribed_symbols: list[str] | None = None,
        last_error: str | None | object = ...,
        reconnect_attempt: int | None = None,
    ) -> None:
        self._status.state = state
        self._status.connected = connected
        if desired_symbols is not None:
            self._status.desired_symbols = desired_symbols
        if subscribed_symbols is not None:
            self._status.subscribed_symbols = subscribed_symbols
        if last_error is not ...:
            self._status.last_error = (
                last_error
                if isinstance(last_error, str) or last_error is None
                else str(last_error)
            )
        if reconnect_attempt is not None:
            self._status.reconnect_attempt = reconnect_attempt

    def _log_activity_once(
        self,
        event_type: str,
        message: str,
        *,
        level: str = "info",
        payload: dict[str, Any] | None = None,
    ) -> None:
        key = f"{event_type}:{message}"
        if self._last_logged_event == key:
            return
        self._last_logged_event = key
        self.runtime.repository.log_activity(event_type, message, level=level, payload=payload)


def _parse_expiration(value: str) -> datetime | None:
    try:
        timestamp = float(value)
    except ValueError:
        timestamp = None
    if timestamp is not None:
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=UTC)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    secret_markers = ("api_key", "secret", "token", "password", "authorization")
    return {
        str(key): value
        for key, value in payload.items()
        if not any(marker in str(key).lower() for marker in secret_markers)
    }


def _safe_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return exc.__class__.__name__
    return message
