from __future__ import annotations

import pytest

from webull_bridge.domain import EventStatus, RouteConfig
from webull_bridge.executor import BridgeExecutor
from webull_bridge.persistence import Ledger
from webull_bridge.webull import WebullError


class FakeWebull:
    configured = True

    def __init__(self, *, preview_error: bool = False) -> None:
        self.preview_error = preview_error
        self.previewed: list[dict] = []
        self.placed: list[dict] = []
        self.replaced: list[dict] = []
        self.cancelled: list[str] = []
        self._positions = [{"symbol": "1OZ", "quantity": "1", "instrument_type": "EQUITY"}]

    async def preview_order(self, account_id: str, order: dict) -> dict:
        if self.preview_error:
            raise WebullError("PREVIEW_FAILED", "preview failed")
        self.previewed.append(order)
        return {"estimated_cost": "1", "estimated_transaction_fee": "0"}

    async def place_order(self, account_id: str, order: dict) -> dict:
        self.placed.append(order)
        return {"order_id": "webull-1", "client_order_id": order["client_order_id"]}

    async def replace_order(self, account_id: str, change: dict) -> dict:
        self.replaced.append(change)
        return {"client_order_id": change["client_order_id"]}

    async def cancel_order(self, account_id: str, client_order_id: str) -> dict:
        self.cancelled.append(client_order_id)
        return {"client_order_id": client_order_id}

    async def positions(self, account_id: str) -> list[dict]:
        return self._positions

    async def open_orders(self, account_id: str) -> list[dict]:
        return []

    async def list_accounts(self) -> list[dict]:
        return []


def make_executor(tmp_path, fake: FakeWebull) -> tuple[Ledger, BridgeExecutor]:
    ledger = Ledger(tmp_path / "bridge.db")
    ledger.initialize()
    ledger.bootstrap(
        execution_enabled=True,
        route=RouteConfig(route_id="tv", account_id="acct", allowed_symbols=["1OZ"]),
        secret="secret",
    )
    return ledger, BridgeExecutor(ledger, fake)


@pytest.mark.asyncio
async def test_market_order_is_previewed_before_placement(tmp_path) -> None:
    fake = FakeWebull()
    ledger, executor = make_executor(tmp_path, fake)
    event, _ = ledger.create_event(
        "tv",
        "evt-1",
        "BUY",
        "1OZ",
        {
            "secret": "<redacted>",
            "event_id": "evt-1",
            "action": "BUY",
            "symbol": "1OZ",
            "quantity": "1",
        },
    )

    await executor.process_event(event["id"])

    assert fake.previewed[0]["order_type"] == "MARKET"
    assert fake.placed[0]["client_order_id"].startswith("WB")
    assert ledger.get_event(event["id"])["status"] == EventStatus.SUBMITTED.value


@pytest.mark.asyncio
async def test_preview_failure_stops_placement(tmp_path) -> None:
    fake = FakeWebull(preview_error=True)
    ledger, executor = make_executor(tmp_path, fake)
    event, _ = ledger.create_event(
        "tv",
        "evt-1",
        "BUY",
        "1OZ",
        {
            "secret": "<redacted>",
            "event_id": "evt-1",
            "action": "BUY",
            "symbol": "1OZ",
            "quantity": "1",
        },
    )

    await executor.process_event(event["id"])

    assert fake.placed == []
    assert ledger.get_event(event["id"])["status"] == EventStatus.FAILED.value


@pytest.mark.asyncio
async def test_limit_order_payload_contains_limit_price(tmp_path) -> None:
    fake = FakeWebull()
    ledger, executor = make_executor(tmp_path, fake)
    event, _ = ledger.create_event(
        "tv",
        "evt-1",
        "SELL",
        "1OZ",
        {
            "secret": "<redacted>",
            "event_id": "evt-1",
            "action": "SELL",
            "symbol": "1OZ",
            "quantity": "1",
            "order_type": "LIMIT",
            "limit_price": "2.50",
        },
    )

    await executor.process_event(event["id"])

    assert fake.placed[0]["limit_price"] == "2.50"


@pytest.mark.asyncio
async def test_cancel_uses_target_client_order_id(tmp_path) -> None:
    fake = FakeWebull()
    ledger, executor = make_executor(tmp_path, fake)
    event, _ = ledger.create_event(
        "tv",
        "evt-1",
        "CANCEL",
        None,
        {
            "secret": "<redacted>",
            "event_id": "evt-1",
            "action": "CANCEL",
            "target_client_order_id": "abc",
        },
    )

    await executor.process_event(event["id"])

    assert fake.cancelled == ["abc"]
    assert ledger.get_event(event["id"])["status"] == EventStatus.CANCELLED.value
