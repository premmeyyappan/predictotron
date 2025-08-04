"""
Momentum and trend indicators for prediction market price series.

Implements a subset of standard technical indicators adapted for probability
time series, where values are bounded to [0, 1]. All functions operate on
plain Python lists / numpy arrays to keep the analytics layer dependency-free
from database concerns.
"""

from __future__ import annotations

import numpy as np

from app.schemas.analytics import MomentumSchema


def _ema(prices: np.ndarray, period: int) -> np.ndarray:
    """Compute the exponential moving average with a given period."""
    alpha = 2.0 / (period + 1)
    ema = np.empty_like(prices)
    ema[0] = prices[0]
    for i in range(1, len(prices)):
        ema[i] = alpha * prices[i] + (1 - alpha) * ema[i - 1]
    return ema


def _rsi(prices: np.ndarray, period: int = 14) -> float | None:
    """
    Compute the Relative Strength Index over the last `period` bars.

    RSI = 100 - 100 / (1 + RS), where RS = avg_gain / avg_loss.
    Returns None when insufficient data is available.
    """
    if len(prices) < period + 1:
        return None

    deltas = np.diff(prices[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = gains.mean()
    avg_loss = losses.mean()

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _rate_of_change(prices: np.ndarray, period: int = 5) -> float | None:
    """
    Percentage rate of change over `period` bars.

    ROC = (price_now - price_n_bars_ago) / price_n_bars_ago * 100
    """
    if len(prices) <= period:
        return None
    base = prices[-(period + 1)]
    if base == 0:
        return None
    return float((prices[-1] - base) / base * 100)


def compute_momentum(prices: list[float]) -> MomentumSchema:
    """
    Derive momentum indicators from a price time series.

    Args:
        prices: Chronologically ordered list of implied probabilities.

    Returns:
        MomentumSchema with EMA crossover, MACD, RSI, and rate-of-change.
    """
    if not prices:
        return MomentumSchema(
            ema_12=None, ema_26=None, macd=None, rsi_14=None,
            rate_of_change_5=None, trend="neutral",
        )

    arr = np.array(prices, dtype=float)

    ema_12: float | None = None
    ema_26: float | None = None
    macd: float | None = None

    if len(arr) >= 12:
        ema_12 = float(_ema(arr, 12)[-1])
    if len(arr) >= 26:
        ema_26 = float(_ema(arr, 26)[-1])
    if ema_12 is not None and ema_26 is not None:
        macd = ema_12 - ema_26

    rsi = _rsi(arr)
    roc = _rate_of_change(arr)

    # Trend classification based on EMA crossover and RSI
    if macd is not None and rsi is not None:
        if macd > 0 and rsi > 55:
            trend = "bullish"
        elif macd < 0 and rsi < 45:
            trend = "bearish"
        else:
            trend = "neutral"
    elif roc is not None:
        trend = "bullish" if roc > 0 else "bearish" if roc < 0 else "neutral"
    else:
        trend = "neutral"

    return MomentumSchema(
        ema_12=ema_12,
        ema_26=ema_26,
        macd=macd,
        rsi_14=rsi,
        rate_of_change_5=roc,
        trend=trend,
    )
