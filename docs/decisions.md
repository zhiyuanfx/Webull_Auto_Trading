# Architecture Decisions

## Legacy bridge removed

The old external-alert bridge has been removed from the project. The repo is being reset for
Python-native strategies that consume InsightSentry streaming data and use Webull only for
read-only live account operations in the current runtime.

## Minimal Webull foundation

The Webull Trading API boundary remains because account lookup, positions, open orders, and
order API access are useful for the next runtime. The wrapper uses the official Python SDK
and production token cache. Endpoint fields, SDK method names, hosts, and entitlement
behavior must be verified against current official Webull documentation before any Webull
behavior changes.

## Test/Live multi-strategy runtime

The runtime is a local Python service with SQLite persistence, a shared InsightSentry
market-data boundary, strategy instances isolated by `strategy_instance_id`, paper virtual
orders, and a FastAPI/React operator UI. Runtime mode is global:

- `test`: paper trading with real InsightSentry market data.
- `live`: Webull account reads and live strategy configuration only; execution is disabled.

Strategy instances are configured from `config/strategies.test.yml` or
`config/strategies.live.yml` based on the active mode. Strategy additions and parameter
changes are made in source/config, not through the UI. The local API supports runtime
pause/resume controls for configured instances.

Operator pause state is persistent. Config `enabled` seeds a new strategy ID, while the
SQLite strategy setting remains authoritative for an existing ID. Global and per-strategy
flatten controls are paper-only: they pause execution, cancel virtual pending orders, and
close paper positions only when an in-memory current quote provides the documented paper
close side. Live mode rejects flatten because live execution is disabled.

The first ported strategy is `day_many_bian`, modeled as a Python state machine with daily
brackets, reverse-buffer entries after the first cycle, trailing stop updates, optional
pyramiding, cooldown, max-cycle, and daily-loss controls.

## Secret and credential handling

Personal Webull credentials live in gitignored `.env` and SDK tokens live under ignored
`.runtime/webull_tokens/live`. Diagnostics and raw account lookup output must redact
sensitive fields and must not print app keys, app secrets, access tokens, passwords, or
authorization material.

## Conda runtime ownership

The existing `webull-strategy-desk` Conda environment remains the owned runtime because it
has already verified the Webull SDK setup. The project is installed editable with
`python -m pip install -e ".[dev,webull]"`; normal commands do not use `uv run` or `uv sync`.

## Offline ordinary tests

Ordinary tests cover configuration, account response summarization, redaction, Webull error
sanitization, quote merging/rejection, daily-bar bootstrap, paper order fills, strategy
bracket logic, per-instance isolation, runtime mode switching, paper account persistence,
and storage cleanup. They must not call Webull, InsightSentry, or submit live orders.

## No live order placement

Live mode is execution-read-only. The UI and API expose no live place, replace, cancel, or
flatten commands. Any future live execution phase must re-check current official Webull docs
and add explicit safety gates.

## Volatile market display

Market quotes and series are not persisted. Runtime code keeps only current quote state in
memory for strategy decisions and a capped in-memory stream buffer for operator visibility.
Cleanup may clear legacy `quote_snapshots` and `bars` rows.
