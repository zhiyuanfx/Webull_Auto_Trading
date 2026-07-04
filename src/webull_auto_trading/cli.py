from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from webull_auto_trading.config import Settings, get_settings
from webull_auto_trading.webull import WebullError, WebullTradingClient


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


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "<redacted>"
                if any(part in _key_slug(key) for part in _SENSITIVE_KEY_PARTS)
                else redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
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
        "copy_this_to_webull_account_id": (
            unique_candidates[0] if len(unique_candidates) == 1 else None
        ),
    }


async def fetch_accounts(*, raw: bool = False) -> dict[str, Any]:
    settings = get_settings()
    accounts = await WebullTradingClient(settings).list_accounts()
    summary = summarize_accounts(accounts)
    if raw:
        summary["raw_response"] = redact(accounts)
    return summary


def diagnose_payload(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    live_token_dir = settings.webull_token_dir / "live"
    return {
        "webull_region": settings.webull_region,
        "webull_configured": settings.production_configured,
        "webull_account_id_configured": bool(settings.webull_account_id),
        "token_dir": str(live_token_dir),
        "token_dir_exists": live_token_dir.exists(),
        "token_wait_seconds": settings.webull_prod_token_wait_seconds,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="webull-auto-trading")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("diagnose", help="Print safe offline Webull configuration diagnostics")
    accounts = commands.add_parser(
        "accounts", help="List Webull account id candidates from the authenticated API session"
    )
    accounts.add_argument(
        "--raw",
        action="store_true",
        help="Also print the redacted raw Webull account-list response",
    )
    args = parser.parse_args()

    if args.command == "diagnose":
        print_json(diagnose_payload())
    elif args.command == "accounts":
        try:
            print_json(asyncio.run(fetch_accounts(raw=args.raw)))
        except WebullError as exc:
            raise SystemExit(f"Webull account lookup failed: {exc.message}") from exc


if __name__ == "__main__":
    main()
