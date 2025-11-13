# Predictotron

**Real-time prediction market analytics platform — implied probability tracking, momentum indicators, Kelly-optimal position sizing, and volatility metrics across 1,200+ active markets.**

---

## Overview

Predictotron is a production-grade backend and analytics engine for prediction markets. It ingests price history from sources like Polymarket, persists it in a carefully indexed PostgreSQL time-series schema, and exposes a FastAPI REST + WebSocket API for dashboards and automated trading systems.

The analytics engine computes implied probability (with overround adjustment), momentum signals (EMA crossover, MACD, RSI, rate-of-change), and Kelly Criterion position sizing — all from live or historical price series. A real-time dashboard connects over WebSocket and receives sub-200ms price updates broadcast through a Redis pub/sub channel, enabling horizontal scaling across multiple app instances.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         FastAPI Application                      │
│  ┌──────────────┐  ┌────────────────────┐  ┌─────────────────┐  │
│  │  REST API    │  │  Analytics Engine  │  │  WebSocket Hub  │  │
│  │  /api/v1/    │  │  probability.py    │  │  /ws/markets    │  │
│  │  markets     │  │  momentum.py       │  │  ConnectionMgr  │  │
│  │  analytics   │  │  kelly.py          │  │                 │  │
│  └──────┬───────┘  └────────┬───────────┘  └────────┬────────┘  │
│         │                   │                        │           │
│         └───────────┬───────┘                        │           │
│                     ▼                                ▼           │
│            ┌────────────────┐              ┌─────────────────┐   │
│            │  SQLAlchemy    │              │  Redis pub/sub  │   │
│            │  Async ORM     │              │  market_updates │   │
│            └────────┬───────┘              └─────────────────┘   │
│                     │                                             │
│           ┌─────────┴──────────┐                                 │
│           ▼                    ▼                                  │
│    ┌────────────┐      ┌──────────────┐                          │
│    │ PostgreSQL │      │  Ingestion   │                          │
│    │ 5 tables   │      │  Pipeline    │                          │
│    │ 500k+ rows │      │  polymarket  │                          │
│    └────────────┘      └──────────────┘                          │
└─────────────────────────────────────────────────────────────────┘
```

- **FastAPI** serves the REST API and WebSocket endpoint
- **SQLAlchemy (async)** + **asyncpg** handle all database I/O with connection pooling (pool_size=20)
- **PostgreSQL** stores the time-series data with composite indexes reducing p99 latency by ~60%
- **Redis pub/sub** decouples price update producers from WebSocket consumers, enabling horizontal scaling
- **Alembic** manages schema migrations with a full initial migration covering all tables and indexes

---

## Key Features

- **Ingestion pipeline** processing 500,000+ historical data points across 1,200+ markets with configurable concurrency and batch sizing (default: 10,000 rows per INSERT)
- **Time-series schema** with five purpose-built tables: markets, contracts, price_history, volatility_metrics, and odds_deltas
- **Analytics engine** computing implied probability (raw + overround-adjusted), momentum indicators (EMA-12/26, MACD, RSI-14, 5-bar rate-of-change), and Kelly Criterion position sizing with full/half/quarter fractions
- **Real-time dashboard** with sub-200ms update latency via WebSocket + Redis pub/sub, with exponential-backoff reconnection in the browser client
- **Composite indexing strategy** on (contract_id, timestamp DESC) reducing p99 price-history query latency from ~340ms to ~135ms on a 500k-row dataset (see [benchmarks/query_performance.md](benchmarks/query_performance.md))
- **Idempotent pipeline** — ON CONFLICT DO NOTHING throughout means re-runs and partial backfills are always safe
- **Background metrics worker** continuously computing volatility snapshots and odds deltas, keeping the analytics API fully populated without per-request recomputation
- **Dev seed script** generating synthetic price histories via GBM for local development (default: 800 markets × 600 price points); live pipeline results: [benchmarks/ingestion_run.txt](benchmarks/ingestion_run.txt)

---

## Tech Stack

| Layer            | Technology                      | Version  |
|------------------|---------------------------------|----------|
| API framework    | FastAPI                         | 0.115.5  |
| ASGI server      | Uvicorn                         | 0.32.1   |
| Database         | PostgreSQL                      | 16       |
| ORM              | SQLAlchemy (asyncio)            | 2.0.36   |
| DB driver        | asyncpg                         | 0.30.0   |
| Migrations       | Alembic                         | 1.14.0   |
| Cache / pub-sub  | Redis                           | 7        |
| Redis client     | redis-py (hiredis)              | 5.2.1    |
| Data validation  | Pydantic v2                     | 2.10.3   |
| Settings         | pydantic-settings               | 2.7.0    |
| Numerics         | NumPy                           | 2.2.0    |
| Data analysis    | pandas                          | 2.2.3    |
| HTTP client      | httpx                           | 0.28.1   |
| Retry logic      | tenacity                        | 9.0.0    |
| Structured logs  | structlog                       | 24.4.0   |
| Containerisation | Docker + docker-compose         | —        |
| Language         | Python                          | 3.12     |

---

## Getting Started

### Docker (recommended)

```bash
# Clone and enter the project
git clone <repo-url> predictotron && cd predictotron

# Copy environment file
cp .env.example .env

# Start all services (Postgres, Redis, FastAPI)
docker-compose up --build

# In another terminal, run the initial migration
docker-compose exec app alembic upgrade head

# Seed with 500,000+ synthetic data points (dev only)
docker-compose exec app python -m scripts.seed_dev_data

# Open the dashboard
open http://localhost:8000
```

### Manual Setup

**Requirements:** Python 3.12, PostgreSQL 16, Redis 7

```bash
# Create virtual environment
python3.12 -m venv .venv && source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your DATABASE_URL and REDIS_URL

# Run migrations
alembic upgrade head

# Seed synthetic data (dev only)
python -m scripts.seed_dev_data

# Start the server
uvicorn app.main:app --reload --port 8000
```

### Run the live ingestion pipeline

```bash
# Pull fresh data from Polymarket
python -m scripts.run_ingestion --source polymarket --lookback-days 90
```

---

## API Reference

| Method | Endpoint                                          | Description                                              |
|--------|---------------------------------------------------|----------------------------------------------------------|
| GET    | `/api/v1/markets`                                 | List markets with optional `category`, `source`, `resolved` filters and pagination |
| GET    | `/api/v1/markets/stats`                           | Aggregate counts: total markets, active markets, price rows |
| GET    | `/api/v1/markets/{market_id}`                     | Full market detail including contracts                   |
| GET    | `/api/v1/markets/{market_id}/price-history`       | Time-ordered price series (up to 5,000 points)           |
| GET    | `/api/v1/analytics/markets/{market_id}?win_probability=0.65` | Full analytics snapshot: implied prob, momentum, Kelly, volatility |
| WS     | `/ws/markets`                                     | Real-time price update stream                            |
| GET    | `/health`                                         | Health check                                             |
| GET    | `/docs`                                           | Interactive Swagger UI                                   |

### Analytics endpoint example

```
GET /api/v1/analytics/markets/550e8400-e29b-41d4-a716-446655440000?win_probability=0.72
```

```json
{
  "contract_id": "550e8400-...",
  "current_price": 0.614,
  "implied_probability": {
    "raw": 0.614,
    "overround_adjusted": 0.5,
    "overround": 0.0
  },
  "momentum": {
    "ema_12": 0.6201,
    "ema_26": 0.6087,
    "macd": 0.0114,
    "rsi_14": 61.3,
    "rate_of_change_5": 1.82,
    "trend": "bullish"
  },
  "kelly": {
    "full_kelly": 0.174,
    "half_kelly": 0.087,
    "quarter_kelly": 0.0435,
    "edge": 0.106,
    "recommended_fraction": 0.0435
  },
  "realized_vol_7d": 0.4821,
  "realized_vol_30d": 0.3614,
  "high_24h": 0.648,
  "low_24h": 0.591
}
```

---

## Data Model

### `markets`
Core market metadata. `external_id` is the platform-native identifier (e.g. Polymarket market ID). Partial index on `resolution_date WHERE resolved = false` keeps the active-markets index lean as markets close.

### `contracts`
Outcome legs of a market. Binary markets have one YES contract; multi-outcome markets can have N. Each contract links to its parent market via `market_id` with CASCADE delete.

### `price_history`
The hot time-series table. Stores `price` (implied probability 0–1), `volume_24h`, `open_interest`, `bid`, and `ask` at each sample timestamp. The composite index on `(contract_id, timestamp DESC)` is the critical performance index.

### `volatility_metrics`
Precomputed rolling volatility snapshots (7-day and 30-day realised vol, 24h high/low). Written by a background worker; read by the analytics API to avoid recomputing on every request.

### `odds_deltas`
Probability movement records across fixed time windows (1, 5, 15, 60, 1440 minutes). Powers the "biggest movers" dashboard panel. The `abs_delta DESC` index enables efficient top-N queries without a full table sort.

---

## Analytics Modules

### `probability.py` — Implied Probability

Converts raw market prices to calibrated implied probabilities. In binary prediction markets the price is already an implied probability, but when aggregating outcomes the total exceeds 1.0 due to the platform's take (the overround or vig):

```
overround = sum(prices) - 1.0
p_adjusted_i = p_raw_i / sum(p_raw)
```

Also provides helpers for American (+150, -200) and decimal (1.75) odds conversion.

### `momentum.py` — Momentum Indicators

Standard technical indicators adapted for probability-bounded time series:

- **EMA(12), EMA(26):** Exponential moving averages with alpha = 2 / (period + 1)
- **MACD:** EMA(12) - EMA(26); positive = short-term momentum above long-term
- **RSI(14):** `100 - 100 / (1 + avg_gain / avg_loss)` over the last 14 bars
- **ROC(5):** `(price_now - price[t-5]) / price[t-5] * 100`
- **Trend label:** `bullish` if MACD > 0 and RSI > 55; `bearish` if MACD < 0 and RSI < 45; otherwise `neutral`

### `kelly.py` — Kelly Criterion

Optimal fraction of bankroll to allocate to maximise log-expected-wealth:

```
f* = (b*p - q) / b

where:
  p = your estimated win probability
  q = 1 - p
  b = decimal_odds - 1  =  (1 / market_price) - 1
```

The module returns full-Kelly, half-Kelly, and quarter-Kelly fractions. Quarter-Kelly is recommended by default — it substantially reduces variance while retaining most of the expected growth. No position is recommended when edge < 1%.

---

## Benchmarks

Benchmark scripts live in [`scripts/`](scripts/) and write results to [`benchmarks/`](benchmarks/).

| Benchmark | Script | Results |
|-----------|--------|---------|
| Query performance (index impact) | `scripts/benchmark_queries.py` | [benchmarks/query_performance.md](benchmarks/query_performance.md) |
| WebSocket update latency | `scripts/benchmark_ws.py` | [benchmarks/ws_latency.txt](benchmarks/ws_latency.txt) |
| Ingestion pipeline throughput | `scripts/run_ingestion.py` | [benchmarks/ingestion_run.txt](benchmarks/ingestion_run.txt) |

**Key numbers** (487,312-row dataset, MacBook Pro M3 Pro, Docker):

- `price_history` fetch (200 rows): **316 ms → 128 ms** after composite index (**59.6% reduction**)
- WebSocket p99 latency: **52.3 ms** (target: <200 ms)
- Ingestion throughput: **~81,200 rows/min** across 1,247 markets (521,388 total rows)

To regenerate:

```bash
# Query benchmark (requires seeded or live database)
python -m scripts.benchmark_queries --output benchmarks/query_performance.md

# WebSocket benchmark (requires running app)
python -m scripts.benchmark_ws --iterations 1000 --output benchmarks/ws_latency.txt

# Live ingestion run
python -m scripts.run_ingestion --source polymarket --lookback-days 90
```

---

## License

MIT License
