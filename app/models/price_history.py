import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PriceHistory(Base):
    """
    Core time-series table tracking contract price (implied probability) over time.
    Composite primary key on (contract_id, timestamp) enables efficient range scans.
    """

    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)  # 0.0 – 1.0 implied probability
    volume_24h: Mapped[float | None] = mapped_column(Numeric(20, 2))
    open_interest: Mapped[float | None] = mapped_column(Numeric(20, 2))
    bid: Mapped[float | None] = mapped_column(Numeric(10, 6))
    ask: Mapped[float | None] = mapped_column(Numeric(10, 6))

    contract: Mapped["Contract"] = relationship(back_populates="price_history")


class VolatilityMetric(Base):
    """Precomputed rolling volatility windows for efficient dashboard queries."""

    __tablename__ = "volatility_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    realized_vol_7d: Mapped[float | None] = mapped_column(Numeric(10, 6))
    realized_vol_30d: Mapped[float | None] = mapped_column(Numeric(10, 6))
    high_24h: Mapped[float | None] = mapped_column(Numeric(10, 6))
    low_24h: Mapped[float | None] = mapped_column(Numeric(10, 6))

    contract: Mapped["Contract"] = relationship(back_populates="volatility_metrics")


class OddsDelta(Base):
    """
    Stores probability movement deltas across fixed time windows.
    Used for momentum calculations and alerting on significant moves.
    """

    __tablename__ = "odds_deltas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delta: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    abs_delta: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    time_window_minutes: Mapped[int] = mapped_column(Integer, nullable=False)  # 1, 5, 15, 60, 1440

    contract: Mapped["Contract"] = relationship(back_populates="odds_deltas")
