from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from webull_auto_trading.config import Settings
from webull_auto_trading.domain import StrategyInstance
from webull_auto_trading.insightsentry_stream import InsightSentryQuoteStreamService
from webull_auto_trading.runtime import RuntimeService


def test_stream_status_is_disabled_without_credentials(tmp_path) -> None:
    async def scenario() -> None:
        runtime = make_runtime(tmp_path)
        service = InsightSentryQuoteStreamService(runtime, poll_seconds=0.01)

        service.start()
        await wait_until(lambda: service.status()["state"] == "disabled_missing_credentials")
        await service.stop()

        assert service.status()["connected"] is False

    asyncio.run(scenario())


def test_stream_status_is_idle_without_enabled_symbols(tmp_path) -> None:
    async def scenario() -> None:
        runtime = make_runtime(tmp_path, insightsentry_api_key="direct-key")
        service = InsightSentryQuoteStreamService(runtime, poll_seconds=0.01)

        service.start()
        await wait_until(lambda: service.status()["state"] == "idle_no_symbols")
        await service.stop()

        assert service.status()["subscribed_symbols"] == []

    asyncio.run(scenario())


def test_stream_sends_quote_only_subscription_and_ingests_top_level_quote(tmp_path) -> None:
    async def scenario() -> None:
        runtime = make_runtime(tmp_path, insightsentry_api_key="direct-key")
        runtime.repository.upsert_strategy_instance(
            StrategyInstance(id="st-1", strategy_name="day_many_bian", symbol="NASDAQ:AAPL")
        )
        socket = MockWebSocket(
            [
                '{"message":"Connecting...","server":"test"}',
                '{"code":"NASDAQ:AAPL","bid":100.0,"ask":100.1}',
                '{"code":"NASDAQ:AAPL","last_price":100.05,"delay_seconds":0}',
            ]
        )
        service = InsightSentryQuoteStreamService(
            runtime,
            connect_factory=lambda *_args, **_kwargs: MockWebSocketContext(socket),
            poll_seconds=0.01,
        )

        service.start()
        await wait_until(lambda: bool(socket.sent))
        await wait_until(lambda: runtime.current_quotes().get("NASDAQ:AAPL") is not None)
        await service.stop()

        payload = json.loads(socket.sent[0])
        assert payload == {
            "api_key": "direct-key",
            "subscriptions": [{"code": "NASDAQ:AAPL", "type": "quote"}],
        }
        quote = runtime.current_quotes()["NASDAQ:AAPL"]
        assert quote.fields["bid"] == 100.0
        assert quote.fields["ask"] == 100.1
        assert quote.fields["last_price"] == 100.05
        assert quote.fields["delay_seconds"] == 0
        assert socket.closed is True

    asyncio.run(scenario())


def test_stream_rejects_expired_cached_websocket_key(tmp_path) -> None:
    async def scenario() -> None:
        runtime = make_runtime(
            tmp_path,
            insightsentry_websocket_key="cached-key",
            insightsentry_websocket_key_expiration=(
                datetime.now(UTC) - timedelta(days=1)
            ).isoformat(),
        )
        runtime.repository.upsert_strategy_instance(
            StrategyInstance(id="st-1", strategy_name="day_many_bian", symbol="NASDAQ:AAPL")
        )
        service = InsightSentryQuoteStreamService(runtime, poll_seconds=0.01)

        service.start()
        await wait_until(lambda: service.status()["state"] == "disabled_missing_credentials")
        await service.stop()

        assert service.status()["last_error"] == "INSIGHTSENTRY_WEBSOCKET_KEY is expired"

    asyncio.run(scenario())


class MockWebSocket:
    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        self.sent: list[str] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(1)
        return "pong"

    async def close(self) -> None:
        self.closed = True


class MockWebSocketContext:
    def __init__(self, socket: MockWebSocket) -> None:
        self.socket = socket

    async def __aenter__(self) -> MockWebSocket:
        return self.socket

    async def __aexit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        await self.socket.close()


async def wait_until(predicate: Callable[[], bool], deadline_seconds: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + deadline_seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")


def make_runtime(tmp_path, **settings: Any) -> RuntimeService:
    settings.setdefault("insightsentry_api_key", "")
    settings.setdefault("insightsentry_websocket_key", "")
    settings.setdefault("insightsentry_websocket_key_expiration", "")
    runtime = RuntimeService(
        Settings(
            runtime_db_path=tmp_path / "runtime.sqlite3",
            strategies_test_config_path=tmp_path / "strategies.test.yml",
            strategies_live_config_path=tmp_path / "strategies.live.yml",
            **settings,
        )
    )
    runtime.initialize(seed_config=False)
    return runtime
