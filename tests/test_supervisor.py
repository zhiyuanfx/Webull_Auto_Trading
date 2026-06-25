import asyncio
from pathlib import Path

import pytest

from strategy_desk.gateway import ExecutionGateway
from strategy_desk.persistence import Ledger
from strategy_desk.plugins import PluginRegistry
from strategy_desk.supervisor import StrategySupervisor


@pytest.mark.asyncio
async def test_supervisor_spawns_sanitized_strategy_worker_and_stops_cleanly(tmp_path):
    ledger = Ledger(tmp_path / "worker.sqlite3")
    ledger.initialize()
    gateway = ExecutionGateway(ledger)
    supervisor = StrategySupervisor(gateway, ledger)
    plugin = PluginRegistry(Path("strategies")).discover()[0]

    await supervisor.start("worker-1", "SIM-1", plugin, {"enabled": False})
    await asyncio.sleep(0.2)
    assert supervisor.status()[0]["alive"] is True
    await supervisor.stop("worker-1", "test complete")
    assert supervisor.status() == []


@pytest.mark.asyncio
async def test_supervisor_rejects_failed_worker_startup_and_cleans_up(tmp_path):
    strategy_root = tmp_path / "strategies"
    plugin_path = strategy_root / "broken"
    plugin_path.mkdir(parents=True)
    (plugin_path / "strategy.yaml").write_text(
        """id: broken
version: 1.0.0
entrypoint: strategy:Strategy
name: {en: Broken, zh_cn: 故障}
description: {en: Test, zh_cn: 测试}
assets: [EQUITY]
required_events: []
parameters: {type: object}
"""
    )
    (plugin_path / "strategy.py").write_text(
        """from strategy_desk.strategy import BaseStrategy
class Strategy(BaseStrategy):
    async def on_start(self, context):
        raise RuntimeError("startup failed")
"""
    )
    ledger = Ledger(tmp_path / "broken.sqlite3")
    ledger.initialize()
    supervisor = StrategySupervisor(ExecutionGateway(ledger), ledger)
    plugin = PluginRegistry(strategy_root).discover()[0]

    with pytest.raises(RuntimeError, match="exited during startup"):
        await supervisor.start("broken-1", "SIM-1", plugin, {})

    assert supervisor.status() == []
    events = ledger.audit_events("broken-1")
    assert events[0]["kind"] == "STRATEGY_WORKER_EXITED"
