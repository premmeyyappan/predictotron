import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ContractSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    market_id: uuid.UUID
    name: str
    side: str
    created_at: datetime


class MarketSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    external_id: str
    title: str
    category: str | None
    source: str
    description: str | None
    resolution_date: datetime | None
    resolved: bool
    resolution_value: float | None
    created_at: datetime
    updated_at: datetime
    contracts: list[ContractSchema] = []


class MarketListSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    external_id: str
    title: str
    category: str | None
    source: str
    resolution_date: datetime | None
    resolved: bool
    created_at: datetime


class PricePointSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    price: float
    volume_24h: float | None
    bid: float | None
    ask: float | None
