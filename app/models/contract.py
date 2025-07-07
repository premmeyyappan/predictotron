import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("markets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # 'yes' | 'no'
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    market: Mapped["Market"] = relationship(back_populates="contracts")
    price_history: Mapped[list["PriceHistory"]] = relationship(back_populates="contract", cascade="all, delete-orphan")
    volatility_metrics: Mapped[list["VolatilityMetric"]] = relationship(back_populates="contract", cascade="all, delete-orphan")
    odds_deltas: Mapped[list["OddsDelta"]] = relationship(back_populates="contract", cascade="all, delete-orphan")
