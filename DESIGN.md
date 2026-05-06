# Polymarket Trading Bot — Design

## 1. Goals & Non-Goals

**Goals**
- Place, cancel, and manage limit/market orders on Polymarket CLOB programmatically.
- Run pluggable strategies (arbitrage, market-making, event-driven) in parallel.
- Enforce hard risk limits before any order leaves the process.
- Keep accurate local state (positions, P&L, fills) reconciled with on-chain truth.
- Support paper-trading and backtest modes from the same strategy code.

**Non-goals (v1)**
- Cross-chain bridging, fiat on-ramps, tax accounting.
- Mobile/Web UI — CLI + structured logs only.
- Trading on platforms other than Polymarket (Kalshi/Manifold are *signal sources* only).

## 2. Polymarket Surface

| Concern | Endpoint / Mechanism |
|---|---|
| Market metadata | Gamma REST API (`/markets`, `/events`) |
| Order book + trading | CLOB REST + WebSocket (`/book`, `/order`, `/trades`) |
| Auth (L1) | Polygon EOA private key, EIP-712 order signing |
| Auth (L2) | API key + secret + passphrase (HMAC) |
| Settlement | USDC on Polygon, UMA optimistic oracle for resolution |
| Order types | GTC, GTD, FOK, FAK; binary YES/NO at 0–1 USDC |

Two clients are needed: **read-only** (no signer, just market data) and **signing** (private key + L2 creds). Most processes only need read; the *executor* alone holds the signer.

## 3. Architecture

```
                  +------------------+
                  |  Strategy Pool   |   pure functions: (state) -> intents
                  +---------+--------+
                            | OrderIntent
                            v
+-----------+  ticks  +-----+-----+  approved   +-----------+   signed   +---------+
| MarketData|-------->| RiskGate  |------------>| Executor  |----------->| CLOB API|
|  Bus      |         | (limits)  |             | (signer)  |            +----+----+
+-----+-----+         +-----+-----+             +-----+-----+                 |
      ^                     ^                         |                       |
      |                     |                         v                       |
      |                +----+-----+              +----+-----+                 |
      |                | Position |<-------------|  Fills   |<----------------+
      |                |  Store   |   reconcile  |  Stream  |  WebSocket
      |                +----------+              +----------+
      |
      +-- WebSocket book + Gamma poller
```

Components are independent processes/tasks communicating over an in-memory bus (asyncio queues for v1; swap to Redis Streams when we need horizontal scale).

### 3.1 Components

- **MarketData Bus** — subscribes to CLOB WS for order books on watched markets; polls Gamma for metadata; publishes normalized `BookUpdate`, `Trade`, `MarketResolved` events.
- **Strategy Pool** — each strategy is a coroutine consuming the bus and emitting `OrderIntent { market_id, side, size, price, ttl, strategy_id }`. Strategies are stateless w.r.t. execution; they receive a read-only view of `PositionStore`.
- **RiskGate** — single chokepoint. Rejects intents that violate config: per-market notional, total notional, max position skew, daily loss, kill-switch flag, sanity bounds (price ∈ [0.01, 0.99]).
- **Executor** — only component holding the signer. Signs EIP-712, posts to CLOB, retries idempotently on transient errors, surfaces fills via WS user channel.
- **PositionStore** — append-only event log (SQLite/Postgres) of orders + fills; derives current positions and realized/unrealized P&L. Reconciles vs `/positions` endpoint every N seconds; alerts on drift.
- **Scheduler/Supervisor** — top-level asyncio orchestrator; restarts crashed tasks with exponential backoff; flips kill-switch on repeated failures.

## 4. Strategies (v1 candidates)

1. **YES/NO arb** — within a single market, if `ask(YES) + ask(NO) < 1 - fees`, buy both; symmetric for sell side. Lowest-risk starting strategy.
2. **Stale-quote sniper** — when book moves on Kalshi/Manifold for a correlated event but Polymarket hasn't, take the stale side. Needs cross-platform market mapping (manual whitelist v1).
3. **Resolution-edge fade** — late in a market's life, fade prices >0.97 / <0.03 if resolution criterion is still genuinely uncertain (gated by human-curated watchlist).
4. **Event-driven (LLM-assisted)** — ingest news headlines, ask Claude to estimate directional impact + confidence, size accordingly. This is where the `claude-api` skill applies during implementation; use prompt caching on the market-context block.

Each strategy ships with: config schema, expected edge, max sizing, required signals, kill conditions.

## 5. Risk Model

Hard limits enforced in `RiskGate` (config-driven, no strategy override):

- `max_notional_per_market_usdc`
- `max_total_notional_usdc`
- `max_orders_per_minute`
- `max_daily_realized_loss_usdc` → trips kill-switch
- `min_price`, `max_price` (default 0.02 / 0.98)
- `allowed_market_ids` whitelist for v1

Soft checks (warn, don't block): inventory skew, age of last reconciliation, WS staleness.

Kill-switch: a single boolean in shared state; set manually (CLI), by supervisor (repeated errors), or by daily-loss breach. Executor refuses to sign while set; running orders are cancelled.

## 6. Data & Persistence

- **SQLite** for v1 (single-process). Schema:
  - `orders(id, client_id, market_id, side, size, price, status, ts)`
  - `fills(order_id, size, price, fee, ts, tx_hash)`
  - `events(ts, kind, payload_json)` — append-only audit log
  - `markets(id, slug, question, end_ts, resolution_source, ...)` cache
- Migrate to Postgres + Redis Streams when adding a second executor or backtest workers.

## 7. Backtesting & Paper Trading

Same strategy interface; swap the executor:
- **PaperExecutor** — simulates fills using live book (no signer, no orders sent).
- **BacktestExecutor** — replays historical CLOB snapshots from S3/local parquet, deterministic clock.

This requires strategies to read time from an injected `Clock` and book state from the bus, not wall-clock or live REST. Enforce via lint/typing (no `time.time()` in `strategies/`).

## 8. Tech Stack

- **Python 3.12**, `asyncio`, `httpx`, `websockets`, `pydantic` v2 for schemas.
- **`web3.py`** + `eth-account` for EIP-712 signing.
- **`py-clob-client`** (official) wrapped behind our own `PolymarketClient` interface so we can mock it.
- **`structlog`** + JSON logs; **OpenTelemetry** traces around each intent → fill.
- **`pytest`** + `pytest-asyncio`; **`hypothesis`** for risk-gate property tests.
- **`uv`** for dependency management.

## 9. Security

- Private key in OS keychain or KMS — never in `.env` for production. Dev uses `.env` (already gitignored).
- L2 API secret stored alongside private key.
- Separate keys per environment (paper/live); fund the live key minimally.
- All signed payloads logged (without the signature) for audit.
- CI runs `security-review` skill on PRs that touch `executor/` or `risk/`.
- Rate-limit our own outbound to stay well under Polymarket's caps; circuit-breaker on 429s.

## 10. Repo Layout

```
zero-strike/
├── pyproject.toml
├── src/zero_strike/
│   ├── clients/         # polymarket, gamma, ws
│   ├── marketdata/      # bus, normalizers
│   ├── strategies/      # one module per strategy
│   ├── risk/            # gate, limits, kill-switch
│   ├── executor/        # signer, order lifecycle
│   ├── store/           # sqlite, models, reconciler
│   ├── backtest/        # historical replay
│   └── cli.py           # run / paper / backtest / cancel-all / kill
├── configs/             # strategy + risk YAMLs per env
├── tests/
└── DESIGN.md            # this file
```

## 11. Milestones

1. **M1 — Read path**: clients, market-data bus, position store reading from `/positions`. No orders.
2. **M2 — Paper trading**: strategy interface, PaperExecutor, YES/NO arb strategy. Verify edge offline.
3. **M3 — Risk gate + signer**: implement EIP-712 signing in isolation, full risk gate with property tests, kill-switch CLI.
4. **M4 — Live executor**: smallest possible position sizes; ship behind whitelist + low daily-loss cap.
5. **M5 — Backtest harness**: historical book recorder, BacktestExecutor, replay reports.
6. **M6 — LLM strategy**: event-driven strategy using Claude API with prompt caching.

## 12. Open Questions

- Use `py-clob-client` directly or fork? (Probably wrap, then decide based on missing features.)
- Single-process asyncio vs. process-per-strategy from day one? Leaning single-process until we hit GIL or blast-radius issues.
- Where to store historical book snapshots — local parquet, S3, or DuckDB-on-S3? Defer until M5.
- Self-host a Polygon RPC, or rely on Alchemy/Infura? Start with hosted, monitor latency.
