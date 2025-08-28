from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.kelly import kelly_criterion
from app.analytics.momentum import compute_momentum
from app.analytics.probability import implied_probability, overround, adjust_for_overround
from app.database import get_db
from app.models.contract import Contract
from app.models.market import Market
from app.models.price_history import PriceHistory, VolatilityMetric
from app.schemas.analytics import ContractAnalyticsSchema, ImpliedProbabilitySchema

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/markets/{market_id}", response_model=ContractAnalyticsSchema)
async def market_analytics(
    market_id: uuid.UUID,
    win_probability: float = Query(
        ...,
        gt=0,
        lt=1,
        description="Your estimated probability the market resolves YES",
    ),
    db: AsyncSession = Depends(get_db),
) -> ContractAnalyticsSchema:
    """
    Full analytics snapshot for a market's YES contract.

    Combines the current market price with a caller-supplied probability
    estimate to compute Kelly sizing, momentum indicators, and volatility.
    """
    # Fetch the YES contract
    result = await db.execute(
        select(Contract)
        .join(Market, Contract.market_id == Market.id)
        .where(Market.id == market_id, Contract.side == "yes")
    )
    contract = result.scalar_one_or_none()
    if contract is None:
        raise HTTPException(status_code=404, detail="Market or contract not found")

    # Last N price points for momentum
    price_rows = await db.execute(
        select(PriceHistory)
        .where(PriceHistory.contract_id == contract.id)
        .order_by(PriceHistory.timestamp.desc())
        .limit(200)
    )
    prices = [float(row.price) for row in reversed(list(price_rows.scalars().all()))]

    if not prices:
        raise HTTPException(status_code=422, detail="No price history available for this market")

    current_price = prices[-1]

    # Latest volatility snapshot
    vol_row = await db.scalar(
        select(VolatilityMetric)
        .where(VolatilityMetric.contract_id == contract.id)
        .order_by(VolatilityMetric.timestamp.desc())
    )

    # All same-market prices for overround (binary: YES + implied NO)
    all_prices = [current_price, 1.0 - current_price]
    over = overround(all_prices)

    return ContractAnalyticsSchema(
        contract_id=str(contract.id),
        current_price=round(current_price, 6),
        implied_probability=ImpliedProbabilitySchema(
            raw=implied_probability(current_price),
            overround_adjusted=adjust_for_overround(all_prices)[0],
            overround=round(over, 6),
        ),
        momentum=compute_momentum(prices),
        kelly=kelly_criterion(win_probability, current_price),
        realized_vol_7d=float(vol_row.realized_vol_7d) if vol_row and vol_row.realized_vol_7d else None,
        realized_vol_30d=float(vol_row.realized_vol_30d) if vol_row and vol_row.realized_vol_30d else None,
        high_24h=float(vol_row.high_24h) if vol_row and vol_row.high_24h else None,
        low_24h=float(vol_row.low_24h) if vol_row and vol_row.low_24h else None,
    )
