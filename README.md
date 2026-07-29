# Webull Auto Trading

Python foundation for a local InsightSentry plus Webull trading console. The old
external-alert bridge has been removed and replaced with Python-native strategy instances,
SQLite persistence, paper virtual orders, a FastAPI backend, and a React/Vite operator
console.

Runtime mode is global: `test` mode paper trades against real InsightSentry market data, and
`live` mode allows Webull account/balance/position/open-order reads through the official SDK.
It also has named account aliases, persisted live order intents, fill-confirmed tagged
allocations, and safety-gated market-order execution. Live transmission remains blocked
unless the global mode, environment master switch, per-strategy switch, account alias,
read-only previews, fresh quote/account state, and broker reconciliation are all ready.

## Quick Start

Use the existing Conda environment that already works with the Webull SDK:

```bash
conda activate webull-strategy-desk
python -m pip install -e ".[dev,webull]"
cp .env.example .env
webull-auto-trading diagnose
webull-auto-trading diagnose-live
webull-auto-trading init-db
```

Discover Webull account id candidates from the authenticated SDK session:

```bash
webull-auto-trading accounts
```

Use `webull-auto-trading accounts --raw` only when you need the redacted raw account-list
response for troubleshooting. Sensitive keys such as tokens, app keys, app secrets,
passwords, and authorization fields are replaced with `<redacted>`.

## Configuration

`.env` is ignored and should contain only local secrets and machine-specific settings:

```text
WEBULL_REGION=us
WEBULL_PROD_APP_KEY=
WEBULL_PROD_APP_SECRET=
WEBULL_TOKEN_DIR=.runtime/webull_tokens
WEBULL_PROD_TOKEN_WAIT_SECONDS=300
WEBULL_ACCOUNT_ID=
WEBULL_ACCOUNT_DEFAULT_ALIAS=stock_margin
WEBULL_ACCOUNT_STOCK_CASH_ID=
WEBULL_ACCOUNT_STOCK_MARGIN_ID=
WEBULL_ACCOUNT_FUTURES_ID=
LIVE_EXECUTION_MASTER_ENABLE=false
```

SDK tokens are stored under `.runtime/webull_tokens/live`, which is ignored by git. Do not
commit `.env`, `.runtime/`, Webull logs, account secrets, access tokens, or copied raw API
payloads containing secrets.

## Commands

```bash
webull-auto-trading diagnose
webull-auto-trading diagnose-live
webull-auto-trading accounts
webull-auto-trading accounts --raw
webull-auto-trading init-db
webull-auto-trading serve --reload
webull-auto-trading run
webull-auto-trading cleanup --dry-run
```

`serve` starts the local backend, InsightSentry market stream worker, and periodic live
safety/reconciliation coordinator on
<http://127.0.0.1:8765> by default. The API remains usable if stream credentials are missing
or the stream is reconnecting. The frontend lives in `frontend/` and proxies `/api` to that
backend during Vite development.

```bash
cd frontend
npm install
npm run dev
```

Mode-specific strategy configs are ignored local files. Start from the tracked examples:

```bash
cp config/strategies.test.example.yml config/strategies.test.yml
cp config/strategies.live.example.yml config/strategies.live.yml
```

Add real strategies there, not through the UI. Keep real symbols, private strategy names,
and tuned parameters out of git. The tracked public strategy package includes only the base
interface, package init, and the disclosed `recycle_buy` demo; private strategy modules can
live beside it as ignored local files.

`recycle_buy` works in Test mode and through the existing gated Live market-order path. A
Live entry waits for fresh account data, successful BUY/SELL previews, and ready position
reconciliation. It treats every in-flight allocation state as active so a second quote
cannot create a duplicate entry, scopes stop/target handling to its own strategy instance,
and recalculates risk levels from the confirmed Webull fill. Its cooldown state is durable,
but the strategy intentionally has no daily cycle limit, trading window, or contract cutoff.
Keep a new Live instance paused until those operating limits are acceptable.

Pausing one strategy from the operator console cancels only that instance's local pending
virtual entries and preserves them as `CANCELLED` history in the test or live virtual-order
store. It does not cancel a Webull order or close broker exposure, and the disabled strategy
stops running its normal quote/timer risk management. Resuming does not restore cancelled
entries; a later eligible evaluation may create a fresh setup from the current YAML
parameters.

Strategies may request quote and complete replacement series subscriptions, consume live
account snapshots, export control state, and run timer-based safety checks. Quotes and OHLC
bars stay in memory. Durable strategy state must contain only control data such as session
identity, cycle counts, cooldowns, account-equity baselines, and active allocation counters.

Live futures brackets are virtual because Webull does not support futures OCO/OTOCO
combinations. A virtual trigger submits a gated market order, then remains `OPENING` until
Order Detail confirms the fill. Confirmed allocations become `OPEN`, use the actual fill
price for risk levels, pass through `CLOSING`, and become `CLOSED` only after the exit fill.
Rejected, cancelled, unknown, or mismatched mutations pause the affected strategy. An
unexplained aggregate position mismatch pauses every strategy sharing that account alias
and Webull symbol; the runtime never auto-adopts or auto-flattens the mismatch.

CME-relative strategies should express their daily open/close in a named time zone rather
than fixed machine-local windows. Their periodic safety checks must cancel virtual entries
and create strategy-only grouped exits before the maintenance break and enforce any
contract-specific last-entry and force-exit timestamps.

## Verification

Normal verification must not call Webull, InsightSentry, or place live orders:

```bash
python -m pytest
ruff check .
cd frontend && npm run build
```

For Webull behavior, use the current official documentation from
<https://developer.webull.com/apis/>, especially the Trading API account/order, SDK,
authentication, and token pages. For future InsightSentry work, use
<https://insightsentry.com/docs/ws>.
