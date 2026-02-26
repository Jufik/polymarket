"""Configuration for the HR Pool copy strategy.

Simple high-HR trader pool with fixed-bet copy. Sweep-validated:
- HR >= 0.65-0.75 with monthly consistency
- NO-direction + cheap entry (<0.50) is the robust edge
- Zero fees on most Polymarket markets
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HRPoolConfig:
    """Immutable configuration for the HR Pool strategy.

    Parameters
    ----------
    pool_traders
        Pre-computed set of high-HR trader addresses.
    bet_size_usd
        Fixed USD per signal (no proportional sizing).
    direction
        "NO" (robust default), "ALL", or "YES".
    max_entry_price
        Maximum directional entry price. 0.50 = longshot only.
    price_slippage
        Slippage buffer added to trigger price for max_price.
    contradiction_filter
        Skip markets where pool traders disagree on direction.
    min_hr
        Minimum training hit rate for pool membership.
    min_markets
        Minimum resolved markets to qualify.
    min_good_months
        Minimum months with HR >= monthly_hr and >= monthly_min_bets.
    monthly_hr
        HR threshold for a month to count as "good".
    monthly_min_bets
        Minimum bets in a month for it to count.
    max_classification_entry
        Entry price cap in classification (safe-bet filter).
    min_positions
        Minimum positions per trader (one-off filter).
    max_positions
        Maximum positions per trader (bot filter).
    """

    pool_traders: frozenset[str] = field(default_factory=frozenset)
    bet_size_usd: float = 100.0
    direction: str = "NO"
    max_entry_price: float = 0.50
    price_slippage: float = 0.05
    contradiction_filter: bool = True
    # Pool construction
    min_hr: float = 0.65
    min_markets: int = 30
    min_good_months: int = 6
    monthly_hr: float = 0.50
    monthly_min_bets: int = 3
    # Classification
    max_classification_entry: float = 0.90
    min_positions: int = 3
    max_positions: int = 5000

    def __init__(
        self,
        pool_traders: set[str] | frozenset[str] | list[str] | None = None,
        bet_size_usd: float = 100.0,
        direction: str = "NO",
        max_entry_price: float = 0.50,
        price_slippage: float = 0.05,
        contradiction_filter: bool = True,
        min_hr: float = 0.65,
        min_markets: int = 30,
        min_good_months: int = 6,
        monthly_hr: float = 0.50,
        monthly_min_bets: int = 3,
        max_classification_entry: float = 0.90,
        min_positions: int = 3,
        max_positions: int = 5000,
    ) -> None:
        object.__setattr__(
            self,
            "pool_traders",
            frozenset(pool_traders) if pool_traders is not None else frozenset(),
        )
        object.__setattr__(self, "bet_size_usd", bet_size_usd)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "max_entry_price", max_entry_price)
        object.__setattr__(self, "price_slippage", price_slippage)
        object.__setattr__(self, "contradiction_filter", contradiction_filter)
        object.__setattr__(self, "min_hr", min_hr)
        object.__setattr__(self, "min_markets", min_markets)
        object.__setattr__(self, "min_good_months", min_good_months)
        object.__setattr__(self, "monthly_hr", monthly_hr)
        object.__setattr__(self, "monthly_min_bets", monthly_min_bets)
        object.__setattr__(self, "max_classification_entry", max_classification_entry)
        object.__setattr__(self, "min_positions", min_positions)
        object.__setattr__(self, "max_positions", max_positions)
