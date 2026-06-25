import asyncio
import os

import pytest

from strategy_desk.config import get_settings
from strategy_desk.domain import ExecutionMode
from strategy_desk.webull import WebullMarketStream, WebullTradingAdapter

pytestmark = [pytest.mark.integration, pytest.mark.uat]


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_WEBULL_UAT_TESTS") != "1",
    reason="Set RUN_WEBULL_UAT_TESTS=1 for an explicit read-only UAT check",
)
async def test_uat_account_discovery_read_only():
    settings = get_settings()
    assert settings.uat_configured
    adapter = WebullTradingAdapter(settings, ExecutionMode.WEBULL_UAT)
    accounts = await adapter.list_accounts()
    assert accounts
    account_id = accounts[0]["account_id"]
    balance = await adapter.account_balance(account_id)
    positions = await adapter.account_positions(account_id)
    contracts = await adapter.futures_contracts("MGC")
    assert isinstance(balance, dict)
    assert isinstance(positions, list)
    assert contracts
    assert {"symbol", "contract_month", "min_tick", "size"} <= contracts[0].keys()


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_WEBULL_UAT_TESTS") != "1",
    reason="Set RUN_WEBULL_UAT_TESTS=1 for an explicit read-only UAT check",
)
async def test_uat_market_stream_connects_and_subscribes_read_only():
    stream = WebullMarketStream(get_settings(), ExecutionMode.WEBULL_UAT)

    async def receive(_topic, _value):
        return None

    task = asyncio.create_task(stream.run(["AAPL"], "US_STOCK", ["QUOTE"], receive))
    try:
        await asyncio.wait_for(stream.wait_ready(), timeout=20)
    finally:
        await stream.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
