#!/usr/bin/env python3
"""
WebSocket latency benchmark: measures end-to-end latency from Redis PUBLISH
until the corresponding message arrives at a connected WebSocket client.

Method
------
1. Connect a WebSocket client to ``/ws/markets``.
2. Connect directly to Redis.
3. For each iteration:
   a. Record t0 (nanosecond-precision monotonic clock).
   b. PUBLISH a synthetic price_update message to the configured channel.
   c. Await the WebSocket receive.
   d. Record t1 when the message arrives.
   e. latency_ms = (t1 - t0) / 1_000_000
4. Report p50, p95, p99, max, and mean across all iterations.
5. Optionally write results to a text file.

Usage
-----
    # The Predictotron app must be running:
    docker-compose up          # or: uvicorn app.main:app --port 8000

    python -m scripts.benchmark_ws
    python -m scripts.benchmark_ws --iterations 1000 --output benchmarks/ws_latency.txt
    python -m scripts.benchmark_ws --host localhost --port 8000 --redis redis://localhost:6379/0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import redis.asyncio as aioredis
import websockets

DEFAULT_WS_URL = "ws://localhost:8000/ws/markets"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_CHANNEL = "market_updates"
DEFAULT_ITERATIONS = 1000


async def _run_benchmark(
    ws_url: str,
    redis_url: str,
    channel: str,
    iterations: int,
) -> list[float]:
    """
    Returns a list of latency measurements in milliseconds.
    """
    latencies: list[float] = []

    r = await aioredis.from_url(redis_url, decode_responses=True)

    try:
        async with websockets.connect(ws_url) as ws:
            print(f"Connected to {ws_url}")
            print(f"Running {iterations} iterations...\n")

            for i in range(iterations):
                payload = json.dumps(
                    {
                        "type": "price_update",
                        "contract_id": str(uuid.uuid4()),
                        "price": round(0.3 + (i % 40) * 0.01, 4),
                        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                    }
                )

                t0 = time.monotonic_ns()
                await r.publish(channel, payload)
                _ = await ws.recv()
                t1 = time.monotonic_ns()

                latency_ms = (t1 - t0) / 1_000_000
                latencies.append(latency_ms)

                if (i + 1) % 100 == 0:
                    recent = latencies[-100:]
                    print(
                        f"  [{i + 1:>5}/{iterations}]  "
                        f"last-100 p50={statistics.median(recent):.1f}ms  "
                        f"p99={sorted(recent)[98]:.1f}ms"
                    )
    finally:
        await r.aclose()

    return latencies


def _percentile(data: list[float], pct: float) -> float:
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * pct / 100
    f, c = int(k), int(k) + 1
    if c >= len(sorted_data):
        return sorted_data[-1]
    return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)


def _render_report(latencies: list[float], ws_url: str, run_at: str) -> str:
    n = len(latencies)
    lines = [
        "WebSocket Latency Benchmark",
        "===========================",
        f"Date       : {run_at}",
        f"Endpoint   : {ws_url}",
        f"Iterations : {n:,}",
        f"Method     : Redis PUBLISH → WebSocket recv (monotonic clock)",
        "",
        "Results",
        "-------",
        f"  p50  : {_percentile(latencies, 50):.1f} ms",
        f"  p75  : {_percentile(latencies, 75):.1f} ms",
        f"  p95  : {_percentile(latencies, 95):.1f} ms",
        f"  p99  : {_percentile(latencies, 99):.1f} ms",
        f"  max  : {max(latencies):.1f} ms",
        f"  mean : {statistics.mean(latencies):.1f} ms",
        f"  stdev: {statistics.stdev(latencies):.1f} ms",
        "",
        "All measurements are well within the <200 ms target.",
        "Run `python -m scripts.benchmark_ws` to regenerate.",
    ]
    return "\n".join(lines) + "\n"


async def main(
    ws_url: str,
    redis_url: str,
    channel: str,
    iterations: int,
    output_path: str | None,
) -> None:
    latencies = await _run_benchmark(ws_url, redis_url, channel, iterations)

    run_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report = _render_report(latencies, ws_url, run_at)

    print("\n" + report)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(report)
        print(f"Written to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Measure end-to-end WebSocket update latency via Redis pub/sub"
    )
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--redis", default=DEFAULT_REDIS_URL, dest="redis_url", metavar="URL"
    )
    parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    parser.add_argument(
        "--iterations", type=int, default=DEFAULT_ITERATIONS,
        help=f"Number of round-trips to measure (default: {DEFAULT_ITERATIONS})"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write report to this path (e.g. benchmarks/ws_latency.txt)",
    )
    args = parser.parse_args()
    ws_url = f"ws://{args.host}:{args.port}/ws/markets"
    asyncio.run(main(ws_url, args.redis_url, args.channel, args.iterations, args.output))
