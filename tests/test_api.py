from fastapi.testclient import TestClient

from strategy_desk.api import create_app
from strategy_desk.config import Settings
from strategy_desk.persistence import Ledger


def test_health_and_create_simulator_account(tmp_path):
    settings = Settings(
        strategy_desk_db_path=tmp_path / "api.sqlite3",
        strategy_root="strategies",
    )
    with TestClient(create_app(settings)) as client:
        frontend = client.get("/")
        assert frontend.status_code == 200
        assert "Strategy Desk" in frontend.text
        assert client.get("/api/health").json()["status"] == "ok"
        response = client.post(
            "/api/simulator/accounts",
            json={"name": "Test", "initial_cash": "50000"},
        )
        assert response.status_code == 201
        account = client.get("/api/simulator/accounts").json()[0]
        assert account["cash"] == "50000"
        assert account["leverage"] == "1"
        reset = client.post(f"/api/simulator/accounts/{account['id']}/reset", json={})
        assert reset.status_code == 201
        assert reset.json()["id"] != account["id"]
        assert len(client.get("/api/simulator/accounts").json()) == 2


def test_startup_marks_orphaned_running_instance_degraded(tmp_path):
    database = tmp_path / "recovery.sqlite3"
    ledger = Ledger(database)
    ledger.initialize()
    ledger.save_instance(
        {
            "id": "orphan",
            "plugin_id": "example_momentum",
            "plugin_version": "0.1.0",
            "plugin_source_hash": "old",
            "mode": "LOCAL_SIM",
            "account_id": "SIM-OLD",
            "feed_source": "REPLAY",
            "state": "RUNNING",
            "config": {},
        }
    )
    settings = Settings(strategy_desk_db_path=database, strategy_root="strategies")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/instances").json()[0]["state"] == "DEGRADED"
    assert ledger.audit_events("orphan")[0]["kind"] == "STALE_RUNTIME_RECOVERED"


def test_live_start_requires_environment_gate_even_with_confirmation(tmp_path):
    settings = Settings(
        strategy_desk_db_path=tmp_path / "live-gate.sqlite3",
        strategy_root="strategies",
        webull_prod_app_key="prod-key",
        webull_prod_app_secret="prod-secret",
        webull_live_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        create = client.post(
            "/api/instances",
            json={
                "name": "Live gate",
                "plugin_id": "example_momentum",
                "plugin_version": "0.1.0",
                "mode": "WEBULL_LIVE",
                "feed_source": "REPLAY",
                "account_id": "real-account",
                "symbols": ["AAPL"],
            },
        )
        assert create.status_code == 201
        start = client.post(
            f"/api/instances/{create.json()['id']}/start",
            json={"live_confirmation": "ENABLE LIVE TRADING"},
        )
        assert start.status_code == 409
        assert "WEBULL_LIVE_ENABLED=true" in start.json()["detail"]
        assert client.get("/api/health").json()["workers"] == []
