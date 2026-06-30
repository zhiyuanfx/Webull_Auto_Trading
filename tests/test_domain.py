from decimal import Decimal

import pytest
from pydantic import ValidationError

from webull_bridge.domain import BridgePayload, OrderType, make_client_order_id


def test_payload_defaults_and_sanitizes_secret() -> None:
    payload = BridgePayload.model_validate(
        {
            "secret": "shh",
            "event_id": "evt-1",
            "action": "buy",
            "symbol": "1oz",
            "quantity": "1",
        }
    )

    assert payload.symbol == "1OZ"
    assert payload.order_type == OrderType.MARKET
    assert payload.time_in_force == "DAY"
    assert payload.sanitized()["event_id"] == "evt-1"
    assert "secret" not in payload.sanitized()


def test_limit_order_requires_limit_price() -> None:
    with pytest.raises(ValidationError):
        BridgePayload.model_validate(
            {
                "secret": "shh",
                "event_id": "evt-1",
                "action": "BUY",
                "symbol": "1OZ",
                "quantity": "1",
                "order_type": "LIMIT",
            }
        )


def test_stop_limit_requires_both_prices() -> None:
    with pytest.raises(ValidationError):
        BridgePayload.model_validate(
            {
                "secret": "shh",
                "event_id": "evt-1",
                "action": "SELL",
                "symbol": "1OZ",
                "quantity": "1",
                "order_type": "STOP_LOSS_LIMIT",
                "limit_price": "1.23",
            }
        )


def test_cancel_requires_target_order_id() -> None:
    with pytest.raises(ValidationError):
        BridgePayload.model_validate({"secret": "shh", "event_id": "evt-1", "action": "CANCEL"})


def test_generated_client_order_id_is_stable_and_short() -> None:
    first = make_client_order_id("tv", "evt-1", "BUY")
    second = make_client_order_id("tv", "evt-1", "BUY")
    other = make_client_order_id("tv", "evt-1", "SELL")

    assert first == second
    assert first != other
    assert len(first) <= 32


def test_decimal_quantity_remains_exact() -> None:
    payload = BridgePayload.model_validate(
        {
            "secret": "shh",
            "event_id": "evt-1",
            "action": "BUY",
            "symbol": "1OZ",
            "quantity": "1.25",
        }
    )

    assert payload.quantity == Decimal("1.25")
