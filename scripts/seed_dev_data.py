#!/usr/bin/env python3
"""
Development seed script: generate synthetic prediction market data for local
development and benchmark runs.

Produces realistic random-walk price histories using Geometric Brownian Motion.
Uses asyncpg directly (bypassing the ORM) for maximum insert throughput.

NOTE: This script is for local development only.  For production data, use
the live ingestion pipeline:

    python -m scripts.run_ingestion --source polymarket --lookback-days 90

See benchmarks/ingestion_run.txt for measured pipeline throughput against
the live Polymarket API.

Usage:
    python -m scripts.seed_dev_data
    python -m scripts.seed_dev_data --markets 800 --points-per-market 600
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

DATABASE_URL = "postgresql://predictotron:password@localhost:5432/predictotron"

CATEGORIES = ["Politics", "Economics", "Sports", "Science", "Crypto", "Climate", "Tech", "Entertainment"]
SOURCES = ["polymarket", "kalshi", "manifold"]

MARKET_TEMPLATES = [
    "Will {subject} happen before {date}?",
    "Will {subject} exceed {threshold} by {date}?",
    "Will {subject} win the {event}?",
    "Will {subject} be resolved by {date}?",
    "{subject}: Yes or No by {date}?",
]

SUBJECTS = [
    "Bitcoin", "the Fed", "the S&P 500", "OpenAI", "Tesla", "Apple",
    "the US election", "CPI inflation", "gold prices", "oil prices",
    "Ethereum", "interest rates", "the housing market", "AI regulation",
    "climate legislation", "semiconductor exports", "Meta", "Google",
    "SpaceX", "the ECB", "the Yuan", "natural gas", "uranium",
    "the Dow Jones", "Nvidia", "AMD", "Microsoft", "Amazon",
    "Anthropic", "xAI", "Mistral", "the IMF", "the World Bank",
    "NATO expansion", "the UN Security Council", "fusion energy",
    "quantum computing", "autonomous vehicles", "OPEC production cuts",
]


def generate_market_title(rng: random.Random) -> str:
    template = rng.choice(MARKET_TEMPLATES)
    subject = rng.choice(SUBJECTS)
    days_out = rng.randint(30, 365)
    date = (datetime.now() + timedelta(days=days_out)).strftime("%b %d, %Y")
    threshold = rng.choice(["$50k", "$100k", "5%", "10%", "20%", "50%"])
    event = rng.choice(["2025 election", "championship", "summit", "IPO", "merger", "vote", "audit"])
    return template.format(subject=subject, date=date, threshold=threshold, event=event)


def geometric_brownian_motion(
    s0: float,
    mu: float,
    sigma: float,
    n: int,
    dt: float = 1 / (24 * 60),  # 1-minute steps normalised to daily
) -> list[float]:
    """
    Simulate a probability path using geometric Brownian motion, reflected
    at the [0.01, 0.99] boundaries to keep prices valid.

    The reflection ensures the path stays in the valid probability range
    while preserving the random-walk character of real market prices.
    """
    prices = [s0]
    rng = random.Random()
    for _ in range(n - 1):
        prev = prices[-1]
        # Log-normal step: dS = S * (mu*dt + sigma*sqrt(dt)*dW)
        shock = rng.gauss(0, 1)
        log_return = (mu - 0.5 * sigma ** 2) * dt + sigma * math.sqrt(dt) * shock
        new_price = prev * math.exp(log_return)
        # Reflect at boundaries to keep probability in (0, 1)
        new_price = max(0.01, min(0.99, new_price))
        prices.append(new_price)
    return prices


async def seed(
    conn: asyncpg.Connection,
    n_markets: int,
    points_per_market: int,
) -> None:
    rng = random.Random(42)
    batch_size = 10_000
    now = datetime.now(tz=timezone.utc)

    total_rows = 0
    price_batch: list[tuple] = []

    print(f"Seeding {n_markets} markets x ~{points_per_market} points = ~{n_markets * points_per_market:,} rows")
    print(f"Batch size: {batch_size:,} rows per INSERT\n")

    for i in range(n_markets):
        market_id = uuid.uuid4()
        category = rng.choice(CATEGORIES)
        source = rng.choice(SOURCES)
        title = generate_market_title(rng)
        resolution_days = rng.randint(14, 365)
        resolution_date = now + timedelta(days=resolution_days)

        await conn.execute(
            """
            INSERT INTO markets (id, external_id, title, category, source, resolution_date)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (external_id) DO NOTHING
            """,
            market_id,
            f"{source}-{market_id}",
            title,
            category,
            source,
            resolution_date,
        )

        contract_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO contracts (id, market_id, external_id, name, side)
            VALUES ($1, $2, $3, $4, 'yes')
            """,
            contract_id,
            market_id,
            f"{source}-{market_id}-yes",
            title,
        )

        # Generate price history using GBM with varied drift and volatility
        s0 = rng.uniform(0.1, 0.9)
        mu = rng.gauss(0, 0.01)         # slight drift (positive or negative)
        sigma = rng.uniform(0.05, 0.3)  # annualised volatility
        prices = geometric_brownian_motion(s0, mu, sigma, points_per_market)

        # Spread timestamps over the lookback window (up to 90 days back)
        history_days = min(resolution_days, 90)
        start_ts = now - timedelta(days=history_days)
        interval = timedelta(days=history_days) / max(points_per_market - 1, 1)

        for j, price in enumerate(prices):
            ts = start_ts + interval * j
            volume = rng.uniform(5_000, 500_000)
            oi = rng.uniform(volume * 0.5, volume * 3)
            spread = rng.uniform(0.001, 0.02)
            bid = round(max(0.001, price - spread / 2), 6)
            ask = round(min(0.999, price + spread / 2), 6)
            price_batch.append((
                contract_id,
                ts,
                round(price, 6),
                round(volume, 2),
                round(oi, 2),
                bid,
                ask,
            ))

        if len(price_batch) >= batch_size:
            await conn.executemany(
                """
                INSERT INTO price_history
                    (contract_id, timestamp, price, volume_24h, open_interest, bid, ask)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT DO NOTHING
                """,
                price_batch,
            )
            total_rows += len(price_batch)
            price_batch = []
            pct = (i + 1) / n_markets * 100
            print(f"  [{i + 1:>4}/{n_markets}] {pct:5.1f}%  {total_rows:>9,} rows inserted")

    # Flush remainder
    if price_batch:
        await conn.executemany(
            """
            INSERT INTO price_history
                (contract_id, timestamp, price, volume_24h, open_interest, bid, ask)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT DO NOTHING
            """,
            price_batch,
        )
        total_rows += len(price_batch)

    print(f"\nDone. Total rows inserted: {total_rows:,} across {n_markets} markets.")
    print(f"Average per market: {total_rows / n_markets:.0f} rows")


async def main(n_markets: int, points_per_market: int) -> None:
    print(f"Connecting to {DATABASE_URL}...")
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await seed(conn, n_markets, points_per_market)
    finally:
        await conn.close()
        print("Connection closed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Predictotron with synthetic dev data")
    parser.add_argument("--markets", type=int, default=800, help="Number of markets to create")
    parser.add_argument("--points-per-market", type=int, default=600, help="Price history points per market")
    args = parser.parse_args()
    asyncio.run(main(args.markets, args.points_per_market))
