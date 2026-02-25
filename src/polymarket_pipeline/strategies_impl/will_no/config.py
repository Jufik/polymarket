"""Configuration for the will-no (favorite-longshot) strategy."""

from __future__ import annotations

from dataclasses import dataclass, field

# Data-derived price bands (explore_s2_deep.py / explore_s2_final.py).
# Each tuple: (yes_price_min, yes_price_max, bet_multiplier).
# Multiplier is $/bet normalised to the best band (30-35 % = 1.0).
#
# Within the profitable niche ("between" + vol < $1K), the edge
# INCREASES with YES price — opposite of the original research.
#
# | YES %  | HR    | $/bet  | edge   | ROI    | mult |
# |--------|-------|--------|--------|--------|------|
# | 15-20  | 89.4% | $+3.66 | +6.1%  | +7.3%  | 0.35 |
# | 20-25  | 87.7% | $+5.87 | +9.2%  | +11.7% | 0.60 |
# | 25-30  | 85.6% | $+8.21 | +12.1% | +16.4% | 0.80 |
# | 30-35  | 81.5% | $+10.10| +13.7% | +20.2% | 1.00 |
# | 35-40  | 73.9% | $+8.92 | +11.2% | +17.8% | 0.90 |
DEFAULT_PRICE_BANDS: tuple[tuple[float, float, float], ...] = (
    (0.15, 0.20, 0.35),
    (0.20, 0.25, 0.60),
    (0.25, 0.30, 0.80),
    (0.30, 0.35, 1.00),
    (0.35, 0.40, 0.90),
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
        Maximum event volume — thin markets have higher edge.
        Data shows vol < $1K is the critical profitability filter.
        Enforced in both event-driven (when available) and vectorized paths.
        0 = no filter.
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

    yes_price_min: float = 0.15
    yes_price_max: float = 0.40
    base_bet_usd: float = 50.0
    fee_pct: float = 0.0
    price_bands: tuple[tuple[float, float, float], ...] = DEFAULT_PRICE_BANDS
    prefer_keywords: frozenset[str] = field(default_factory=frozenset)
    avoid_keywords: frozenset[str] = field(default_factory=frozenset)
    max_volume_usd: float = 1000.0
    question_pattern: str = r"^Will\b"
    max_bucket: str | None = "med"
    dual_sided: bool = False

    def __init__(
        self,
        yes_price_min: float = 0.15,
        yes_price_max: float = 0.40,
        base_bet_usd: float = 50.0,
        fee_pct: float = 0.0,
        price_bands: tuple[tuple[float, float, float], ...] | None = None,
        prefer_keywords: set[str] | frozenset[str] | list[str] | None = None,
        avoid_keywords: set[str] | frozenset[str] | list[str] | None = None,
        max_volume_usd: float = 1000.0,
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
