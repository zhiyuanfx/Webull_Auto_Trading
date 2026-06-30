from decimal import Decimal

from webull_bridge.domain import RouteConfig
from webull_bridge.persistence import Ledger


def test_route_and_event_persist_across_ledger_instances(tmp_path) -> None:
    path = tmp_path / "bridge.db"
    ledger = Ledger(path)
    ledger.initialize()
    ledger.bootstrap(
        execution_enabled=False,
        route=RouteConfig(route_id="tv", account_id="acct"),
        secret="secret",
    )
    event, duplicate = ledger.create_event(
        "tv",
        "evt-1",
        "BUY",
        "1OZ",
        {
            "secret": "<redacted>",
            "event_id": "evt-1",
            "action": "BUY",
            "symbol": "1OZ",
            "quantity": "1",
        },
    )

    assert duplicate is False
    assert event["status"] == "queued"

    reloaded = Ledger(path)
    reloaded.initialize()

    assert reloaded.get_route("tv") is not None
    assert reloaded.queued_events()[0]["event_id"] == "evt-1"


def test_duplicate_event_is_detected(tmp_path) -> None:
    ledger = Ledger(tmp_path / "bridge.db")
    ledger.initialize()
    ledger.bootstrap(
        execution_enabled=False,
        route=RouteConfig(route_id="tv", account_id="acct"),
        secret="secret",
    )
    payload = {
        "secret": "<redacted>",
        "event_id": "evt-1",
        "action": "BUY",
        "symbol": "1OZ",
        "quantity": "1",
    }

    first, first_duplicate = ledger.create_event("tv", "evt-1", "BUY", "1OZ", payload)
    second, second_duplicate = ledger.create_event("tv", "evt-1", "BUY", "1OZ", payload)

    assert first_duplicate is False
    assert second_duplicate is True
    assert first["id"] == second["id"]


def test_route_limits_round_trip(tmp_path) -> None:
    ledger = Ledger(tmp_path / "bridge.db")
    ledger.initialize()
    ledger.upsert_route(
        RouteConfig(
            route_id="tv",
            account_id="acct",
            allowed_symbols=["1oz", "aapl"],
            max_quantity=Decimal("3"),
            max_notional=Decimal("10"),
        )
    )

    route = ledger.require_route("tv")

    assert route.allowed_symbols == ["1OZ", "AAPL"]
    assert route.max_quantity == Decimal("3")
