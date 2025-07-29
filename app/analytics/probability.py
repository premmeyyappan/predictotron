"""
Implied probability calculations for prediction market contracts.

Prediction markets price contracts as probabilities [0, 1]. When aggregating
across all outcomes in a market, the sum typically exceeds 1.0 due to the
platform's take rate (the "overround" or "vig"). These utilities compute
fair-value probabilities adjusted for that overround.
"""

from __future__ import annotations


def implied_probability(price: float) -> float:
    """
    Convert a raw market price to an implied probability.

    In binary prediction markets the price IS the implied probability,
    but we clamp to [0.001, 0.999] to avoid degenerate Kelly fractions.
    """
    return max(0.001, min(0.999, price))


def overround(prices: list[float]) -> float:
    """
    Compute the market overround (vig) as the excess over fair-value 1.0.

    A perfectly efficient market sums to exactly 1.0; any excess represents
    the platform's take. E.g. [0.55, 0.48] -> overround = 0.03 (3%).
    """
    total = sum(prices)
    return max(0.0, total - 1.0)


def adjust_for_overround(prices: list[float]) -> list[float]:
    """
    Normalise a set of raw prices to sum to 1.0, removing the overround.

    Uses the standard additive adjustment: p_adj_i = p_i / sum(p).
    """
    total = sum(prices)
    if total <= 0:
        raise ValueError("Price sum must be positive")
    return [p / total for p in prices]


def implied_probability_from_american(odds: int) -> float:
    """
    Derive implied probability from American (moneyline) odds.

    Positive odds (e.g. +150): p = 100 / (odds + 100)
    Negative odds (e.g. -200): p = |odds| / (|odds| + 100)
    """
    if odds > 0:
        return 100 / (odds + 100)
    else:
        abs_odds = abs(odds)
        return abs_odds / (abs_odds + 100)


def implied_probability_from_decimal(odds: float) -> float:
    """Derive implied probability from decimal (European) odds."""
    if odds <= 1.0:
        raise ValueError(f"Decimal odds must be > 1.0, got {odds}")
    return 1.0 / odds
