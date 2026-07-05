from datetime import UTC, datetime, timedelta

from webull_auto_trading.domain import QuoteState
from webull_auto_trading.market_data import (
    QuoteBook,
    build_subscription_payload,
    merge_quote_fields,
    parse_market_message,
    validate_quote,
)


def test_quote_partial_updates_merge_without_dropping_existing_fields() -> None:
    existing = {"code": "NASDAQ:AAPL", "bid": 100.0, "ask": 100.1, "delay_seconds": 0}

    merged = merge_quote_fields(existing, {"code": "NASDAQ:AAPL", "last_price": 100.05})

    assert merged["bid"] == 100.0
    assert merged["ask"] == 100.1
    assert merged["last_price"] == 100.05


def test_quote_book_tracks_partial_quote_state() -> None:
    book = QuoteBook()
    book.merge_quote_item({"code": "NASDAQ:AAPL", "bid": 100.0})
    quote = book.merge_quote_item({"code": "NASDAQ:AAPL", "ask": 100.1})

    assert quote.fields["bid"] == 100.0
    assert quote.fields["ask"] == 100.1


def test_quote_rejects_incomplete_delayed_and_stale() -> None:
    now = datetime(2026, 7, 5, 16, 0, tzinfo=UTC)
    incomplete = QuoteState("NASDAQ:AAPL", {"code": "NASDAQ:AAPL", "bid": 1.0})
    delayed = QuoteState(
        "NASDAQ:AAPL",
        {
            "code": "NASDAQ:AAPL",
            "bid": 100.0,
            "ask": 100.1,
            "last_price": 100.05,
            "lp_time": now.timestamp(),
            "delay_seconds": 900,
        },
    )
    stale = QuoteState(
        "NASDAQ:AAPL",
        {
            "code": "NASDAQ:AAPL",
            "bid": 100.0,
            "ask": 100.1,
            "last_price": 100.05,
            "lp_time": (now - timedelta(seconds=90)).timestamp(),
            "delay_seconds": 0,
        },
    )

    assert validate_quote(incomplete, now=now).ok is False
    assert validate_quote(delayed, now=now).reason == "quote is delayed by 900 seconds"
    assert validate_quote(stale, now=now, max_staleness_seconds=30).ok is False


def test_build_subscription_payload_is_complete_replacement_state() -> None:
    payload = build_subscription_payload(
        "key",
        ["NASDAQ:MSFT", "NASDAQ:AAPL"],
        include_minute_series=True,
    )

    assert payload["api_key"] == "key"
    assert len(payload["subscriptions"]) == 6
    assert {"code": "NASDAQ:AAPL", "type": "quote"} in payload["subscriptions"]


def test_parse_market_message_classifies_heartbeat_quote_and_fatal_error() -> None:
    assert parse_market_message("pong").kind == "pong"
    assert parse_market_message('{"server_time":1741397070281}').kind == "heartbeat"
    assert parse_market_message('{"data":[{"code":"NASDAQ:AAPL"}]}').kind == "quote"
    error = parse_market_message('{"error":"server_busy","message":"retry"}')

    assert error.kind == "error"
    assert error.fatal is True
