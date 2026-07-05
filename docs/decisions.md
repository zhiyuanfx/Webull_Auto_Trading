# Architecture Decisions

## Legacy bridge removed

The old external-alert bridge has been removed from the project. The repo is being reset for
Python-native strategies that consume InsightSentry streaming data and use Webull only for
account and trading operations.

## Minimal Webull foundation

The Webull Trading API boundary remains because account lookup, positions, open orders, and
order API access are useful for the next runtime. The wrapper uses the official Python SDK
and production token cache. Endpoint fields, SDK method names, hosts, and entitlement
behavior must be verified against current official Webull documentation before any Webull
behavior changes.

## Paper-first multi-strategy runtime

The next runtime baseline is a local Python service with SQLite persistence, a shared
InsightSentry market-data boundary, strategy instances isolated by `strategy_instance_id`,
paper virtual orders, and a FastAPI/React operator UI. Strategy instances are configured from
`config/strategies.yml` and can also be edited through the local API.

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
bracket logic, per-instance isolation, and runtime persistence. They must not call Webull,
InsightSentry, or submit live orders.

## No live order placement in runtime v1

The runtime exposes `paper` and `preview` strategy modes only. Paper mode owns the virtual
order lifecycle. Preview mode is reserved for paper lifecycle plus verified Webull preview
calls, but live placement, replacement, and cancellation are out of scope for this phase.
