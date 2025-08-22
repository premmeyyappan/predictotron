from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.market import Market
from app.models.price_history import PriceHistory
from app.models.contract import Contract
from app.schemas.market import MarketListSchema, MarketSchema, PricePointSchema

router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("", response_model=list[MarketListSchema])
async def list_markets(
    category: str | None = Query(None),
    resolved: bool | None = Query(None),
    source: str | None = Query(None),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[Market]:
    stmt = select(Market).order_by(Market.created_at.desc()).limit(limit).offset(offset)
    if category is not None:
        stmt = stmt.where(Market.category == category)
    if resolved is not None:
        stmt = stmt.where(Market.resolved == resolved)
    if source is not None:
        stmt = stmt.where(Market.source == source)

    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/stats")
async def market_stats(db: AsyncSession = Depends(get_db)) -> dict:
    total = await db.scalar(select(func.count()).select_from(Market))
    resolved = await db.scalar(select(func.count()).select_from(Market).where(Market.resolved == True))  # noqa: E712
    price_rows = await db.scalar(select(func.count()).select_from(PriceHistory))
    return {
        "total_markets": total,
        "resolved_markets": resolved,
        "active_markets": (total or 0) - (resolved or 0),
        "total_price_history_rows": price_rows,
    }


@router.get("/{market_id}", response_model=MarketSchema)
async def get_market(market_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Market:
    result = await db.execute(
        select(Market).options(selectinload(Market.contracts)).where(Market.id == market_id)
    )
    market = result.scalar_one_or_none()
    if market is None:
        raise HTTPException(status_code=404, detail="Market not found")
    return market


@router.get("/{market_id}/price-history", response_model=list[PricePointSchema])
async def get_price_history(
    market_id: uuid.UUID,
    limit: int = Query(500, le=5000),
    db: AsyncSession = Depends(get_db),
) -> list[PriceHistory]:
    # Verify market exists
    market = await db.scalar(select(Market).where(Market.id == market_id))
    if market is None:
        raise HTTPException(status_code=404, detail="Market not found")

    # Fetch the YES contract's price history
    stmt = (
        select(PriceHistory)
        .join(Contract, PriceHistory.contract_id == Contract.id)
        .where(Contract.market_id == market_id, Contract.side == "yes")
        .order_by(PriceHistory.timestamp.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    return list(reversed(rows))
