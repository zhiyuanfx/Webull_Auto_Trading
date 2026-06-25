from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from strategy_desk.api import AppState, FuturesBindingConfig, InstanceCreate
from strategy_desk.config import Settings
from strategy_desk.domain import ContractMode, StrategyState
from strategy_desk.futures import FuturesContract, SettlementType


def future_contract(symbol: str, month: str, critical_offset: int) -> FuturesContract:
    today = datetime.now(ZoneInfo("America/New_York")).date()
    return FuturesContract(
        symbol=symbol,
        code="MGC",
        instrument_id=symbol,
        contract_month=month,
        settlement=SettlementType.PHYSICAL,
        first_notice_date=today + timedelta(days=critical_offset),
        last_trading_date=today + timedelta(days=critical_offset),
        status="OC",
        size=Decimal("10"),
        min_tick=Decimal("0.1"),
    )


@pytest.mark.asyncio
async def test_flat_auto_roll_rebinds_and_persists(tmp_path):
    desk = AppState(
        Settings(strategy_desk_db_path=tmp_path / "expiry.sqlite3", strategy_root="strategies")
    )
    old = future_contract("MGCM6", "202606", 2)
    new = future_contract("MGCQ6", "202608", 90)
    config = InstanceCreate(
        name="Roll test",
        plugin_id="example_momentum",
        plugin_version="1.0.0",
        account_id="SIM-TEST",
        symbols=[old.symbol],
        contract=FuturesBindingConfig(
            mode=ContractMode.AUTO_ROLL,
            product_code="MGC",
            current_symbol=old.symbol,
            contracts=[old, new],
        ),
    )
    instance = {
        "id": "roll-instance",
        "plugin_id": config.plugin_id,
        "plugin_version": config.plugin_version,
        "plugin_source_hash": "test",
        "mode": config.mode,
        "account_id": config.account_id,
        "feed_source": config.feed_source,
        "state": StrategyState.RUNNING,
        "config": config.model_dump(mode="json"),
    }
    desk.ledger.save_instance(instance)

    await desk.evaluate_expiry(desk.ledger.instance("roll-instance"))

    saved = InstanceCreate.model_validate(desk.ledger.instance("roll-instance")["config"])
    assert saved.contract.current_symbol == new.symbol
    assert saved.symbols == [new.symbol]
    assert desk.ledger.load_checkpoint("roll-instance", "__system_roll__")["stage"] == "COMPLETE"
