"""GBM fair value computation for BTC Up or Down markets.

P(Up) = Φ(d₂)  where  d₂ = ln(S_t / S₀) / (σ √(T − t))
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm


def compute_gbm_p_up(
    s_start: float,
    s_current: float,
    sigma_1m: float,
    time_remaining_min: float,
) -> float:
    """Probability BTC ends the window above its opening price.

    Parameters
    ----------
    s_start : BTC price at window open.
    s_current : BTC price right now.
    sigma_1m : Per-minute realized volatility (std of 1m log returns).
    time_remaining_min : Minutes remaining in the window.

    Returns
    -------
    P(Up) in [0, 1].
    """
    if time_remaining_min <= 0:
        return 1.0 if s_current > s_start else 0.0

    if s_start <= 0 or s_current <= 0:
        return 0.5

    log_return = np.log(s_current / s_start)
    vol = sigma_1m * np.sqrt(time_remaining_min)

    if vol < 1e-12:
        return 1.0 if log_return > 0 else (0.0 if log_return < 0 else 0.5)

    d2 = log_return / vol
    return float(norm.cdf(d2))


def estimate_rolling_sigma(
    closes: list[float],
    window: int = 1440,
    min_samples: int = 60,
) -> float | None:
    """Rolling std of 1-minute log returns.

    Parameters
    ----------
    closes : 1-minute close prices, most recent last.
    window : Lookback in minutes (default 1440 = 24 h).
    min_samples : Minimum data points required.

    Returns
    -------
    Per-minute σ, or None if insufficient data.
    """
    if len(closes) < max(min_samples, 2):
        return None
    arr = np.array(closes[-window:], dtype=np.float64)
    if len(arr) < 2:
        return None
    log_returns = np.diff(np.log(arr))
    log_returns = log_returns[np.isfinite(log_returns)]
    if len(log_returns) < min_samples:
        return None
    return float(np.std(log_returns, ddof=1))
