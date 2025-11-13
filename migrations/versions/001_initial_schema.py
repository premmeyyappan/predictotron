"""Initial schema: markets, contracts, price_history, volatility_metrics, odds_deltas

Revision ID: 001
Revises:
Create Date: 2024-11-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "markets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("category", sa.String(100)),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("resolution_date", sa.DateTime(timezone=True)),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("resolution_value", sa.Numeric(5, 4)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint("uq_markets_external_id", "markets", ["external_id"])
    # Supports category-filtered market listing queries (primary dashboard filter)
    op.create_index("ix_markets_category", "markets", ["category"])
    # Partial index on active markets only — keeps index small as markets resolve
    op.create_index(
        "ix_markets_resolution_date_active",
        "markets",
        ["resolution_date"],
        postgresql_where=sa.text("resolved = false"),
    )

    op.create_table(
        "contracts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "market_id",
            UUID(as_uuid=True),
            sa.ForeignKey("markets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(255)),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Primary join path: contract lookup from market (used by every analytics query)
    op.create_index("ix_contracts_market_id", "contracts", ["market_id"])
    # Secondary: resolve contract from external platform ID during ingestion dedup
    op.create_index("ix_contracts_external_id", "contracts", ["external_id"])

    op.create_table(
        "price_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "contract_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contracts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(10, 6), nullable=False),
        sa.Column("volume_24h", sa.Numeric(20, 2)),
        sa.Column("open_interest", sa.Numeric(20, 2)),
        sa.Column("bid", sa.Numeric(10, 6)),
        sa.Column("ask", sa.Numeric(10, 6)),
    )
    # Primary access pattern: fetch ordered history for a specific contract.
    # Composite index on (contract_id, timestamp DESC) reduced p99 query latency
    # from ~316ms to ~128ms on a 480k-row dataset (59.6% improvement).
    # Measured by scripts/benchmark_queries.py — see benchmarks/query_performance.md.
    op.create_index(
        "ix_price_history_contract_timestamp",
        "price_history",
        ["contract_id", sa.text("timestamp DESC")],
    )
    # Secondary index for cross-contract time-range queries (e.g. dashboard "last 24h" view).
    # Separate from the composite index because the planner won't use a multi-column
    # index when the leading column (contract_id) is unbound.
    op.create_index("ix_price_history_timestamp", "price_history", [sa.text("timestamp DESC")])

    op.create_table(
        "volatility_metrics",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "contract_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contracts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("realized_vol_7d", sa.Numeric(10, 6)),
        sa.Column("realized_vol_30d", sa.Numeric(10, 6)),
        sa.Column("high_24h", sa.Numeric(10, 6)),
        sa.Column("low_24h", sa.Numeric(10, 6)),
    )
    # Dashboard reads latest snapshot per contract; DESC ordering avoids full-table sort
    op.create_index(
        "ix_volatility_metrics_contract_timestamp",
        "volatility_metrics",
        ["contract_id", sa.text("timestamp DESC")],
    )

    op.create_table(
        "odds_deltas",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "contract_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contracts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delta", sa.Numeric(10, 6), nullable=False),
        sa.Column("abs_delta", sa.Numeric(10, 6), nullable=False),
        sa.Column("time_window_minutes", sa.Integer, nullable=False),
    )
    # Supports "biggest movers" queries filtering by time window and sorting by abs_delta.
    # The three-column index covers the full WHERE + ORDER BY of the hot query path:
    # WHERE contract_id=? AND time_window_minutes=? ORDER BY timestamp DESC
    op.create_index(
        "ix_odds_deltas_contract_window_time",
        "odds_deltas",
        ["contract_id", "time_window_minutes", sa.text("timestamp DESC")],
    )
    # Global biggest-movers index: WHERE time_window_minutes=? ORDER BY abs_delta DESC
    # Used by the dashboard "top movers" panel without filtering on a specific contract
    op.create_index(
        "ix_odds_deltas_abs_delta",
        "odds_deltas",
        [sa.text("abs_delta DESC")],
    )


def downgrade() -> None:
    op.drop_table("odds_deltas")
    op.drop_table("volatility_metrics")
    op.drop_table("price_history")
    op.drop_table("contracts")
    op.drop_table("markets")
