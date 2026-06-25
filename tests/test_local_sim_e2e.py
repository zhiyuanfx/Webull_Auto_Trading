import time

from fastapi.testclient import TestClient

from strategy_desk.api import create_app
from strategy_desk.config import Settings


def test_local_simulator_strategy_lifecycle_end_to_end(tmp_path):
    strategy_root = tmp_path / "strategies"
    plugin = strategy_root / "readiness_probe"
    plugin.mkdir(parents=True)
    (plugin / "strategy.yaml").write_text(
        """id: readiness_probe
version: 1.0.0
entrypoint: strategy:Strategy
name: {en: Readiness Probe, zh_cn: 就绪探针}
description: {en: Local test, zh_cn: 本地测试}
assets: [EQUITY]
required_events: [quote]
parameters: {type: object}
"""
    )
    (plugin / "strategy.py").write_text(
        """from decimal import Decimal
from strategy_desk.domain import AssetClass, OrderType, Side
from strategy_desk.strategy import BaseStrategy

class Strategy(BaseStrategy):
    def __init__(self, parameters=None):
        self.context = None
        self.placed = False

    async def on_start(self, context):
        self.context = context

    async def on_quote(self, event):
        if self.placed:
            return
        self.placed = True
        await self.context.orders.place(
            symbol=event["symbol"],
            asset_class=AssetClass.EQUITY,
            side=Side.BUY,
            quantity=Decimal("2"),
            order_type=OrderType.MARKET,
        )
"""
    )
    settings = Settings(
        strategy_desk_db_path=tmp_path / "e2e.sqlite3",
        strategy_root=strategy_root,
    )
    with TestClient(create_app(settings)) as client:
        account_id = client.post(
            "/api/simulator/accounts",
            json={"name": "E2E", "initial_cash": "1000", "commission_per_unit": "1"},
        ).json()["id"]
        plugin_info = client.get("/api/plugins").json()[0]
        instance_id = client.post(
            "/api/instances",
            json={
                "name": "E2E strategy",
                "plugin_id": plugin_info["id"],
                "plugin_version": plugin_info["version"],
                "mode": "LOCAL_SIM",
                "feed_source": "REPLAY",
                "account_id": account_id,
                "symbols": ["AAPL"],
            },
        ).json()["id"]
        started = client.post(
            f"/api/instances/{instance_id}/start", json={"live_confirmation": None}
        )
        assert started.status_code == 200
        quote = {
            "symbol": "AAPL",
            "bid": "99.90",
            "ask": "100.00",
            "bid_size": "10",
            "ask_size": "10",
            "source_at": "2026-06-23T16:00:00Z",
        }
        assert client.post("/api/market/quotes", json=quote).status_code == 202

        detail = None
        for _ in range(40):
            detail = client.get(f"/api/instances/{instance_id}/detail").json()
            if detail["orders"] and detail["orders"][0]["status"] == "FILLED":
                break
            time.sleep(0.05)

        assert detail["orders"][0]["status"] == "FILLED"
        assert detail["positions"][0]["quantity"] == "2"
        assert detail["runs"][0]["status"] == "RUNNING"
        account = next(
            item
            for item in client.get("/api/simulator/accounts").json()
            if item["id"] == account_id
        )
        assert account["cash"] == "798.00"
        assert client.post(f"/api/instances/{instance_id}/stop").status_code == 200
        assert client.get(f"/api/instances/{instance_id}/runs").json()[0]["status"] == "STOPPED"
