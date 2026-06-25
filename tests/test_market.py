from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from strategy_desk.domain import MarketQuote
from strategy_desk.gateway import ExecutionGateway
from strategy_desk.market import MarketDataHub
from strategy_desk.persistence import Ledger


@pytest.mark.asyncio
async def test_replay_refreshes_receive_time_and_delivers_quotes(tmp_path):
    ledger = Ledger(tmp_path / "replay.sqlite3")
    ledger.initialize()
    hub = MarketDataHub(ExecutionGateway(ledger))
    old = datetime.now(UTC) - timedelta(days=1)
    quote = MarketQuote(
        symbol="AAPL",
        bid=Decimal("100"),
        ask=Decimal("100.01"),
        source_at=old,
        received_at=old,
    )
    path = tmp_path / "quotes.jsonl"
    path.write_text(quote.model_dump_json() + "\n")
    delivered = []

    async def receive(value):
        delivered.append(value)

    await hub.replay(path, on_quote=receive)
    assert delivered[0].received_at > old
    assert hub.last_quotes["AAPL"] == delivered[0]
