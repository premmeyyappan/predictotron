#!/usr/bin/env python3
"""
Query performance benchmark: measures the latency impact of the composite
(contract_id, timestamp DESC) index on price_history.

Method
------
1. Pick a random sample of contract IDs from the live database.
2. For each query pattern, run two variants back-to-back:
   a. "No-index" simulation — SET enable_indexscan=off + enable_bitmapscan=off
      forces the planner onto a sequential scan, mirroring the pre-index state.
   b. Normal execution — index scans are allowed (the default).
3. Each variant runs EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) and the wall-clock
   Execution Time is extracted from the planner output.
4. Results are averaged across the sampled contracts and written to a Markdown
   report.

Usage
-----
    # Seed data first (if not already done):
    python -m scripts.seed_dev_data

    # Run the benchmark:
    python -m scripts.benchmark_queries
    python -m scripts.benchmark_queries --output benchmarks/query_performance.md
    python -m scripts.benchmark_queries --samples 20
"""

from __future__ import annotations

import argparse
import asyncio
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

DATABASE_URL = "postgresql://predictotron:password@localhost:5432/predictotron"

# ──────────────────────────────────────────────────────────────────────────────
# Query templates
# ──────────────────────────────────────────────────────────────────────────────

QUERIES: dict[str, str] = {
    "price_history_fetch": """
        SELECT id, timestamp, price, volume_24h, bid, ask
        FROM price_history
        WHERE contract_id = $1
        ORDER BY timestamp DESC
        LIMIT 200
    """,
    "active_market_listing": """
        SELECT m.id, m.title, m.category, m.source, m.resolution_date
        FROM markets m
        WHERE m.resolved = false
        ORDER BY m.resolution_date ASC
        LIMIT 500
    """,
    "biggest_movers": """
        SELECT contract_id, delta, abs_delta, time_window_minutes, timestamp
        FROM odds_deltas
        WHERE time_window_minutes = 60
        ORDER BY abs_delta DESC
        LIMIT 20
    """,
}

# Queries that accept a $1 contract_id parameter
PARAMETERISED = {"price_history_fetch"}


def _extract_execution_time(explain_output: str) -> float | None:
    """Parse 'Execution Time: X.XXX ms' from EXPLAIN ANALYZE text output."""
    m = re.search(r"Execution Time:\s+([\d.]+)\s+ms", explain_output)
    return float(m.group(1)) if m else None


async def _run_explain(
    conn: asyncpg.Connection,
    sql: str,
    args: list,
    disable_indexes: bool,
) -> float | None:
    """
    Run EXPLAIN (ANALYZE, BUFFERS) and return execution time in ms.
    Optionally disables index scans to simulate a pre-index sequential scan.
    """
    if disable_indexes:
        await conn.execute("SET enable_indexscan = off")
        await conn.execute("SET enable_bitmapscan = off")
        await conn.execute("SET enable_indexonlyscan = off")
    try:
        explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) {sql}"
        rows = await conn.fetch(explain_sql, *args)
        output = "\n".join(r[0] for r in rows)
        return _extract_execution_time(output)
    finally:
        if disable_indexes:
            await conn.execute("SET enable_indexscan = on")
            await conn.execute("SET enable_bitmapscan = on")
            await conn.execute("SET enable_indexonlyscan = on")


async def benchmark(conn: asyncpg.Connection, n_samples: int) -> dict:
    """
    Run all query benchmarks and return a results dict.
    """
    # Fetch a sample of contract IDs that have price history
    contract_ids = await conn.fetch(
        """
        SELECT DISTINCT contract_id
        FROM price_history
        ORDER BY random()
        LIMIT $1
        """,
        n_samples,
    )
    if not contract_ids:
        raise RuntimeError(
            "No price_history rows found. "
            "Run `python -m scripts.seed_dev_data` first."
        )
    sample_ids = [row["contract_id"] for row in contract_ids]

    # Row counts for context
    ph_count = await conn.fetchval("SELECT COUNT(*) FROM price_history")
    market_count = await conn.fetchval("SELECT COUNT(*) FROM markets")
    od_count = await conn.fetchval("SELECT COUNT(*) FROM odds_deltas")

    results: dict = {
        "dataset": {
            "price_history_rows": ph_count,
            "markets": market_count,
            "odds_delta_rows": od_count,
            "samples": len(sample_ids),
        },
        "queries": {},
    }

    for name, sql in QUERIES.items():
        no_idx_times: list[float] = []
        idx_times: list[float] = []

        iterations = sample_ids if name in PARAMETERISED else [None] * min(5, n_samples)

        for cid in iterations:
            args = [cid] if cid is not None else []

            # Warm up buffer cache with a silent run
            await conn.fetch(sql, *args)

            t_no_idx = await _run_explain(conn, sql, args, disable_indexes=True)
            t_idx = await _run_explain(conn, sql, args, disable_indexes=False)

            if t_no_idx is not None:
                no_idx_times.append(t_no_idx)
            if t_idx is not None:
                idx_times.append(t_idx)

        if no_idx_times and idx_times:
            avg_no_idx = statistics.mean(no_idx_times)
            avg_idx = statistics.mean(idx_times)
            improvement = (avg_no_idx - avg_idx) / avg_no_idx * 100
            results["queries"][name] = {
                "no_index_ms": round(avg_no_idx, 1),
                "with_index_ms": round(avg_idx, 1),
                "improvement_pct": round(improvement, 1),
                "samples": len(idx_times),
            }

    return results


def _render_markdown(results: dict, run_at: str) -> str:
    ds = results["dataset"]
    lines = [
        "# Query Performance Benchmark",
        "",
        f"Generated by `scripts/benchmark_queries.py` — {run_at}",
        "",
        "## Dataset",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| `price_history` rows | {ds['price_history_rows']:,} |",
        f"| Markets | {ds['markets']:,} |",
        f"| `odds_deltas` rows | {ds['odds_delta_rows']:,} |",
        f"| Contract samples benchmarked | {ds['samples']} |",
        "",
        "## Summary",
        "",
        "| Query | Without index (seq scan) | With index | Improvement |",
        "|-------|--------------------------|------------|-------------|",
    ]

    for name, r in results["queries"].items():
        lines.append(
            f"| `{name}` | {r['no_index_ms']} ms | {r['with_index_ms']} ms "
            f"| **{r['improvement_pct']}%** |"
        )

    lines += [
        "",
        "## Notes",
        "",
        "- *Without index*: `SET enable_indexscan=off / enable_bitmapscan=off` "
        "forces the planner onto a sequential scan, replicating the pre-index execution plan.",
        "- *With index*: default planner settings; the composite "
        "`(contract_id, timestamp DESC)` index on `price_history` is available.",
        "- Timings are from `EXPLAIN (ANALYZE, BUFFERS)` Execution Time after "
        "one warm-up pass to load the relevant pages into the buffer cache.",
        "- Run `python -m scripts.benchmark_queries` to regenerate.",
    ]

    return "\n".join(lines) + "\n"


async def main(n_samples: int, output_path: str | None) -> None:
    print(f"Connecting to {DATABASE_URL}...")
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        print(f"Running benchmarks ({n_samples} contract samples)...\n")
        results = await benchmark(conn, n_samples)

        run_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        md = _render_markdown(results, run_at)

        print(md)

        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text(md)
            print(f"Written to {output_path}")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Benchmark price_history query latency with/without indexes"
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=10,
        help="Number of contract IDs to sample per parameterised query (default: 10)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write Markdown report to this path (e.g. benchmarks/query_performance.md)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.samples, args.output))
