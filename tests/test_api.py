from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

import webull_auto_trading.api as api


def test_market_stream_status_endpoint_returns_safe_status(monkeypatch) -> None:
    fake_service = FakeStreamService(
        {
            "state": "connected",
            "connected": True,
            "desired_symbols": ["NASDAQ:AAPL"],
            "subscribed_symbols": ["NASDAQ:AAPL"],
            "last_message_at": "2026-07-06T12:00:00+00:00",
            "last_error": None,
            "reconnect_attempt": 0,
        }
    )
    monkeypatch.setattr(api, "get_stream_service", lambda: fake_service)

    with TestClient(api.create_app()) as client:
        response = client.get("/api/market/stream-status")

    assert response.status_code == 200
    assert response.json() == fake_service.payload
    assert "api_key" not in response.text.lower()
    assert fake_service.started is True
    assert fake_service.stopped is True


class FakeStreamService:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def status(self) -> dict[str, Any]:
        return self.payload
