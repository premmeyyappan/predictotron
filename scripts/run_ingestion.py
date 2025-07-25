#!/usr/bin/env python3
"""
CLI entry point for running the live ingestion pipeline.

Fetches current market data from configured sources and persists it to
the database. Designed to be run as a scheduled job (e.g. cron or
Kubernetes CronJob) at whatever cadence fresh data is needed.

Usage:
    python -m scripts.run_ingestion
    python -m scripts.run_ingestion --source polymarket --lookback-days 30
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.sources.polymarket import PolymarketSource

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


async def main(source: str, lookback_days: int) -> None:
    sources = []
    if source in ("polymarket", "all"):
        sources.append(PolymarketSource())

    if not sources:
        logger.error(f"Unknown source: {source}")
        return

    pipeline = IngestionPipeline(sources, lookback_days=lookback_days)

    logger.info(f"Starting ingestion: source={source}, lookback_days={lookback_days}")
    stats = await pipeline.run()

    logger.info(
        "Ingestion finished — "
        f"markets={stats['markets_processed']}, "
        f"contracts={stats['contracts_processed']}, "
        f"rows={stats['rows_inserted']}, "
        f"errors={stats['errors']}"
    )

    for src in sources:
        if hasattr(src, "aclose"):
            await src.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Predictotron ingestion pipeline")
    parser.add_argument(
        "--source",
        default="polymarket",
        choices=["polymarket", "all"],
        help="Data source to ingest from (default: polymarket)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=90,
        help="Days of price history to backfill per market (default: 90)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.source, args.lookback_days))
