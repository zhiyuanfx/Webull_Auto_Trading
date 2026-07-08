import asyncio

from webull_auto_trading.config import Settings
from webull_auto_trading.domain import (
    LiveIntentAction,
    LiveIntentStatus,
    OrderSide,
    RuntimeMode,
    StrategyInstance,
)
from webull_auto_trading.live_execution import (
    LiveExecutionBlocked,
    LiveMarketOrderRequest,
    WebullLiveExecutionAdapter,
    build_market_order_request,
    stable_client_order_id,
)
from webull_auto_trading.persistence import RuntimeRepository


def test_build_market_order_request_uses_documented_webull_fields() -> None:
    request = build_market_order_request(
        client_order_id="client-1",
        webull_symbol="AAPL",
        asset_class="stock",
        side=OrderSide.BUY,
        quantity=1.5,
    )

    assert request == {
        "client_order_id": "client-1",
        "combo_type": "NORMAL",
        "symbol": "AAPL",
        "instrument_type": "EQUITY",
        "market": "US",
        "order_type": "MARKET",
        "quantity": "1.5",
        "side": "BUY",
        "time_in_force": "DAY",
        "entrust_type": "QTY",
        "support_trading_session": "CORE",
    }


def test_build_market_order_request_supports_futures_market_order() -> None:
    request = build_market_order_request(
        client_order_id="client-1",
        webull_symbol="ESZ5",
        asset_class="futures",
        side=OrderSide.BUY,
        quantity=1,
    )

    assert request == {
        "client_order_id": "client-1",
        "combo_type": "NORMAL",
        "symbol": "ESZ5",
        "instrument_type": "FUTURES",
        "market": "US",
        "order_type": "MARKET",
        "quantity": "1",
        "side": "BUY",
        "time_in_force": "DAY",
        "entrust_type": "QTY",
    }


def test_stable_client_order_id_is_deterministic_and_short_enough() -> None:
    first = stable_client_order_id(
        strategy_instance_id="st-1",
        cycle_id="cyc-1",
        action=LiveIntentAction.OPEN_MARKET,
        side=OrderSide.BUY,
        webull_symbol="AAPL",
        quantity=1,
    )
    second = stable_client_order_id(
        strategy_instance_id="st-1",
        cycle_id="cyc-1",
        action=LiveIntentAction.OPEN_MARKET,
        side=OrderSide.BUY,
        webull_symbol="AAPL",
        quantity=1,
    )

    assert first == second
    assert len(first) == 32


def test_live_adapter_persists_intent_before_submit(tmp_path) -> None:
    repo = RuntimeRepository(tmp_path / "runtime.sqlite3")
    repo.init_db()
    client = FakeLiveOrderClient(repo)
    adapter = WebullLiveExecutionAdapter(
        Settings(
            webull_account_stock_margin_id="acct-1",
            live_execution_master_enable=True,
            _env_file=None,
        ),
        repo,
        client=client,
    )

    intent = asyncio.run(
        adapter.submit_market_order(
            LiveMarketOrderRequest(
                strategy=live_strategy(),
                cycle_id="cyc-1",
                action=LiveIntentAction.OPEN_MARKET,
                side=OrderSide.BUY,
                quantity=1,
            ),
            runtime_mode=RuntimeMode.LIVE,
            global_pause=False,
        )
    )

    assert client.seen_existing_intents == 1
    assert intent.status == LiveIntentStatus.SUBMITTED
    assert intent.response == {"client_order_id": intent.client_order_id, "order_id": "wb-1"}
    assert repo.list_live_order_intents()[0].status == LiveIntentStatus.SUBMITTED


def test_live_adapter_blocks_duplicate_unresolved_intent(tmp_path) -> None:
    repo = RuntimeRepository(tmp_path / "runtime.sqlite3")
    repo.init_db()
    adapter = WebullLiveExecutionAdapter(
        Settings(
            webull_account_stock_margin_id="acct-1",
            live_execution_master_enable=True,
            _env_file=None,
        ),
        repo,
        client=FakeLiveOrderClient(),
    )
    request = LiveMarketOrderRequest(
        strategy=live_strategy(),
        cycle_id="cyc-1",
        action=LiveIntentAction.OPEN_MARKET,
        side=OrderSide.BUY,
        quantity=1,
    )

    asyncio.run(
        adapter.submit_market_order(
            request,
            runtime_mode=RuntimeMode.LIVE,
            global_pause=False,
        )
    )

    try:
        asyncio.run(
            adapter.submit_market_order(
                request,
                runtime_mode=RuntimeMode.LIVE,
                global_pause=False,
            )
        )
    except LiveExecutionBlocked as exc:
        assert "unresolved live order intent" in str(exc)
    else:
        raise AssertionError("Expected duplicate live intent to be blocked")


def test_live_adapter_requires_master_enable(tmp_path) -> None:
    repo = RuntimeRepository(tmp_path / "runtime.sqlite3")
    repo.init_db()
    adapter = WebullLiveExecutionAdapter(
        Settings(webull_account_stock_margin_id="acct-1", _env_file=None),
        repo,
        client=FakeLiveOrderClient(),
    )

    try:
        asyncio.run(
            adapter.submit_market_order(
                LiveMarketOrderRequest(
                    strategy=live_strategy(),
                    cycle_id="cyc-1",
                    action=LiveIntentAction.OPEN_MARKET,
                    side=OrderSide.BUY,
                    quantity=1,
                ),
                runtime_mode=RuntimeMode.LIVE,
                global_pause=False,
            )
        )
    except LiveExecutionBlocked as exc:
        assert "master enable is off" in str(exc)
    else:
        raise AssertionError("Expected live execution to require master enable")


def live_strategy() -> StrategyInstance:
    return StrategyInstance(
        id="st-live",
        strategy_name="recycle_buy",
        symbol="NASDAQ:AAPL",
        market_data_symbol="NASDAQ:AAPL",
        webull_symbol="AAPL",
        account_alias="stock_margin",
        asset_class="stock",
        enabled=True,
        live_execution_enabled=True,
    )


class FakeLiveOrderClient:
    def __init__(self, repo: RuntimeRepository | None = None) -> None:
        self.repo = repo
        self.seen_existing_intents = 0

    async def place_order(self, account_id: str, order: dict[str, str]) -> dict[str, str]:
        assert account_id == "acct-1"
        if self.repo is not None:
            self.seen_existing_intents = len(self.repo.list_live_order_intents())
        return {"client_order_id": order["client_order_id"], "order_id": "wb-1"}
