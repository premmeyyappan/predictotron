"""
Background metrics worker.

Two coroutines run concurrently inside the FastAPI lifespan:

_delta_loop  (every 60 s)
    Computes price-movement deltas for five time windows (1 / 5 / 15 / 60 / 1440 min)
    and writes them to the ``odds_deltas`` table.  After each write it publishes the
    latest price for every active contract to the Redis WebSocket channel so the
    real-time dashboard receives live updates even between ingestion runs.

_volatility_loop  (every 5 min)
    Fetches up to 30 days of price history in a single query, groups by contract,
    and computes annualised realised volatility (7-day and 30-day windows) plus the
    24 h high/low.  Results are written to ``volatility_metrics``.

Together these workers complete the analytics pipeline: the analytics API reads
``volatility_metrics`` and ``odds_deltas`` and now returns fully-populated data
instead of null fields.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from sqlalchemy import text

from app.cache.client import get_redis, publish
from app.config import settings
from app.database import AsyncSessionFactory

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Maths helpers
# ──────────────────────────────────────────────────────────────────────────────

_DELTA_WINDOWS = (1, 5, 15, 60, 1440)  # minutes


def _realized_vol(prices: list[float], periods_per_day: int = 1440) -> float | None:
    """
    Annualised realised volatility from a sequence of implied-probability prices.

    Uses log-returns on probability-clipped values so the result stays finite
    even near the 0/1 boundaries.  Annualisation assumes ``periods_per_day``
    samples per calendar day and 252 trading days per year.
    """
    if len(prices) < 2:
        return None
    arr = np.array(prices, dtype=float)
    arr = np.clip(arr, 1e-6, 1 - 1e-6)
    log_returns = np.diff(np.log(arr))
    if log_returns.std() == 0:
        return 0.0
    return float(np.std(log_returns, ddof=1) * np.sqrt(periods_per_day * 252))


# ──────────────────────────────────────────────────────────────────────────────
# Volatility loop
# ──────────────────────────────────────────────────────────────────────────────

async def _compute_volatility() -> int:
    """
    Fetch the last 30 days of price history across all active contracts in one
    query, compute rolling volatility windows per contract in Python/NumPy, and
    bulk-insert snapshots into ``volatility_metrics``.

    Returns the number of snapshots written.
    """
    now = datetime.now(tz=timezone.utc)
    since_30d = now - timedelta(days=30)
    since_7d = now - timedelta(days=7)
    since_24h = now - timedelta(hours=24)

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            text(
                """
                SELECT contract_id, timestamp, price
                FROM price_history
                WHERE timestamp >= :since
                ORDER BY contract_id, timestamp ASC
                """
            ),
            {"since": since_30d},
        )
        rows = result.fetchall()

    if not rows:
        return 0

    # Group rows by contract_id
    data: dict[uuid.UUID, list[tuple[datetime, float]]] = defaultdict(list)
    for contract_id, ts, price in rows:
        data[contract_id].append((ts, float(price)))

    snapshots: list[dict[str, Any]] = []
    for contract_id, points in data.items():
        timestamps = [t for t, _ in points]
        prices_30d = [p for _, p in points]
        prices_7d = [p for t, p in points if t >= since_7d]
        prices_24h = [p for t, p in points if t >= since_24h]

        snapshots.append(
            {
                "contract_id": contract_id,
                "timestamp": now,
                "realized_vol_7d": _realized_vol(prices_7d),
                "realized_vol_30d": _realized_vol(prices_30d),
                "high_24h": max(prices_24h) if prices_24h else None,
                "low_24h": min(prices_24h) if prices_24h else None,
            }
        )

    if not snapshots:
        return 0

    async with AsyncSessionFactory() as session:
        await session.execute(
            text(
                """
                INSERT INTO volatility_metrics
                    (contract_id, timestamp, realized_vol_7d, realized_vol_30d,
                     high_24h, low_24h)
                VALUES
                    (:contract_id, :timestamp, :realized_vol_7d, :realized_vol_30d,
                     :high_24h, :low_24h)
                """
            ),
            snapshots,
        )
        await session.commit()

    return len(snapshots)


async def _volatility_loop() -> None:
    """Run volatility snapshots every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        try:
            n = await _compute_volatility()
            if n:
                logger.info(f"volatility_worker: wrote {n} snapshots")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"volatility_worker error: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# Delta + price-broadcast loop
# ──────────────────────────────────────────────────────────────────────────────

async def _compute_deltas_for_window(
    session: Any,
    window_minutes: int,
    now: datetime,
) -> list[dict[str, Any]]:
    """
    Compute price deltas over ``window_minutes`` for every contract that has
    data at both endpoints of the window.

    Uses DISTINCT ON to pick the closest row to each endpoint without a
    correlated subquery, keeping execution fast on the composite index.
    """
    tolerance = timedelta(minutes=max(window_minutes // 4, 1))
    result = await session.execute(
        text(
            """
            WITH current_prices AS (
                SELECT DISTINCT ON (contract_id)
                    contract_id,
                    price AS current_price
                FROM price_history
                WHERE timestamp >= :recent_since
                ORDER BY contract_id, timestamp DESC
            ),
            past_prices AS (
                SELECT DISTINCT ON (contract_id)
                    contract_id,
                    price AS past_price
                FROM price_history
                WHERE timestamp BETWEEN :past_lo AND :past_hi
                ORDER BY contract_id, timestamp DESC
            )
            SELECT
                cp.contract_id,
                cp.current_price - pp.past_price  AS delta,
                ABS(cp.current_price - pp.past_price) AS abs_delta
            FROM current_prices cp
            JOIN past_prices pp ON pp.contract_id = cp.contract_id
            """
        ),
        {
            "recent_since": now - timedelta(minutes=5),
            "past_lo": now - timedelta(minutes=window_minutes) - tolerance,
            "past_hi": now - timedelta(minutes=window_minutes) + tolerance,
        },
    )
    return [
        {
            "contract_id": row[0],
            "timestamp": now,
            "delta": float(row[1]),
            "abs_delta": float(row[2]),
            "time_window_minutes": window_minutes,
        }
        for row in result.fetchall()
    ]


async def _get_latest_prices(session: Any, now: datetime) -> list[dict[str, Any]]:
    """Fetch the most recent price for every active contract."""
    result = await session.execute(
        text(
            """
            SELECT DISTINCT ON (contract_id)
                contract_id,
                price,
                timestamp
            FROM price_history
            WHERE timestamp >= :since
            ORDER BY contract_id, timestamp DESC
            """
        ),
        {"since": now - timedelta(minutes=10)},
    )
    return [
        {"contract_id": row[0], "price": float(row[1]), "timestamp": row[2]}
        for row in result.fetchall()
    ]


async def _compute_and_store_deltas() -> tuple[int, int]:
    """
    Compute deltas for all windows, persist them, and broadcast current prices
    to the Redis WebSocket channel.

    Returns (rows_written, prices_published).
    """
    now = datetime.now(tz=timezone.utc)
    all_deltas: list[dict[str, Any]] = []
    latest_prices: list[dict[str, Any]] = []

    async with AsyncSessionFactory() as session:
        for window in _DELTA_WINDOWS:
            deltas = await _compute_deltas_for_window(session, window, now)
            all_deltas.extend(deltas)

        latest_prices = await _get_latest_prices(session, now)

        if all_deltas:
            await session.execute(
                text(
                    """
                    INSERT INTO odds_deltas
                        (contract_id, timestamp, delta, abs_delta, time_window_minutes)
                    VALUES
                        (:contract_id, :timestamp, :delta, :abs_delta,
                         :time_window_minutes)
                    """
                ),
                all_deltas,
            )
            await session.commit()

    # Publish latest price per contract to the WebSocket broadcast channel
    published = 0
    for item in latest_prices:
        await publish(
            settings.ws_channel,
            {
                "type": "price_update",
                "contract_id": str(item["contract_id"]),
                "price": item["price"],
                "timestamp": item["timestamp"].isoformat(),
            },
        )
        published += 1

    return len(all_deltas), published


async def _delta_loop() -> None:
    """Run delta computation and price broadcasts every 60 seconds."""
    while True:
        await asyncio.sleep(60)
        try:
            rows, published = await _compute_and_store_deltas()
            if rows or published:
                logger.info(
                    f"delta_worker: wrote {rows} delta rows, "
                    f"published {published} price updates"
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"delta_worker error: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

async def start_metrics_worker() -> list[asyncio.Task]:
    """
    Launch the delta and volatility background tasks.

    Returns the task handles so the caller can cancel them on shutdown.
    Call this inside the FastAPI lifespan after the Redis pool is warm.
    """
    tasks = [
        asyncio.create_task(_delta_loop(), name="metrics_delta"),
        asyncio.create_task(_volatility_loop(), name="metrics_volatility"),
    ]
    logger.info("metrics_worker: started delta_loop and volatility_loop")
    return tasks
