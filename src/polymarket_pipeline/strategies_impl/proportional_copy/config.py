"""Configuration for the proportional-copy strategy (S1)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProportionalCopyConfig:
    """Immutable configuration for the proportional-copy strategy.

    Parameters
    ----------
    pool_traders
        Pre-computed set of graded trader addresses to copy.
        Computed externally (consistency + MVF + longshot grade).
    capital_per_trader_usd
        Maximum USD to allocate per pool trader.
    max_position_pct
        Maximum fraction of total capital in any single market.
    min_pool_agreement
        Minimum fraction of pool traders agreeing on the same direction
        before copying. 0.0 = copy any trader's entry. 0.5 = majority.
    contradiction_filter
        If True, skip markets where pool traders disagree on direction.
    fee_pct
        Expected fee fraction.
    sizing
        "equal" = fixed per trader; "proportional" = scale by relative trade size.
    max_sizing_mult
        Cap on proportional sizing multiplier (prevents outsized bets
        when a trader makes an unusually large trade).
    max_entry_price
        Maximum directional entry price for copy intent. Prevents chasing
        expensive fills. None = no cap.
    price_slippage
        Slippage buffer added to the trigger trade price for max_price.
    """

    pool_traders: frozenset[str] = field(default_factory=frozenset)
    capital_per_trader_usd: float = 50.0
    max_position_pct: float = 0.05
    min_pool_agreement: float = 0.0
    contradiction_filter: bool = True
    fee_pct: float = 0.02
    sizing: str = "equal"
    max_sizing_mult: float = 3.0
    max_entry_price: float | None = None
    price_slippage: float = 0.05

    def __init__(
        self,
        pool_traders: set[str] | frozenset[str] | list[str] | None = None,
        capital_per_trader_usd: float = 50.0,
        max_position_pct: float = 0.05,
        min_pool_agreement: float = 0.0,
        contradiction_filter: bool = True,
        fee_pct: float = 0.02,
        sizing: str = "equal",
        max_sizing_mult: float = 3.0,
        max_entry_price: float | None = None,
        price_slippage: float = 0.05,
    ) -> None:
        object.__setattr__(
            self,
            "pool_traders",
            frozenset(pool_traders) if pool_traders is not None else frozenset(),
        )
        object.__setattr__(self, "capital_per_trader_usd", capital_per_trader_usd)
        object.__setattr__(self, "max_position_pct", max_position_pct)
        object.__setattr__(self, "min_pool_agreement", min_pool_agreement)
        object.__setattr__(self, "contradiction_filter", contradiction_filter)
        object.__setattr__(self, "fee_pct", fee_pct)
        object.__setattr__(self, "sizing", sizing)
        object.__setattr__(self, "max_sizing_mult", max_sizing_mult)
        object.__setattr__(self, "max_entry_price", max_entry_price)
        object.__setattr__(self, "price_slippage", price_slippage)
