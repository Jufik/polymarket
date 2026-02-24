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
        "proportional" = scale bet to trader's ROI; "equal" = fixed per trader.
    """

    pool_traders: frozenset[str] = field(default_factory=frozenset)
    capital_per_trader_usd: float = 50.0
    max_position_pct: float = 0.05
    min_pool_agreement: float = 0.0
    contradiction_filter: bool = True
    fee_pct: float = 0.02
    sizing: str = "equal"

    def __init__(
        self,
        pool_traders: set[str] | frozenset[str] | list[str] | None = None,
        capital_per_trader_usd: float = 50.0,
        max_position_pct: float = 0.05,
        min_pool_agreement: float = 0.0,
        contradiction_filter: bool = True,
        fee_pct: float = 0.02,
        sizing: str = "equal",
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
