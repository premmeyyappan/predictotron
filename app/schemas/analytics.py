from pydantic import BaseModel


class ImpliedProbabilitySchema(BaseModel):
    raw: float
    overround_adjusted: float
    overround: float


class MomentumSchema(BaseModel):
    ema_12: float | None
    ema_26: float | None
    macd: float | None
    rsi_14: float | None
    rate_of_change_5: float | None
    trend: str  # 'bullish' | 'bearish' | 'neutral'


class KellySchema(BaseModel):
    full_kelly: float
    half_kelly: float
    quarter_kelly: float
    edge: float
    recommended_fraction: float


class ContractAnalyticsSchema(BaseModel):
    contract_id: str
    current_price: float
    implied_probability: ImpliedProbabilitySchema
    momentum: MomentumSchema
    kelly: KellySchema
    realized_vol_7d: float | None
    realized_vol_30d: float | None
    high_24h: float | None
    low_24h: float | None
