"""Configuration for the consensus-copy strategy."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConsensusCopyConfig:
    """Immutable configuration for the consensus-copy strategy.

    Parameters
    ----------
    skilled_traders
        Pre-computed set of trader addresses considered skilled
        (from historical consistency analysis).
    min_traders
        Minimum number of skilled traders required before a signal fires.
    agreement_pct
        Minimum fraction of traders agreeing on the same direction.
    direction
        Which signal direction to act on: ``"YES"``, ``"NO"``, or ``"both"``.
    delay_s
        Execution delay in seconds (handled by the gateway, not the strategy).
    sizing
        Sizing mode. Currently only ``"fixed"`` is supported.
    base_bet_usd
        Fixed bet size in USD when ``sizing="fixed"``.
    fee_pct
        Expected fee as a fraction (e.g. 0.02 = 2%).
    """

    skilled_traders: frozenset[str] = field(default_factory=frozenset)
    min_traders: int = 5
    agreement_pct: float = 0.80
    direction: str = "NO"
    delay_s: float = 60.0
    sizing: str = "fixed"
    base_bet_usd: float = 10.0
    fee_pct: float = 0.02

    def __init__(
        self,
        skilled_traders: set[str] | frozenset[str] | None = None,
        min_traders: int = 5,
        agreement_pct: float = 0.80,
        direction: str = "NO",
        delay_s: float = 60.0,
        sizing: str = "fixed",
        base_bet_usd: float = 10.0,
        fee_pct: float = 0.02,
    ) -> None:
        # frozen=True requires object.__setattr__
        object.__setattr__(
            self,
            "skilled_traders",
            frozenset(skilled_traders) if skilled_traders is not None else frozenset(),
        )
        object.__setattr__(self, "min_traders", min_traders)
        object.__setattr__(self, "agreement_pct", agreement_pct)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "delay_s", delay_s)
        object.__setattr__(self, "sizing", sizing)
        object.__setattr__(self, "base_bet_usd", base_bet_usd)
        object.__setattr__(self, "fee_pct", fee_pct)
