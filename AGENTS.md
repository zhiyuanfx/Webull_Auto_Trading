# Webull Bridge Agent Guide

## Bootstrap every session

1. Read this file before editing.
2. Run `git status --short`; preserve unrelated user changes.
3. Read `docs/decisions.md`.
4. For Webull behavior, fetch the current official `developer.webull.com` page. Never
   guess endpoint fields, enums, SDK method names, hosts, or entitlement behavior.
5. Ordinary tests must not call Webull or place live orders. Live order transmission only
   happens through explicit user operation of the bridge with safety switches enabled.

## Architecture

- `src/webull_bridge/domain.py`: webhook schema, command enums, secret hashing, and ID helpers.
- `persistence.py`: SQLite routes, webhook events, orders, activity, and position snapshots.
- `webull.py`: live-only official SDK boundary for production Trading API calls.
- `executor.py`: validates queued events against route limits and executes Webull commands.
- `api.py`: FastAPI control plane plus TradingView webhook intake.
- `frontend/`: local operational UI.

TradingView/Pine Script owns strategy logic and market data. The bridge must not invent
entries, exits, symbols, prices, or sizes. It only authenticates, validates, deduplicates,
persists, and executes explicit instructions.

## Safety invariants

- `.env`, access tokens, account secrets, webhook secrets, and live payload secrets are never
  committed or logged.
- Webhook secrets are verified on intake and stored only as redacted placeholders.
- `client_order_id` is unique per account, stable, and at most 32 characters.
- Received, queued, processing, submitted, partial, filled, cancelled, failed, rejected,
  duplicate, validation_failed, and unknown are distinct states.
- Global execution and route enablement are safety gates, not simulation/UAT modes.
- There is no local simulator, no UAT mode, no strategy worker runtime, and no Webull market
  data stream in this project.
- CI and ordinary tests perform no external calls and no live trades.

## Commands

```bash
conda env create -f environment.yml
conda activate webull-strategy-desk
python -m pip install -e ".[dev,webull]"
webull-bridge init-db
webull-bridge diagnose
webull-bridge serve --reload
python -m pytest
ruff check .
cd frontend && npm ci && npm run dev
cd frontend && npm run build
uv lock  # update dependency metadata only; do not use uv to run project commands
```

Conda owns the runtime environment. Do not use `uv run` or `uv sync` for ordinary project
work; they may reconcile the environment before a command. Keep dependency declarations in
`pyproject.toml`, refresh `uv.lock` after dependency changes, and register the checkout as an
editable package in the active Conda environment with pip.

Update this guide when architecture, interfaces, commands, or safety rules change. Work is
done only when focused tests pass, the UI/API contract remains typed, errors preserve
actionable context without secrets, and documentation reflects behavior.
