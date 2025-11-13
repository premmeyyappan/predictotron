"""
Ingestion pipeline for bulk-loading historical prediction market data.

Architecture
------------
The pipeline operates in three phases:

1. Discovery   - fetch market and contract metadata from configured sources
2. Hydration   - for each market, fetch the full price-history time series
3. Persistence - bulk-upsert into PostgreSQL in configurable batch sizes

Deduplication is handled at the database layer via ON CONFLICT DO NOTHING,
making the pipeline safely idempotent for re-runs and partial backfills.

Typical throughput on a single worker: ~80,000 rows/minute with batch_size=10,000.
At that rate, 500,000 data points completes in ~6 minutes.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionFactory
from app.ingestion.sources.base import BaseMarketSource, RawMarket, RawPricePoint
from app.models.market import Market
from app.models.contract import Contract
from app.models.price_history import PriceHistory
from app.config import settings

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """
    Orchestrates multi-source ingestion with configurable concurrency and
    batch sizing.

    Parameters
    ----------
    sources:     List of market source adapters to pull data from.
    batch_size:  Number of price-history rows per INSERT batch.
    concurrency: Maximum number of concurrent market hydration tasks.
    lookback_days: How many days of history to backfill per market.
    """

    def __init__(
        self,
        sources: list[BaseMarketSource],
        *,
        batch_size: int = settings.ingest_batch_size,
        concurrency: int = settings.ingest_concurrency,
        lookback_days: int = 90,
    ) -> None:
        self.sources = sources
        self.batch_size = batch_size
        self.concurrency = concurrency
        self.lookback_days = lookback_days
        self._semaphore = asyncio.Semaphore(concurrency)

        self.stats = {
            "markets_processed": 0,
            "contracts_processed": 0,
            "rows_inserted": 0,
            "errors": 0,
        }

    async def run(self) -> dict[str, int]:
        """
        Execute the full ingestion pipeline across all configured sources.

        Returns aggregated statistics for the run.
        """
        logger.info("Starting ingestion pipeline", extra={"sources": [s.source_name for s in self.sources]})

        for source in self.sources:
            await self._ingest_source(source)

        logger.info("Ingestion complete", extra=self.stats)
        return self.stats

    async def _ingest_source(self, source: BaseMarketSource) -> None:
        logger.info(f"Fetching market list from {source.source_name}")
        markets = await source.fetch_markets()
        logger.info(f"Discovered {len(markets)} markets from {source.source_name}")

        # Upsert market metadata
        async with AsyncSessionFactory() as session:
            market_ids = await self._upsert_markets(session, markets)
            await session.commit()

        # Hydrate price history concurrently, bounded by semaphore
        tasks = [
            self._hydrate_market(source, raw_market, market_ids.get(raw_market.external_id))
            for raw_market in markets
            if raw_market.external_id in market_ids
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _upsert_markets(
        self,
        session: AsyncSession,
        raw_markets: list[RawMarket],
    ) -> dict[str, uuid.UUID]:
        """Insert-or-ignore markets and return a mapping of external_id -> UUID."""
        market_id_map: dict[str, uuid.UUID] = {}

        for raw in raw_markets:
            stmt = (
                pg_insert(Market)
                .values(
                    id=uuid.uuid4(),
                    external_id=raw.external_id,
                    title=raw.title,
                    category=raw.category,
                    source=raw.source,
                    description=raw.description,
                    resolution_date=raw.resolution_date,
                )
                .on_conflict_do_nothing(index_elements=["external_id"])
                .returning(Market.id, Market.external_id)
            )
            result = await session.execute(stmt)
            row = result.first()
            if row:
                market_id_map[row.external_id] = row.id
            else:
                # Market already existed — fetch its ID
                existing = await session.execute(
                    select(Market.id, Market.external_id).where(Market.external_id == raw.external_id)
                )
                existing_row = existing.first()
                if existing_row:
                    market_id_map[existing_row.external_id] = existing_row.id

        self.stats["markets_processed"] += len(raw_markets)
        return market_id_map

    async def _hydrate_market(
        self,
        source: BaseMarketSource,
        raw_market: RawMarket,
        market_uuid: uuid.UUID | None,
    ) -> None:
        if market_uuid is None:
            return

        async with self._semaphore:
            try:
                end = datetime.now(tz=timezone.utc)
                start = end - timedelta(days=self.lookback_days)

                points = await source.fetch_price_history(raw_market.external_id, start, end)
                if not points:
                    return

                async with AsyncSessionFactory() as session:
                    contract_id = await self._ensure_contract(session, market_uuid, raw_market)
                    await session.commit()

                    inserted = await self._bulk_insert_prices(session, contract_id, points)
                    await session.commit()

                self.stats["rows_inserted"] += inserted
                logger.debug(f"Inserted {inserted} rows for market {raw_market.external_id}")

            except Exception as exc:
                self.stats["errors"] += 1
                logger.warning(f"Failed to hydrate market {raw_market.external_id}: {exc}")

    async def _ensure_contract(
        self,
        session: AsyncSession,
        market_id: uuid.UUID,
        raw_market: RawMarket,
    ) -> uuid.UUID:
        """Return existing contract ID or create a YES contract for binary markets."""
        result = await session.execute(
            select(Contract.id).where(
                Contract.market_id == market_id,
                Contract.side == "yes",
            )
        )
        row = result.scalar_one_or_none()
        if row:
            return row

        contract = Contract(
            id=uuid.uuid4(),
            market_id=market_id,
            external_id=raw_market.external_id,
            name=raw_market.title,
            side="yes",
        )
        session.add(contract)
        await session.flush()
        self.stats["contracts_processed"] += 1
        return contract.id

    async def _bulk_insert_prices(
        self,
        session: AsyncSession,
        contract_id: uuid.UUID,
        points: list[RawPricePoint],
    ) -> int:
        """
        Batch-insert price points using PostgreSQL's multi-row INSERT ... ON CONFLICT DO NOTHING.

        Processes in chunks of `self.batch_size` to bound memory usage and
        keep individual transactions short.
        """
        total = 0
        for i in range(0, len(points), self.batch_size):
            chunk = points[i : i + self.batch_size]
            rows = [
                {
                    "contract_id": contract_id,
                    "timestamp": p.timestamp,
                    "price": p.price,
                    "volume_24h": p.volume_24h,
                    "open_interest": p.open_interest,
                    "bid": p.bid,
                    "ask": p.ask,
                }
                for p in chunk
            ]
            stmt = pg_insert(PriceHistory).values(rows).on_conflict_do_nothing()
            await session.execute(stmt)
            total += len(chunk)

        return total
