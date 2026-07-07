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
quote stream boundary, strategy instances isolated by `strategy_instance_id`, paper virtual
orders, and a FastAPI/React operator UI. Runtime mode is global:

- `test`: paper trading with real InsightSentry market data.
- `live`: Webull account reads and live strategy configuration only; execution is disabled.

Strategy instances are configured from ignored local files, `config/strategies.test.yml` or
`config/strategies.live.yml`, based on the active mode. Tracked `.example.yml` files show
safe demo/skeleton configuration only. Strategy additions and parameter changes are made in
local source/config, not through the UI. The local API supports runtime pause/resume controls
for configured instances.

`webull-auto-trading serve` owns one background InsightSentry quote WebSocket consumer. It
subscribes only to deduplicated enabled strategy symbols, keeps quote and raw stream display
state in process memory, and surfaces missing credentials, idle symbols, reconnecting, and
stream errors through a safe local status endpoint. Global pause does not stop market data
visibility.

Operator pause state is persistent. Config `enabled` seeds a new strategy ID, while the
SQLite strategy setting remains authoritative for an existing ID. Global and per-strategy
flatten controls are paper-only: they pause execution, cancel virtual pending orders, and
close paper positions only when an in-memory current quote provides the documented paper
close side. Live mode rejects flatten because live execution is disabled.

The tracked public strategy package includes `recycle_buy`, a deliberately small paper-only
helper EA that opens an immediate BUY on each valid quote, attaches fixed stop-loss/take-
profit distances, and waits for a configured cooldown after the position closes before
opening again.

Real strategy modules live next to the tracked demo strategy but are ignored by git. The
runtime resolves private strategies by importing `webull_auto_trading.strategy.<name>` and
looking for a PascalCase `<Name>Strategy` class.

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

## Live execution scaffolding, no runtime transmission

Live mode now stores explicit live execution metadata on strategy instances, supports named
Webull account aliases from `.env`, and persists live order intents plus reconciliation
events. The live adapter can build the documented Webull market order shape and persist an
intent before submission, but strategy quote evaluation still does not call that adapter.

No runtime path transmits live orders yet. Any future wiring must keep Webull as account and
order truth, use deterministic client order IDs, block duplicate in-flight actions, and pass
runtime mode, master switch, per-strategy live execution, quote freshness, account alias,
symbol, and reconciliation gates before submitting.

## Volatile market display

Market quotes and series are not persisted. Runtime code keeps only current quote state in
memory for strategy decisions and a capped in-memory stream buffer for operator visibility.
Cleanup may clear legacy `quote_snapshots` and `bars` rows.
