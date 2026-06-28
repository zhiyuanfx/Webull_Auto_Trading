from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn

from strategy_desk.config import get_settings
from strategy_desk.domain import MarketQuote
from strategy_desk.persistence import Ledger
from strategy_desk.plugins import PluginRegistry

RETENTION_RE = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>[dh])$")


def parse_retention_window(value: str) -> timedelta:
    match = RETENTION_RE.fullmatch(value.strip().lower())
    if not match:
        raise argparse.ArgumentTypeError("Use a retention window like 12h, 30d, or 90d")
    count = int(match.group("count"))
    unit = match.group("unit")
    if unit == "h":
        return timedelta(hours=count)
    return timedelta(days=count)


def print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(prog="strategy-desk")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Initialize or migrate the local SQLite database")
    commands.add_parser("db-stats", help="Print safe SQLite database statistics as JSON")
    prune = commands.add_parser("db-prune", help="Preview or execute safe database pruning")
    prune.add_argument("--older-than", type=parse_retention_window, default=timedelta(days=90))
    prune_mode = prune.add_mutually_exclusive_group()
    prune_mode.add_argument(
        "--execute",
        action="store_true",
        help="Delete eligible rows; default is a dry run",
    )
    prune_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview eligible rows without deleting them; this is the default",
    )
    prune.add_argument(
        "--archive",
        type=Path,
        help="Archive eligible rows to a new SQLite file before deletion when used with --execute",
    )
    commands.add_parser("db-vacuum", help="Run an explicit WAL checkpoint/truncate and VACUUM")
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
    elif args.command == "db-stats":
        print_json(Ledger(settings.strategy_desk_db_path).database_stats())
    elif args.command == "db-prune":
        cutoff = datetime.now(UTC) - args.older_than
        print_json(
            Ledger(settings.strategy_desk_db_path).prune_database(
                cutoff,
                execute=args.execute,
                archive_path=args.archive,
            )
        )
    elif args.command == "db-vacuum":
        print_json(Ledger(settings.strategy_desk_db_path).vacuum())
    elif args.command == "validate-strategies":
        results = PluginRegistry(settings.strategy_root).validate_all()
        print_json(results)
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
        print_json(
            {
                "database": str(settings.strategy_desk_db_path),
                "database_exists": settings.strategy_desk_db_path.exists(),
                "uat_configured": settings.uat_configured,
                "production_configured": settings.production_configured,
                "live_enabled": settings.webull_live_enabled,
                "plugins": PluginRegistry(settings.strategy_root).validate_all(),
            }
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
