"""One-off InsightSentry WebSocket sanity capture.

This script is intentionally separate from the package under src/. It verifies
that market-data streaming works and writes captured rows to ignored data/.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv, set_key
from websockets import connect
from websockets.exceptions import ConnectionClosed

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
ENDPOINT = "wss://realtime.insightsentry.com/live"
RAPIDAPI_HOST = "insightsentry.p.rapidapi.com"
SYMBOL = "BINANCE:BTCUSDT"
SERIES_FIELDS = [
    "received_at_utc",
    "code",
    "bar_type",
    "bar_end",
    "last_update",
    "time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "raw_json",
]
QUOTE_FIELDS = [
    "received_at_utc",
    "last_update",
    "total_items",
    "code",
    "status",
    "lp_time",
    "volume",
    "last_price",
    "change_percent",
    "change",
    "ask",
    "bid",
    "ask_size",
    "bid_size",
    "prev_close_price",
    "open_price",
    "low_price",
    "high_price",
    "market_cap",
    "currency_code",
    "delay_seconds",
    "raw_json",
]
FATAL_ERRORS = {
    "max_connections_exceeded",
    "connection_evicted",
    "server_busy",
    "internal_server_error",
}
QUOTE_ITEM_KEYS = {
    "ask",
    "ask_size",
    "bid",
    "bid_size",
    "change",
    "change_percent",
    "currency_code",
    "delay_seconds",
    "high_price",
    "last_price",
    "low_price",
    "lp_time",
    "market_cap",
    "open_price",
    "prev_close_price",
    "status",
    "volume",
}


class MissingCredentials(RuntimeError):
    """Raised when no InsightSentry WebSocket credential path is configured."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def clean_env(name: str) -> str:
    return os.environ.get(name, "").strip()


def parse_expiration(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None

    try:
        timestamp = float(value)
    except ValueError:
        normalized = value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    if timestamp > 10_000_000_000:
        timestamp = timestamp / 1000
    return datetime.fromtimestamp(timestamp, tz=UTC)


def expiration_is_usable(value: str) -> bool:
    expiration = parse_expiration(value)
    if expiration is None:
        return True
    return expiration > utc_now() + timedelta(minutes=15)


def rapidapi_websocket_key_url(host: str) -> str:
    if host.startswith(("http://", "https://")):
        return f"{host.rstrip('/')}/v2/websocket-key"
    return f"https://{host}/v2/websocket-key"


def fetch_rapidapi_websocket_key(rapidapi_key: str, host: str) -> tuple[str, str]:
    url = rapidapi_websocket_key_url(host)
    request = urllib.request.Request(
        url,
        headers={
            "x-rapidapi-key": rapidapi_key,
            "x-rapidapi-host": host.removeprefix("https://").removeprefix("http://"),
            "accept": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"RapidAPI WebSocket key request failed with HTTP {exc.code}."
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"RapidAPI WebSocket key request failed: {exc.reason}") from exc

    websocket_key = str(payload.get("api_key") or "").strip()
    expiration = str(payload.get("expiration") or "").strip()
    if not websocket_key:
        keys = ", ".join(sorted(payload.keys()))
        raise RuntimeError(
            "RapidAPI WebSocket key response did not include api_key"
            f" (response keys: {keys or 'none'})."
        )
    return websocket_key, expiration


def store_websocket_key(websocket_key: str, expiration: str) -> None:
    if not ENV_PATH.exists():
        return
    set_key(ENV_PATH, "INSIGHTSENTRY_WEBSOCKET_KEY", websocket_key, quote_mode="never")
    if expiration:
        set_key(
            ENV_PATH,
            "INSIGHTSENTRY_WEBSOCKET_KEY_EXPIRATION",
            expiration,
            quote_mode="never",
        )


def websocket_auth_key() -> str:
    load_dotenv(ENV_PATH)

    direct_key = clean_env("INSIGHTSENTRY_API_KEY")
    if direct_key:
        return direct_key

    cached_key = clean_env("INSIGHTSENTRY_WEBSOCKET_KEY")
    cached_expiration = clean_env("INSIGHTSENTRY_WEBSOCKET_KEY_EXPIRATION")
    if cached_key and expiration_is_usable(cached_expiration):
        return cached_key

    rapidapi_key = clean_env("INSIGHTSENTRY_RAPIDAPI_KEY")
    if rapidapi_key:
        host = clean_env("INSIGHTSENTRY_RAPIDAPI_HOST") or RAPIDAPI_HOST
        websocket_key, expiration = fetch_rapidapi_websocket_key(rapidapi_key, host)
        store_websocket_key(websocket_key, expiration)
        return websocket_key

    raise MissingCredentials(
        "No usable InsightSentry WebSocket credential is configured. Set "
        "INSIGHTSENTRY_API_KEY for direct paid WebSocket access, or set "
        "INSIGHTSENTRY_RAPIDAPI_KEY with INSIGHTSENTRY_RAPIDAPI_HOST to fetch a "
        "RapidAPI WebSocket key. You can also paste INSIGHTSENTRY_WEBSOCKET_KEY "
        "and optional INSIGHTSENTRY_WEBSOCKET_KEY_EXPIRATION."
    )


def subscription_payload(
    api_key: str,
    symbol: str,
    subscription_type: str,
) -> dict[str, Any]:
    subscription: dict[str, Any] = {
        "code": symbol,
        "type": subscription_type,
    }
    if subscription_type == "series":
        subscription.update(
            {
                "bar_type": "tick",
                "bar_interval": 1,
                "max_dp": 1,
            }
        )
    return {
        "api_key": api_key,
        "subscriptions": [subscription],
    }


def csv_path(output_dir: Path, symbol: str, subscription_type: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_symbol = symbol.lower().replace(":", "").replace("/", "")
    timestamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    label = "quotes" if subscription_type == "quote" else "ticks"
    return output_dir / f"insightsentry_{safe_symbol}_{label}_{timestamp}.csv"


def compact_raw(raw_json: str) -> str:
    if len(raw_json) <= 240:
        return raw_json
    return f"{raw_json[:237]}..."


def row_from_series_item(
    received_at: str,
    message: dict[str, Any],
    item: dict[str, Any],
    raw_json: str,
) -> dict[str, Any]:
    return {
        "received_at_utc": received_at,
        "code": message.get("code", ""),
        "bar_type": message.get("bar_type", ""),
        "bar_end": message.get("bar_end", ""),
        "last_update": message.get("last_update", ""),
        "time": item.get("time", ""),
        "open": item.get("open", ""),
        "high": item.get("high", ""),
        "low": item.get("low", ""),
        "close": item.get("close", ""),
        "volume": item.get("volume", ""),
        "raw_json": raw_json,
    }


def row_from_quote_item(
    received_at: str,
    message: dict[str, Any],
    item: dict[str, Any],
    raw_json: str,
) -> dict[str, Any]:
    return {
        "received_at_utc": received_at,
        "last_update": message.get("last_update", ""),
        "total_items": message.get("total_items", ""),
        "code": item.get("code", ""),
        "status": item.get("status", ""),
        "lp_time": item.get("lp_time", ""),
        "volume": item.get("volume", ""),
        "last_price": item.get("last_price", ""),
        "change_percent": item.get("change_percent", ""),
        "change": item.get("change", ""),
        "ask": item.get("ask", ""),
        "bid": item.get("bid", ""),
        "ask_size": item.get("ask_size", ""),
        "bid_size": item.get("bid_size", ""),
        "prev_close_price": item.get("prev_close_price", ""),
        "open_price": item.get("open_price", ""),
        "low_price": item.get("low_price", ""),
        "high_price": item.get("high_price", ""),
        "market_cap": item.get("market_cap", ""),
        "currency_code": item.get("currency_code", ""),
        "delay_seconds": item.get("delay_seconds", ""),
        "raw_json": raw_json,
    }


async def capture_ticks(
    *,
    api_key: str,
    output: Path,
    symbol: str,
    subscription_type: str,
    duration_seconds: float,
) -> dict[str, Any]:
    rows = 0
    preview_rows: list[dict[str, Any]] = []
    unexpected_json_samples: list[dict[str, Any]] = []
    non_data_messages: Counter[str] = Counter()
    errors: list[dict[str, Any]] = []

    with output.open("w", newline="", encoding="utf-8") as csv_file:
        fields = QUOTE_FIELDS if subscription_type == "quote" else SERIES_FIELDS
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()

        async with connect(
            ENDPOINT,
            open_timeout=12,
            close_timeout=5,
            ping_interval=None,
        ) as websocket:
            try:
                greeting = await asyncio.wait_for(websocket.recv(), timeout=12)
            except TimeoutError:
                non_data_messages["greeting_timeout"] += 1
            else:
                try:
                    greeting_message = json.loads(greeting)
                except json.JSONDecodeError:
                    non_data_messages["non_json_greeting"] += 1
                else:
                    if (
                        greeting_message.get("message") == "Connecting..."
                        and "error" not in greeting_message
                    ):
                        non_data_messages["Connecting..."] += 1
                    elif "error" in greeting_message:
                        errors.append(
                            {
                                key: greeting_message.get(key)
                                for key in ("error", "message", "symbol", "details")
                                if key in greeting_message
                            }
                        )
                    else:
                        non_data_messages["unexpected_greeting"] += 1

            await websocket.send(
                json.dumps(subscription_payload(api_key, symbol, subscription_type))
            )
            deadline = time.monotonic() + duration_seconds

            while time.monotonic() < deadline:
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    raw_message = await asyncio.wait_for(websocket.recv(), timeout=remaining)
                except TimeoutError:
                    break
                except ConnectionClosed as exc:
                    non_data_messages[f"closed:{exc.code}"] += 1
                    break

                received_at = utc_now().isoformat()

                if raw_message == "pong":
                    non_data_messages["pong"] += 1
                    continue

                try:
                    message = json.loads(raw_message)
                except json.JSONDecodeError:
                    non_data_messages["non_json"] += 1
                    continue

                if "server_time" in message and len(message) == 1:
                    non_data_messages["heartbeat"] += 1
                    continue

                if "error" in message:
                    safe_error = {
                        key: message.get(key)
                        for key in ("error", "message", "symbol", "details")
                        if key in message
                    }
                    errors.append(safe_error)
                    if message.get("error") in FATAL_ERRORS:
                        break
                    continue

                if "message" in message and "series" not in message and "data" not in message:
                    text = str(message.get("message") or "message")
                    if text == "Connecting...":
                        non_data_messages[text] += 1
                    else:
                        errors.append({"message": text})
                    continue

                if subscription_type == "quote":
                    data = message.get("data")
                    if isinstance(data, list):
                        quote_items = data
                    elif "code" in message and QUOTE_ITEM_KEYS.intersection(message):
                        quote_items = [message]
                    else:
                        non_data_messages["json_without_quote_data"] += 1
                        if len(unexpected_json_samples) < 5:
                            unexpected_json_samples.append(
                                {
                                    "keys": sorted(message.keys()),
                                    "raw_json": compact_raw(
                                        json.dumps(
                                            message,
                                            sort_keys=True,
                                            separators=(",", ":"),
                                        )
                                    ),
                                }
                            )
                        continue

                    raw_json = json.dumps(message, sort_keys=True, separators=(",", ":"))
                    for item in quote_items:
                        if not isinstance(item, dict):
                            non_data_messages["non_object_quote_item"] += 1
                            continue
                        row = row_from_quote_item(received_at, message, item, raw_json)
                        writer.writerow(row)
                        rows += 1
                        if len(preview_rows) < 5:
                            preview = row.copy()
                            preview["raw_json"] = compact_raw(str(preview["raw_json"]))
                            preview_rows.append(preview)
                    continue

                series = message.get("series")
                if not isinstance(series, list):
                    non_data_messages["json_without_series"] += 1
                    if len(unexpected_json_samples) < 5:
                        unexpected_json_samples.append(
                            {
                                "keys": sorted(message.keys()),
                                "raw_json": compact_raw(
                                    json.dumps(
                                        message,
                                        sort_keys=True,
                                        separators=(",", ":"),
                                    )
                                ),
                            }
                        )
                    continue

                raw_json = json.dumps(message, sort_keys=True, separators=(",", ":"))
                for item in series:
                    if not isinstance(item, dict):
                        non_data_messages["non_object_series_item"] += 1
                        continue
                    row = row_from_series_item(received_at, message, item, raw_json)
                    writer.writerow(row)
                    rows += 1
                    if len(preview_rows) < 5:
                        preview = row.copy()
                        preview["raw_json"] = compact_raw(str(preview["raw_json"]))
                        preview_rows.append(preview)

            await websocket.close()

    return {
        "csv_path": str(output),
        "row_count": rows,
        "preview_rows": preview_rows,
        "unexpected_json_samples": unexpected_json_samples,
        "non_data_messages": dict(non_data_messages),
        "errors": errors,
        "streaming_appears_to_work": rows > 0 and not errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture a short InsightSentry WebSocket stream to CSV."
    )
    parser.add_argument("--symbol", default=SYMBOL)
    parser.add_argument(
        "--subscription-type",
        choices=("series", "quote"),
        default="series",
    )
    parser.add_argument("--duration-seconds", type=float, default=5.0)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    try:
        api_key = websocket_auth_key()
    except MissingCredentials as exc:
        print(str(exc), file=sys.stderr)
        return 2

    output = csv_path(args.output_dir, args.symbol, args.subscription_type)
    summary = await capture_ticks(
        api_key=api_key,
        output=output,
        symbol=args.symbol,
        subscription_type=args.subscription_type,
        duration_seconds=args.duration_seconds,
    )
    print(json.dumps(summary, indent=2))
    return 0 if not summary["errors"] else 1


def main() -> int:
    try:
        return asyncio.run(async_main())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
