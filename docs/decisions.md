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

## No strategy runtime yet

This cleanup does not add an InsightSentry client, strategy engine, scheduler, persistence
model, or order executor. Those will be designed after the repository no longer carries the
old bridge abstractions.

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

Ordinary tests cover configuration, account response summarization, redaction, and Webull
error sanitization. They must not call Webull, InsightSentry, or submit live orders.
