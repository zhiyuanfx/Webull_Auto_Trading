from datetime import UTC, datetime
from pathlib import Path

import pytest

from strategy_desk.plugins import PluginRegistry


class OrdersThatMustNotRun:
    async def place(self, **_values):
        raise AssertionError("safe defaults must not place orders")


class Context:
    orders = OrdersThatMustNotRun()

    async def log(self, *_args, **_values):
        return None


@pytest.mark.asyncio
async def test_reference_strategy_is_safe_by_default():
    registry = PluginRegistry(Path("strategies"))
    plugin = next(item for item in registry.discover() if item.manifest.id == "example_momentum")
    strategy = registry.load_class(plugin)()
    await strategy.on_start(Context())
    await strategy.on_quote(
        {
            "symbol": "AAPL",
            "bid": "100",
            "ask": "100.01",
            "source_at": datetime.now(UTC).isoformat(),
        }
    )
    assert strategy.ticket_id is None
