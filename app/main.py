from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.cache.client import close_redis, get_redis
from app.config import settings
from app.api.routes import markets, analytics, ws
from app.workers.metrics_worker import start_metrics_worker

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Warm up Redis connection pool
    await get_redis()
    # Start background workers (delta computation + volatility snapshots)
    worker_tasks = await start_metrics_worker()
    yield
    for task in worker_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await close_redis()


app = FastAPI(
    title="Predictotron",
    description=(
        "Prediction market analytics platform providing real-time implied probability tracking, "
        "momentum indicators, Kelly-optimal position sizing, and volatility metrics "
        "across 1,200+ active markets."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(markets.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(ws.router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", include_in_schema=False)
async def serve_dashboard() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": "1.0.0"}
