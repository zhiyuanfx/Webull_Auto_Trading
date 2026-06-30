# Architecture Decisions

## TradingView owns strategy logic

The project is no longer a strategy desk. TradingView/Pine Script owns market data, strategy
rules, alert timing, symbols, sizes, and prices. The local app is a bridge that receives a
webhook instruction and executes it on Webull after validation.

## Live-only Webull execution

The bridge uses the production Webull Trading API through the official Python SDK. UAT and
local simulation were removed because the intended operating model is direct low-size live
testing and live execution. Safety is provided by validation, idempotency, local persistence,
global execution pause, per-route pause, route limits, and explicit confirmations.

## Local-only intake

Version 1 is fully local. TradingView reaches the FastAPI webhook endpoint through a
user-managed public HTTPS tunnel or port-forwarder. The app does not implement Cloudflare,
AWS, serverless workers, remote queues, or cloud storage. Webhook intake persists valid
events locally before any order work.

## Fast webhook response

TradingView webhook responses are delivery receipts, not broker execution acknowledgements.
The bridge validates the secret/schema, deduplicates, stores the event in SQLite, and returns
quickly. Webull preview/place/cancel/replace work happens from the local queue after intake.

## Secret and credential handling

Personal Webull credentials live in gitignored `.env` and SDK tokens live under ignored
`.runtime/webull_tokens/live`. Webhook shared secrets are compared by hash and are not
stored in event history. Logs and error messages must redact Webull keys/secrets and webhook
secrets.

## Conda runtime ownership

The existing `webull-strategy-desk` Conda environment remains the owned runtime because it
has already verified live Webull order placement. The project is installed editable with
`python -m pip install -e ".[dev,webull]"`; normal commands do not use `uv run` or `uv sync`.
