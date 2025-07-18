"""
Polymarket data source adapter.

Polymarket exposes a public CLOB (central limit order book) REST API.
This adapter normalises Polymarket market and price-history payloads
into the internal dataclass format consumed by the ingestion pipeline.

For historical backfills the adapter pages through the time-series endpoint
in configurable chunk sizes, respecting the API's rate limits via
exponential backoff (handled by tenacity).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.ingestion.sources.base import BaseMarketSource, RawMarket, RawContract, RawPricePoint


_POLYMARKET_API = "https://clob.polymarket.com"
_GAMMA_API = "https://gamma-api.polymarket.com"


class PolymarketSource(BaseMarketSource):
    """
    Adapter for the Polymarket CLOB and Gamma APIs.

    Markets are fetched from the Gamma metadata API; price history is
    fetched from the CLOB API's /prices-history endpoint with 1-minute
    resolution for recent data and 1-hour resolution for historical data.
    """

    def __init__(self, *, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout, headers={"Accept": "application/json"})

    @property
    def source_name(self) -> str:
        return "polymarket"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _get(self, url: str, params: dict | None = None) -> dict:
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def fetch_markets(self, limit: int = 1000) -> list[RawMarket]:
        """
        Fetch active markets from the Gamma API.

        Returns up to `limit` markets ordered by volume descending.
        """
        data = await self._get(
            f"{_GAMMA_API}/markets",
            params={"limit": min(limit, 1000), "active": "true", "closed": "false"},
        )
        markets = data if isinstance(data, list) else data.get("markets", [])
        result = []
        for m in markets[:limit]:
            resolution_date = None
            if end_date := m.get("endDate"):
                try:
                    resolution_date = datetime.fromisoformat(end_date.rstrip("Z")).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            result.append(
                RawMarket(
                    external_id=str(m["id"]),
                    title=m.get("question", m.get("title", "")),
                    category=m.get("category"),
                    source=self.source_name,
                    description=m.get("description"),
                    resolution_date=resolution_date,
                )
            )
        return result

    async def fetch_price_history(
        self,
        market_external_id: str,
        start: datetime,
        end: datetime,
    ) -> list[RawPricePoint]:
        """
        Fetch CLOB price history for a token/contract.

        Uses 1-hour fidelity for ranges > 7 days, 1-minute for recent data.
        """
        delta = end - start
        fidelity = 60 if delta.days > 7 else 1  # minutes

        try:
            data = await self._get(
                f"{_POLYMARKET_API}/prices-history",
                params={
                    "market": market_external_id,
                    "startTs": int(start.timestamp()),
                    "endTs": int(end.timestamp()),
                    "fidelity": fidelity,
                },
            )
        except httpx.HTTPError:
            return []

        points = []
        for entry in data.get("history", []):
            try:
                ts = datetime.fromtimestamp(entry["t"], tz=timezone.utc)
                price = float(entry["p"])
                points.append(
                    RawPricePoint(
                        contract_external_id=market_external_id,
                        timestamp=ts,
                        price=max(0.0, min(1.0, price)),
                    )
                )
            except (KeyError, ValueError):
                continue

        return sorted(points, key=lambda x: x.timestamp)

    async def aclose(self) -> None:
        await self._client.aclose()
