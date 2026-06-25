# Webull Strategy Desk Agent Guide

## Bootstrap every session

1. Read this file and the nearest nested `AGENTS.md` before editing.
2. Run `git status --short`; preserve unrelated user changes.
3. Read the relevant architecture decision in `docs/decisions.md`.
4. For Webull behavior, fetch the current official `developer.webull.com` page. Never
   guess endpoint fields, enums, SDK method names, hosts, or entitlement behavior.
5. Default all execution and tests to `LOCAL_SIM`. Never transmit a production order.

## Architecture

- `src/strategy_desk/domain.py`: broker-neutral immutable domain values.
- `strategy.py`, `order_session.py`, `schedule.py`, and `supervisor.py`: strategy contract,
  credential-free order sessions, schedules, process isolation, and IPC.
- `gateway.py`: execution gateway and simulator. Only gateways may hold broker clients.
- `persistence.py`: SQLite audit ledger and restored strategy-local order state.
- `futures.py`: fixed/auto-roll resolution and independent expiry guard.
- `webull.py`: lazy official-SDK boundary for account, order, and event APIs.
- `api.py`: localhost FastAPI/WS control plane; `frontend/`: operational UI.
- `strategies/`: source-first plugins; its nested guide is mandatory for plugin work.

Strategies decide when and how to trade and manage their own tickets through an injected
`OrderSession`. The gateway owns credentials, transport, validation, idempotency, rate
limits, and reconciliation. It must never invent a strategy entry, exit, price, or size.

## Safety invariants

- `.env`, access tokens, account secrets, and live payloads are never committed or logged.
- Strategy workers do not inherit Webull credential variables or receive gateway/broker clients.
- Official-SDK tokens are cached only under ignored `.runtime/webull_tokens/<mode>` directories.
- Worker startup is acknowledged before an instance becomes RUNNING; orphaned or crashed workers
  become DEGRADED and their active run is closed.
- A `client_order_id` is unique per account, stable, and at most 32 characters.
- Submitted, partial, filled, cancelled, failed, rejected, and unknown are distinct states.
- Roll, expiry, and emergency orders are visibly tagged `SYSTEM_ROLL`, `SYSTEM_EXPIRY`,
  or `SYSTEM_EMERGENCY`.
- Virtual strategy positions plus `UNASSIGNED` activity must reconcile to the broker net.
- CI and ordinary tests perform no external calls. UAT is explicit opt-in; live tests do not exist.

## Commands

```bash
conda env create -f environment.yml
conda activate webull-strategy-desk
python -m pip install -e ".[dev,webull]"
strategy-desk init-db
strategy-desk validate-strategies
strategy-desk replay-validate recordings/example.jsonl
strategy-desk diagnose
strategy-desk serve --reload
python -m pytest
ruff check .
cd frontend && npm ci && npm run dev
cd frontend && npm run build
uv lock  # update dependency metadata only; do not use uv to run project commands
```

Conda owns the runtime environment. Do not use `uv run` or `uv sync` for ordinary project
work; they may reconcile the environment before a command. Keep dependency declarations in
`pyproject.toml`, refresh `uv.lock` after dependency changes, and register the checkout as an
editable package in the active Conda environment with pip. This is a pointer to the source
tree, not a copied project.

Update this guide when architecture, interfaces, commands, or safety rules change. Work is
done only when focused tests pass, the UI/API contract remains typed, errors preserve
actionable context without secrets, and documentation reflects behavior.

Architecture rationale lives in [`docs/decisions.md`](docs/decisions.md). When changing an
invariant, update both that record and this guide in the same change.
