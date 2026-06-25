from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import uvicorn

from strategy_desk.config import get_settings
from strategy_desk.domain import MarketQuote
from strategy_desk.persistence import Ledger
from strategy_desk.plugins import PluginRegistry


def main() -> None:
    parser = argparse.ArgumentParser(prog="strategy-desk")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Initialize or migrate the local SQLite database")
    commands.add_parser("validate-strategies", help="Validate all discovered strategy plugins")
    replay_validate = commands.add_parser("replay-validate", help="Validate a quote JSONL file")
    replay_validate.add_argument("path", type=Path)
    replay_import = commands.add_parser("replay-import", help="Validate and import quote JSONL")
    replay_import.add_argument("path", type=Path)
    replay_import.add_argument("--name")
    commands.add_parser("diagnose", help="Print safe offline configuration diagnostics")
    serve = commands.add_parser("serve", help="Start the local API and built frontend")
    serve.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    settings = get_settings()

    if args.command == "init-db":
        Ledger(settings.strategy_desk_db_path).initialize()
        print(f"Initialized {settings.strategy_desk_db_path}")
    elif args.command == "validate-strategies":
        results = PluginRegistry(settings.strategy_root).validate_all()
        print(json.dumps(results, indent=2))
        if any(result["status"] != "ok" for result in results):
            raise SystemExit(1)
    elif args.command in {"replay-validate", "replay-import"}:
        source = args.path.expanduser().resolve()
        count = 0
        with source.open(encoding="utf-8") as replay:
            for line_number, line in enumerate(replay, 1):
                if not line.strip():
                    continue
                try:
                    MarketQuote.model_validate_json(line)
                except Exception as exc:
                    raise SystemExit(f"Invalid replay line {line_number}: {exc}") from exc
                count += 1
        if args.command == "replay-import":
            settings.recording_root.mkdir(parents=True, exist_ok=True)
            destination = settings.recording_root / (args.name or source.name)
            if destination.exists():
                raise SystemExit(f"Refusing to overwrite {destination}")
            shutil.copy2(source, destination)
            print(f"Imported {count} quotes to {destination}")
        else:
            print(f"Validated {count} quotes in {source}")
    elif args.command == "diagnose":
        ledger = Ledger(settings.strategy_desk_db_path)
        ledger.initialize()
        print(
            json.dumps(
                {
                    "database": str(settings.strategy_desk_db_path),
                    "database_exists": settings.strategy_desk_db_path.exists(),
                    "uat_configured": settings.uat_configured,
                    "production_configured": settings.production_configured,
                    "live_enabled": settings.webull_live_enabled,
                    "plugins": PluginRegistry(settings.strategy_root).validate_all(),
                },
                indent=2,
            )
        )
    elif args.command == "serve":
        uvicorn.run(
            "strategy_desk.api:app",
            host=settings.strategy_desk_host,
            port=settings.strategy_desk_port,
            reload=args.reload,
            log_level=settings.strategy_desk_log_level.lower(),
        )


if __name__ == "__main__":
    main()
