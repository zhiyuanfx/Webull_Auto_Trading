from fastapi.testclient import TestClient

from webull_bridge.api import app
from webull_bridge.config import get_settings


def test_webhook_secret_validation_and_duplicate(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WEBULL_BRIDGE_DB_PATH", str(tmp_path / "bridge.db"))
    monkeypatch.setenv("BRIDGE_DEFAULT_SECRET", "secret")
    monkeypatch.setenv("WEBULL_ACCOUNT_ID", "acct")
    monkeypatch.setenv("BRIDGE_EXECUTION_ENABLED", "false")
    get_settings.cache_clear()

    with TestClient(app) as client:
        bad = client.post(
            "/webhook/tradingview/tv",
            json={
                "secret": "wrong",
                "event_id": "evt-1",
                "action": "BUY",
                "symbol": "1OZ",
                "quantity": "1",
            },
        )
        assert bad.status_code == 401

        good = client.post(
            "/webhook/tradingview/tv",
            json={
                "secret": "secret",
                "event_id": "evt-1",
                "action": "BUY",
                "symbol": "1OZ",
                "quantity": "1",
            },
        )
        assert good.status_code == 200
        assert good.json()["status"] == "queued"

        duplicate = client.post(
            "/webhook/tradingview/tv",
            json={
                "secret": "secret",
                "event_id": "evt-1",
                "action": "BUY",
                "symbol": "1OZ",
                "quantity": "1",
            },
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["status"] == "duplicate"

        events = client.get("/api/events").json()
        assert len(events) == 1
        assert (
            "secret" not in events[0]["payload"] or events[0]["payload"]["secret"] == "<redacted>"
        )


def test_health_shape_is_ui_compatible(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WEBULL_BRIDGE_DB_PATH", str(tmp_path / "bridge.db"))
    monkeypatch.setenv("BRIDGE_DEFAULT_SECRET", "secret")
    monkeypatch.setenv("WEBULL_ACCOUNT_ID", "acct")
    get_settings.cache_clear()

    with TestClient(app) as client:
        health = client.get("/api/health")

    assert health.status_code == 200
    body = health.json()
    assert {
        "status",
        "database",
        "webull_configured",
        "execution_enabled",
        "token_dir",
        "routes",
    } <= set(body)
    assert "secret_hash" not in body["routes"][0]
