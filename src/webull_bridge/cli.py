from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import uvicorn

from webull_bridge.config import get_settings
from webull_bridge.domain import RouteConfig
from webull_bridge.persistence import Ledger
from webull_bridge.webull import WebullError, WebullTradingClient


def print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


_ACCOUNT_ID_KEYS = {"accountid", "account_id"}
_SUMMARY_KEYS = {
    "account_id",
    "accountid",
    "account_type",
    "accounttype",
    "broker_id",
    "brokerid",
    "currency",
    "name",
    "nickname",
    "region",
    "status",
}
_SENSITIVE_KEY_PARTS = ("secret", "token", "password", "authorization", "appkey", "appsecret")


def _key_slug(key: str) -> str:
    return key.replace("_", "").replace("-", "").lower()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "<redacted>"
                if any(part in _key_slug(key) for part in _SENSITIVE_KEY_PARTS)
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for item in value.values():
            found.extend(_walk_dicts(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_dicts(item))
    return found


def summarize_accounts(payload: Any) -> dict[str, Any]:
    candidates: list[str] = []
    summaries_by_id: dict[str, dict[str, Any]] = {}

    for item in _walk_dicts(payload):
        summary: dict[str, Any] = {}
        for key, value in item.items():
            slug = _key_slug(key)
            if slug in _ACCOUNT_ID_KEYS:
                candidates.append(str(value))
                summary["account_id"] = str(value)
            elif slug in _SUMMARY_KEYS and not isinstance(value, dict | list):
                summary[key] = value
        if "account_id" in summary:
            account_id = summary["account_id"]
            existing = summaries_by_id.get(account_id, {})
            if len(summary) >= len(existing):
                summaries_by_id[account_id] = summary

    unique_candidates = sorted({candidate for candidate in candidates if candidate})
    summaries = [summaries_by_id[account_id] for account_id in unique_candidates]
    return {
        "account_id_candidates": unique_candidates,
        "accounts": summaries,
        "account_count": len(summaries),
        "copy_this_to_route_account_id": (
            unique_candidates[0] if len(unique_candidates) == 1 else None
        ),
    }


async def fetch_accounts(*, raw: bool = False) -> dict[str, Any]:
    settings = get_settings()
    accounts = await WebullTradingClient(settings).list_accounts()
    summary = summarize_accounts(accounts)
    if raw:
        summary["raw_response"] = _redact(accounts)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(prog="webull-bridge")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Initialize or migrate the local SQLite database")
    commands.add_parser("db-stats", help="Print safe SQLite database statistics as JSON")
    commands.add_parser("db-vacuum", help="Run WAL checkpoint/truncate and VACUUM")
    commands.add_parser("diagnose", help="Print safe offline configuration diagnostics")
    accounts = commands.add_parser(
        "accounts", help="List Webull account id candidates from the authenticated API session"
    )
    accounts.add_argument(
        "--raw",
        action="store_true",
        help="Also print the redacted raw Webull account-list response",
    )
    route = commands.add_parser(
        "ensure-route", help="Create or update a route without printing secrets"
    )
    route.add_argument("route_id")
    route.add_argument("--name", default="TradingView")
    route.add_argument("--account-id", default="")
    route.add_argument("--secret", default="")
    serve = commands.add_parser("serve", help="Start the local API and built frontend")
    serve.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    ledger = Ledger(settings.bridge_db_path)
    ledger.initialize()

    if args.command == "init-db":
        ledger.bootstrap(
            execution_enabled=settings.bridge_execution_enabled,
            route=RouteConfig(
                route_id=settings.bridge_default_route_id,
                name=settings.bridge_default_route_name,
                account_id=settings.webull_account_id,
            ),
            secret=settings.bridge_default_secret,
        )
        print(f"Initialized {settings.bridge_db_path}")
    elif args.command == "db-stats":
        print_json(ledger.database_stats())
    elif args.command == "db-vacuum":
        print_json(ledger.vacuum())
    elif args.command == "diagnose":
        print_json(
            {
                "database": str(settings.bridge_db_path),
                "database_exists": settings.bridge_db_path.exists(),
                "webull_configured": settings.production_configured,
                "execution_enabled": ledger.get_setting("execution_enabled", False),
                "routes": [
                    {
                        **route.model_dump(mode="json", exclude={"secret_hash"}),
                        "secret_configured": bool(route.secret_hash),
                    }
                    for route in ledger.list_routes()
                ],
            }
        )
    elif args.command == "accounts":
        try:
            print_json(asyncio.run(fetch_accounts(raw=args.raw)))
        except WebullError as exc:
            raise SystemExit(f"Webull account lookup failed: {exc.message}") from exc
    elif args.command == "ensure-route":
        existing = ledger.get_route(args.route_id)
        route_config = existing or RouteConfig(route_id=args.route_id)
        route_config.name = args.name
        route_config.account_id = args.account_id
        if args.secret:
            from webull_bridge.domain import hash_secret

            route_config.secret_hash = hash_secret(args.secret)
        saved = ledger.upsert_route(route_config)
        print_json(
            {
                **saved.model_dump(mode="json", exclude={"secret_hash"}),
                "secret_configured": bool(saved.secret_hash),
            }
        )
    elif args.command == "serve":
        uvicorn.run(
            "webull_bridge.api:app",
            host=settings.bridge_host,
            port=settings.bridge_port,
            reload=args.reload,
            log_level=settings.bridge_log_level.lower(),
        )


if __name__ == "__main__":
    main()
