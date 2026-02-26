"""Configuration for the will-no (favorite-longshot) strategy."""

from __future__ import annotations

from dataclasses import dataclass, field

# Data-derived price bands (explore_s2_deeper.py / explore_s2_expand.py).
# Each tuple: (yes_price_min, yes_price_max, bet_multiplier).
# Multiplier normalised to the 30-35 % band = 1.0.
#
# Calibrated on prod_kw + vol < $2K (~2,500 signals, 18 months).
# Edge INCREASES with YES price: higher payoff ratio more than compensates
# for the lower HR.  Bands above 50 % have extraordinary edge because NO
# is cheap ($0.30-0.50) and pays $1.00 on win.  Multipliers above 50 %
# are capped conservatively (max 2.00) given smaller sample sizes.
#
# | YES %  | HR    | ROI(2%) | $/bet    | med LD | mult |
# |--------|-------|---------|----------|--------|------|
# | 10-15  | 92.9% | +3.1%   | $+1.55   | 2.4d   | 0.25 |
# | 15-20  | 87.2% | +3.0%   | $+1.50   | 2.0d   | 0.25 |
# | 20-25  | 85.7% | +7.6%   | $+3.81   | 1.6d   | 0.60 |
# | 25-30  | 81.0% | +8.5%   | $+4.27   | 1.2d   | 0.75 |
# | 30-35  | 77.0% | +12.0%  | $+5.98   | 1.0d   | 1.00 |
# | 35-40  | 72.2% | +13.9%  | $+6.97   | 0.3d   | 1.10 |
# | 40-45  | 68.2% | +16.3%  | $+8.15   | 0.8d   | 1.30 |
# | 45-50  | 68.1% | +28.8%  | $+14.40  | 1.0d   | 1.50 |
# | 50-55  | 77.1% | +54.7%  | $+27.35  | 1.8d   | 1.80 |
# | 55-60  | 59.1% | +34.5%  | $+17.26  | 1.1d   | 1.20 |
# | 60-65  | 68.6% | +81.0%  | $+40.50  | 1.0d   | 1.80 |
# | 65-70  | 73.0% | +131.0% | $+65.50  | 0.6d   | 2.00 |
DEFAULT_PRICE_BANDS: tuple[tuple[float, float, float], ...] = (
    (0.10, 0.15, 0.25),
    (0.15, 0.20, 0.25),
    (0.20, 0.25, 0.60),
    (0.25, 0.30, 0.75),
    (0.30, 0.35, 1.00),
    (0.35, 0.40, 1.10),
    (0.40, 0.45, 1.30),
    (0.45, 0.50, 1.50),
    (0.50, 0.55, 1.80),
    (0.55, 0.60, 1.20),
    (0.60, 0.65, 1.80),
    (0.65, 0.70, 2.00),
)


@dataclass(frozen=True)
class WillNoConfig:
    """Immutable configuration for the will-no strategy.

    Parameters
    ----------
    yes_price_min
        Lower bound of the YES price band (inclusive).
        Only used when ``price_bands`` is empty (flat-pricing fallback).
    yes_price_max
        Upper bound of the YES price band (inclusive).
        Only used when ``price_bands`` is empty (flat-pricing fallback).
    base_bet_usd
        Reference bet size in USD.  Actual size is
        ``base_bet_usd * band_multiplier(yes_price)`` when bands are active.
    fee_pct
        Expected fee as a fraction (e.g. 0.02 = 2%).
    price_bands
        Tuple of ``(yes_min, yes_max, multiplier)`` tuples.  When non-empty
        the strategy uses band-based sizing instead of flat
        ``yes_price_min``/``yes_price_max``.  Pass ``()`` to disable bands
        and fall back to flat pricing.
    prefer_keywords
        Keywords that indicate profitable niche markets.  When non-empty the
        question must contain **at least one** keyword to qualify.
        Data-derived: ``{"between", "mlb", "prix", "grand", "league",
        "park", "traded", "fed"}`` (sports draws + finance).
    avoid_keywords
        Keywords that indicate negative edge or long lockup (skip).
    max_volume_usd
        Maximum market-level trade volume (sum of price × size per market).
        Vol < $2K balances throughput (~140 sigs/month) vs edge (+47% ROI).
        IMPORTANT: This is trade-level volume, NOT Gamma API event_volume
        (which is ~52× larger on median, corr=0.28 — completely different).
        Vectorized path checks ``volume_column`` in the joined DataFrame.
        0 = no filter.
    volume_column
        Column name to use for the volume filter in compute_signals().
        Default ``"market_volume"`` = trade-level sum(price*size).
        Set to ``"event_volume"`` to use Gamma API volume instead.
    question_pattern
        Regex pattern the question must match (case-insensitive).
        Default matches questions starting with "Will".
    max_bucket
        Maximum market-size bucket (thin/med/thick/heavy) allowed.
        Requires ``MarketSizeProvider`` in the feature pipeline.
        ``None`` = no filter.
    dual_sided
        When ``True`` emit both BUY NO and SELL YES at half size.
    """

    yes_price_min: float = 0.10
    yes_price_max: float = 0.70
    base_bet_usd: float = 50.0
    fee_pct: float = 0.0
    price_bands: tuple[tuple[float, float, float], ...] = DEFAULT_PRICE_BANDS
    prefer_keywords: frozenset[str] = field(default_factory=frozenset)
    avoid_keywords: frozenset[str] = field(default_factory=frozenset)
    max_volume_usd: float = 2000.0
    volume_column: str = "market_volume"
    question_pattern: str = r"^Will\b"
    max_bucket: str | None = "med"
    dual_sided: bool = False

    def __init__(
        self,
        yes_price_min: float = 0.10,
        yes_price_max: float = 0.70,
        base_bet_usd: float = 50.0,
        fee_pct: float = 0.0,
        price_bands: tuple[tuple[float, float, float], ...] | None = None,
        prefer_keywords: set[str] | frozenset[str] | list[str] | None = None,
        avoid_keywords: set[str] | frozenset[str] | list[str] | None = None,
        max_volume_usd: float = 2000.0,
        volume_column: str = "market_volume",
        question_pattern: str = r"^Will\b",
        max_bucket: str | None = "med",
        dual_sided: bool = False,
    ) -> None:
        object.__setattr__(self, "yes_price_min", yes_price_min)
        object.__setattr__(self, "yes_price_max", yes_price_max)
        object.__setattr__(self, "base_bet_usd", base_bet_usd)
        object.__setattr__(self, "fee_pct", fee_pct)
        object.__setattr__(
            self,
            "price_bands",
            price_bands if price_bands is not None else DEFAULT_PRICE_BANDS,
        )
        object.__setattr__(
            self,
            "prefer_keywords",
            frozenset(prefer_keywords)
            if prefer_keywords is not None
            else frozenset({
                "between", "mlb", "prix", "grand",
                "league", "park", "traded", "fed",
            }),
        )
        object.__setattr__(
            self,
            "avoid_keywords",
            frozenset(avoid_keywords)
            if avoid_keywords is not None
            else frozenset({"reach", "hit"}),
        )
        object.__setattr__(self, "max_volume_usd", max_volume_usd)
        object.__setattr__(self, "volume_column", volume_column)
        object.__setattr__(self, "question_pattern", question_pattern)
        object.__setattr__(self, "max_bucket", max_bucket)
        object.__setattr__(self, "dual_sided", dual_sided)

    def band_multiplier(self, yes_price: float) -> float:
        """Return bet-size multiplier for *yes_price* based on ``price_bands``.

        Uses ``[lo, hi)`` for all bands except the last which uses ``[lo, hi]``.
        Returns ``0.0`` when the price falls outside every band.
        """
        n = len(self.price_bands)
        for i, (lo, hi, mult) in enumerate(self.price_bands):
            if i < n - 1:
                if lo <= yes_price < hi:
                    return mult
            else:
                if lo <= yes_price <= hi:
                    return mult
        return 0.0
