# Architecture Decisions

## Legacy bridge removed

The old external-alert bridge has been removed from the project. The repo is being reset for
Python-native strategies that consume InsightSentry streaming data and use Webull for
live account operations and gated market-order execution.

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
- `live`: Webull account reads plus gated live market-order execution.

Strategy instances are configured from ignored local files, `config/strategies.test.yml` or
`config/strategies.live.yml`, based on the active mode. Tracked `.example.yml` files show
safe demo/skeleton configuration only. Strategy additions and parameter changes are made in
local source/config, not through the UI. The local API supports runtime pause/resume controls
for configured instances.

`webull-auto-trading serve` owns one background InsightSentry quote WebSocket consumer. It
builds one complete replacement subscription set from every enabled strategy's requirements,
keeps quote, series, and raw stream display state in process memory, and surfaces missing
credentials, idle symbols, reconnecting, and stream errors through a safe local status
endpoint. Global pause does not stop market data visibility.

Operator pause state is persistent. Config `enabled` seeds a new strategy ID, while the
SQLite strategy setting remains authoritative for an existing ID. Global and per-strategy
flatten controls are paper-only: they pause execution, cancel virtual pending orders, and
close paper positions only when an in-memory current quote provides the documented paper
close side. Live mode rejects broad flatten; live exits are produced by the same gated
strategy close path that submits Webull market orders.

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

## Gated live execution

Live mode stores explicit live execution metadata on strategy instances, supports named
Webull account aliases from `.env`, persists live order intents plus reconciliation events,
and uses InsightSentry quote-driven strategy decisions to submit Webull market orders only
after all gates pass.

The runtime keeps Webull as account and order truth, uses deterministic client order IDs
that include a unique virtual execution leg, blocks duplicate in-flight actions, and
requires runtime mode, master switch, per-strategy live execution, quote freshness, account
alias, successful BUY/SELL previews, current account state, and reconciliation gates before
submitting. Futures OCO/OTOCO entry brackets and SL/TP/trailing state are virtual in the
backend; opens and closes transmit as market orders only.

Order Detail is polled within its documented limit because Open Orders and Order History can
lag. Allocations transition through `OPENING`, `OPEN`, `CLOSING`, `CLOSED`, or an explicit
error state. Partial fills remain in-flight. Initial stops, final targets, and trailing
calculations use the actual confirmed Webull fill price. A rejected, cancelled, or unknown
mutation pauses the strategy instead of retrying.

Balance and position snapshots refresh every five seconds. `total_net_liquidation_value`
feeds account-wide daily loss controls, while snapshots older than 15 seconds block entries.
The Webull aggregate position for an account alias and symbol must equal the signed sum of
all tagged strategy allocations plus the captured external baseline. Same-symbol strategies
remain independently attributed even when their positions net at Webull. An unexplained
mismatch pauses all matching strategies and is never auto-adopted or auto-flattened.

## Session-relative strategy safety

Strategies can request series history, consume account snapshots, export/import durable
control state, and receive periodic timer callbacks without breaking existing quote-only
strategies. Market bars remain volatile. Durable state is limited to session identity,
cycle/cooldown data, equity baseline, active cycle identity, and add-on counters.

CME-relative futures strategies define the session in `America/Chicago`, derive yesterday's
range from the last completed exchange session, delay entry relative to the daily candle
open, and use timer callbacks for the pre-maintenance flatten. Contract-specific last-entry
and force-exit timestamps replace reusable EA license expiration dates. Strategy-only
grouped exits never close allocations owned by another strategy.

## Volatile market display

Market quotes and series are not persisted. Runtime code keeps only current quote state in
memory for strategy decisions and a capped in-memory stream buffer for operator visibility.
Cleanup may clear legacy `quote_snapshots` and `bars` rows.
