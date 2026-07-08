# Webull Auto Trading Agent Guide

## Bootstrap every session

1. Read this file before editing.
2. Run `git status --short`; preserve unrelated user changes.
3. Read `docs/decisions.md`.
4. For Webull behavior, fetch the current official `developer.webull.com` page. Never
   guess endpoint fields, enums, SDK method names, hosts, or entitlement behavior.
5. Ordinary tests must not call Webull, InsightSentry, or place live orders. Live order
   transmission only happens through explicit user operation of future trading runtime code
   with safety switches enabled.

## Architecture

- `src/webull_auto_trading/config.py`: environment-backed settings for Webull credentials,
  token cache location, and default account id.
- `src/webull_auto_trading/webull.py`: live-only official SDK boundary for Trading API calls.
- `src/webull_auto_trading/cli.py`: safe diagnostics, account id discovery helpers, and local
  runtime commands.
- `src/webull_auto_trading/runtime.py`: Test/Live local runtime coordinator.
- `src/webull_auto_trading/live_execution.py`: live order intent model, Webull market-order request
  construction, and safety-gated adapter scaffolding.
- `src/webull_auto_trading/market_data.py`: InsightSentry quote merge, subscription, and
  rejection rules.
- `src/webull_auto_trading/insightsentry_stream.py`: production InsightSentry quote
  WebSocket service and safe stream status.
- `src/webull_auto_trading/strategy/recycle_buy.py`: simple Test-mode paper recycle-buy EA.
- `src/webull_auto_trading/strategy/`: tracks only the base interface, package init, and
  disclosed demo strategies; real strategy modules are local/ignored.
- `src/webull_auto_trading/order_manager.py`: virtual pending orders and paper fills.
- `src/webull_auto_trading/api.py`: local FastAPI API for the operator UI.
- `frontend/`: local React/Vite operator console.

The runtime uses InsightSentry streaming data plus Python strategy code and Webull Trading
API reads. Test mode owns paper trading and persistence. Live mode can transmit gated Webull
market orders from strategy decisions when runtime mode, env master, per-strategy config,
quote, in-flight, account alias, and reconciliation gates pass. The old external-alert
bridge has been removed. Do not add new live mutation paths without an explicit safety plan
and user request.

## Safety invariants

- `.env`, access tokens, account secrets, API keys, and live payload secrets are never
  committed, logged, or echoed.
- SDK token caches under `.runtime/` and local data under `data/` are ignored and must not be
  deleted or migrated without explicit confirmation.
- The Webull SDK wrapper is live-only. Ordinary tests must use mocks and must not call Webull.
- Runtime supports global `test` and `live` modes. Live order transmission is allowed only
  through the gated market-order path after global mode, master, per-strategy, quote,
  in-flight, account alias, and reconciliation gates pass.
- Strategy instances do not expose public per-strategy execution modes.
- Market quote/series display is process-memory only; do not persist new quote or bar data.
- There is no external-alert intake, local simulator, alternate trading environment, or live
  order worker in this baseline.
- Real strategy modules and real strategy config files are local/ignored. Keep examples
  tracked, then copy them to ignored local files before operating the runtime.
- Keep trading logic separate from market-data logic when future runtime code is added.

## Commands

```bash
conda env create -f environment.yml
conda activate webull-strategy-desk
python -m pip install -e ".[dev,webull]"
webull-auto-trading diagnose
webull-auto-trading diagnose-live
webull-auto-trading accounts
webull-auto-trading init-db
webull-auto-trading cleanup --dry-run
webull-auto-trading serve --reload  # starts API plus one InsightSentry quote stream worker
webull-auto-trading run
python -m pytest
ruff check .
uv lock  # update dependency metadata only; do not use uv to run project commands
cd frontend && npm run build
```

Conda owns the runtime environment. Do not use `uv run` or `uv sync` for ordinary project
work; they may reconcile the environment before a command. Keep dependency declarations in
`pyproject.toml`, refresh `uv.lock` after dependency changes, and register the checkout as an
editable package in the active Conda environment with pip.

Update this guide when architecture, interfaces, commands, or safety rules change. Work is
done only when focused tests pass, errors preserve actionable context without secrets, and
documentation reflects behavior.
