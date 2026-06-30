from __future__ import annotations

import argparse
import json

import uvicorn

from webull_bridge.config import get_settings
from webull_bridge.domain import RouteConfig
from webull_bridge.persistence import Ledger


def print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(prog="webull-bridge")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Initialize or migrate the local SQLite database")
    commands.add_parser("db-stats", help="Print safe SQLite database statistics as JSON")
    commands.add_parser("db-vacuum", help="Run WAL checkpoint/truncate and VACUUM")
    commands.add_parser("diagnose", help="Print safe offline configuration diagnostics")
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
