"""Abstract base class for prediction market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class RawMarket:
    external_id: str
    title: str
    category: str | None
    source: str
    description: str | None
    resolution_date: datetime | None


@dataclass
class RawContract:
    external_id: str
    market_external_id: str
    name: str
    side: str  # 'yes' | 'no'


@dataclass
class RawPricePoint:
    contract_external_id: str
    timestamp: datetime
    price: float
    volume_24h: float | None = None
    open_interest: float | None = None
    bid: float | None = None
    ask: float | None = None


class BaseMarketSource(ABC):
    """
    Defines the interface all market data sources must implement.

    Sources are responsible for fetching raw data from external APIs or
    local files and returning normalised dataclass instances. The ingestion
    pipeline handles persistence and deduplication.
    """

    @property
    @abstractmethod
    def source_name(self) -> str: ...

    @abstractmethod
    async def fetch_markets(self, limit: int = 1000) -> list[RawMarket]:
        """Return a batch of markets from this source."""
        ...

    @abstractmethod
    async def fetch_price_history(
        self,
        market_external_id: str,
        start: datetime,
        end: datetime,
    ) -> list[RawPricePoint]:
        """Return chronologically sorted price history for a contract."""
        ...
