# Webull Strategy Desk

A local, source-first strategy/EA manager for Webull stocks and futures. Strategies
own their logical orders through an MT4-like `OrderSession`; a credential-holding
execution gateway handles broker transport, shared validation, and reconciliation.

The current build includes isolated strategy workers, strategy-local order sessions,
SQLite WAL persistence with FIFO virtual lots, a configurable local simulator, Webull
market/trading adapters, HTTP order reconciliation, fixed/auto-roll futures expiry guards,
and the bilingual light/dark operations UI. Production execution is double-gated.

## Quick start

Requirements: Conda. The project environment installs Python 3.12 and a compatible
Node.js release.

```bash
conda env create -f environment.yml
conda activate webull-strategy-desk
python -m pip install -e ".[dev,webull]"  # editable pointer + dependencies
cp .env.example .env
strategy-desk init-db
strategy-desk serve --reload
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open <http://127.0.0.1:5173>. Local simulation is the default and production
order transmission is disabled unless both the environment and UI gates are set.

Copy values into the gitignored `.env` using the comments in `.env.example`. Strategy
workers never receive those variables. Webull market data may require a separate OpenAPI
quotes entitlement even when the desktop application already has quote access.
Reusable SDK tokens are cached in the ignored `.runtime/webull_tokens` directory.

## Local database maintenance

`strategy-desk init-db` creates or migrates the local SQLite database. It is idempotent and
does not wipe, prune, or reset existing data. By default, local runtime state persists in
`data/strategy_desk.db`, including strategy instances, runs, orders, fills, virtual
positions, FIFO lots, checkpoints, simulator accounts, and audit events.

Inspect the configured database without exposing payload details:

```bash
strategy-desk db-stats
```

The command prints JSON with the database path, whether the file exists, database/WAL/SHM
file sizes when present, and table row counts.

Prune old local runtime rows with an explicit retention window. Dry-run is the default:

```bash
strategy-desk db-prune --older-than 90d --dry-run
strategy-desk db-prune --older-than 90d --execute
```

Rows older than the cutoff are eligible in `strategy_instances`, `strategy_runs`, `orders`,
`fills`, `virtual_positions`, `virtual_lots`, `checkpoints`, and `audit_events`.
`simulator_accounts` are preserved because they are local account configuration. This is a
local reset/archive workflow: once rows are pruned, Strategy Desk relies on the archive and
broker-level Webull history for review rather than local strategy-state restoration.
Use a conservative retention policy, such as keeping at least 90 days of local runtime data.

Archive eligible rows before deleting them:

```bash
strategy-desk db-prune --older-than 90d \
  --archive data/archives/strategy_desk_YYYYMMDD.sqlite3 \
  --execute
```

Archive databases are local runtime artifacts under `data/archives/`, which is gitignored;
they should not be committed. After a prune, run compaction explicitly when desired:

```bash
strategy-desk db-vacuum
```

`db-vacuum` runs a WAL checkpoint/truncate and SQLite `VACUUM`; it is never automatic.

Conda owns the interpreter and installed packages. The editable installation does not copy
the repository into Conda; it registers a pointer to this checkout and provides the
`strategy-desk` command. `pyproject.toml` remains the source of dependency declarations and
`uv.lock` remains the tracked resolution record, but normal
development and runtime commands do not use `uv run` or `uv sync`. When dependencies
change, update the lock without modifying the active Conda environment:

```bash
uv lock
python -m pip install -e ".[dev,webull]"
```

The `webull` extra installs the official SDK boundary used by UAT and production adapters.
Run all commands only after activating `webull-strategy-desk`; `which python` on macOS/Linux
or `where python` on Windows should point into that Conda environment.

Run offline verification with `python -m pytest` and `ruff check .`, followed by
`cd frontend && npm run build`. The read-only UAT checks remain explicit opt-in and are
not part of ordinary verification:

```bash
RUN_WEBULL_UAT_TESTS=1 python -m pytest tests/integration/test_webull_uat.py
```

Validate and import a replay before selecting its path in the instance form:

```bash
strategy-desk replay-import /path/to/quotes.jsonl --name morning-mgc.jsonl
strategy-desk diagnose
```

To rebuild rather than update the environment, deactivate it, remove it with
`conda env remove -n webull-strategy-desk`, and repeat the three setup commands above.

See [AGENTS.md](AGENTS.md), [strategies/AGENTS.md](strategies/AGENTS.md), and
[docs/decisions.md](docs/decisions.md) for architecture, plugin contracts, safety rules,
and source/UI ownership decisions.
