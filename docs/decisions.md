# Architecture decisions

## Strategy-owned order sessions

Each strategy actively places, replaces, and cancels its own logical orders through a
credential-free `OrderSession`. A central gateway performs transport and shared safety but
does not decide trading logic. This matches the EA/terminal split in MT4 while keeping
plugins broker-neutral.

## Process isolation

Each running instance uses a spawned process and bounded command/event channels. Strategy
failure cannot take down the control plane. A persisted snapshot and order-event replay
restore the instance's own tickets after restart.

## Virtual position ledgers

Webull nets positions by account and instrument. Strategy fills are nevertheless attributed
to isolated FIFO ledgers using client order IDs. Manual/unrecognized fills enter an
`UNASSIGNED` ledger. Virtual independence is accounting independence, not separate custody.

## Feed and broker separation

Market source and execution mode are independent. LOCAL_SIM can consume production, UAT,
or replay data. Broker adapters share the same order/session contract.

## Close-first futures roll

Auto-roll blocks entries, closes and reconciles the expiring contract, then reopens the same
signed quantity in the next eligible contract. A failed reopen leaves the strategy flat and
paused; expiry protection remains independent of strategy health.

## Source and UI ownership

Python, manifests, schemas/defaults, schedules, dependencies, and tests live in source files.
The UI configures immutable instances and operates them; it never rewrites plugin source.

## Local credentials

Personal credentials live in a gitignored `.env`. Only broker/feed adapters in the host
process load them; worker environments have all Webull variables removed. Production live
mode requires an environment flag plus an explicit UI confirmation.

The official SDK's reusable 2FA token is stored per environment under the ignored
`.runtime/webull_tokens/` directory. HTTP connect/read timeouts are bounded. UAT token
verification fails quickly, while production preserves Webull's five-minute app-verification
window.

## Conda runtime ownership

The named `webull-strategy-desk` Conda environment owns Python, Node.js, and installed
packages on every development machine. The checkout is registered editable with pip from
its `pyproject.toml`, including the `dev` and `webull` extras. This adds an import/command
pointer to the working source rather than copying the repository. Normal commands invoke Python,
pytest, Ruff, and `strategy-desk` directly after activation; they do not use `uv run` or
`uv sync`, so command startup cannot reconcile or remove packages unexpectedly.

`pyproject.toml` remains the dependency source of truth and `uv.lock` remains a committed
resolution record. uv may update or validate that metadata, but it does not own the active
runtime environment. Environment recreation starts from `environment.yml` and then performs
the editable pip installation.
