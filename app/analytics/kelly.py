"""
Kelly Criterion sizing for prediction market positions.

The Kelly Criterion determines the optimal fraction of bankroll to wager
to maximise the logarithm of expected wealth. For binary markets:

    f* = (b*p - q) / b

where:
    p = probability of winning (your estimate)
    q = 1 - p (probability of losing)
    b = net odds received on the bet (decimal odds - 1)

Because Kelly can be highly sensitive to probability estimation errors,
we also return fractional variants (half-Kelly, quarter-Kelly) which are
standard in practice for reducing variance.
"""

from __future__ import annotations

from app.schemas.analytics import KellySchema


def kelly_criterion(
    win_probability: float,
    market_price: float,
    *,
    min_edge: float = 0.01,
) -> KellySchema:
    """
    Compute Kelly sizing given a probability estimate and current market price.

    Args:
        win_probability: Your estimated probability of the outcome resolving YES.
        market_price:    Current market price (implied probability), used to
                         derive the effective decimal odds (1 / market_price).
        min_edge:        Minimum edge threshold below which no position is
                         recommended (default 1%).

    Returns:
        KellySchema with full, half, and quarter Kelly fractions plus edge.
    """
    if not (0 < win_probability < 1):
        raise ValueError(f"win_probability must be in (0, 1), got {win_probability}")
    if not (0 < market_price < 1):
        raise ValueError(f"market_price must be in (0, 1), got {market_price}")

    # Decimal odds implied by the market price
    decimal_odds = 1.0 / market_price
    # Net odds (profit per unit staked if you win)
    b = decimal_odds - 1.0

    p = win_probability
    q = 1.0 - p

    edge = p - market_price  # positive -> you have an edge over the market

    if edge < min_edge:
        return KellySchema(
            full_kelly=0.0,
            half_kelly=0.0,
            quarter_kelly=0.0,
            edge=round(edge, 6),
            recommended_fraction=0.0,
        )

    full_kelly = (b * p - q) / b
    # Clamp to [0, 1] — negative Kelly means bet the other side (or abstain)
    full_kelly = max(0.0, min(1.0, full_kelly))

    return KellySchema(
        full_kelly=round(full_kelly, 6),
        half_kelly=round(full_kelly * 0.5, 6),
        quarter_kelly=round(full_kelly * 0.25, 6),
        edge=round(edge, 6),
        recommended_fraction=round(full_kelly * 0.25, 6),  # quarter-Kelly default
    )
