"""S1 Hit-Rate Copy-Trading Strategy.

Copies trades from historically high-hit-rate specialist traders when their
directional entry price falls in the 0.60-0.90 range AND at least N qualified
traders agree on the same side (consensus filter).

Validated filter stack (S1 backtest, 8-month OOS, 87.9% HR, $0.94/trade):
  L1: HR >= 0.75, pos >= 20, 9mo lookback, non-gambling
  L2: dir_entry_price >= 0.60 AND < 0.90
  L3: Consensus >= 4 qualified traders on same side
  L4: Category specialization >= 0.70
  L7: Skip traders with 0 of last 5 correct

The strategy reads pre-computed features from StrategyContext:
  - "s1_qualified_traders": set[str] of lowercase trader addresses
  - "s1_trader_meta": dict[str, TraderMeta] with specialization, streak, etc.
  - "s1_token_outcomes": dict[str, str] mapping asset_id -> "YES" | "NO"
  - "s1_gambling_cids": set[str] of gambling market condition_ids
  - "s1_consensus": dict[str, ConsensusState] mapping condition_id -> consensus count

Usage:
    from research.strategies.s1_hitrate_copy import S1HitRateCopyStrategy
    strategy = S1HitRateCopyStrategy(size_usd=10.0)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polymarket_pipeline.strategies.types import TradeIntent

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import StrategyContext


@dataclass(frozen=True)
class TraderMeta:
    """Per-trader metadata from training window."""

    train_hr: float
    train_positions: int
    specialization: float
    dominant_cat: str
    last5_correct: int


@dataclass
class ConsensusState:
    """Mutable consensus counter for a market-side pair.

    Tracks how many qualified traders have entered on YES vs NO side.
    Updated by the S1 FeatureProvider's on_trade() method.
    """

    yes_count: int = 0
    no_count: int = 0

    def count_for(self, outcome: str) -> int:
        return self.yes_count if outcome == "YES" else self.no_count


class S1HitRateCopyStrategy:
    """Copy high-hit-rate specialist traders on directional entries.

    On each incoming trade, checks if the maker is a qualified trader and
    the trade passes all filter layers. If so, emits a BUY intent on the
    same side as the trader.

    IMPORTANT: Consensus filter is critical for profitability. Without it,
    $10 fixed bets have near-zero EV. With consensus >= 4, all price bands
    0.60-0.90 become strongly positive EV.
    """

    name: str = "s1_hitrate_copy"

    def __init__(
        self,
        size_usd: float = 10.0,
        min_dir_price: float = 0.60,
        max_dir_price: float = 0.90,
        min_specialization: float = 0.70,
        min_last5: int = 1,
        min_consensus: int = 4,
    ) -> None:
        self._size_usd = size_usd
        self._min_dir_price = min_dir_price
        self._max_dir_price = max_dir_price
        self._min_spec = min_specialization
        self._min_last5 = min_last5
        self._min_consensus = min_consensus

    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: StrategyContext,
    ) -> list[TradeIntent] | None:
        # Skip trades without a maker (WS-only trades)
        if trade.maker is None:
            return None

        maker_lower = trade.maker.lower()

        # L1: Is this trader in the qualified pool?
        qualified: set[str] | None = await ctx.get_features("s1_qualified_traders")
        if qualified is None or maker_lower not in qualified:
            return None

        # Gambling exclusion
        gambling: set[str] | None = await ctx.get_features("s1_gambling_cids")
        if gambling is not None and trade.condition_id in gambling:
            return None

        # Determine outcome from asset_id
        token_outcomes: dict[str, str] | None = await ctx.get_features(
            "s1_token_outcomes"
        )
        if token_outcomes is None:
            return None
        outcome = token_outcomes.get(trade.asset_id)
        if outcome is None:
            return None

        # Compute directional entry price
        price = float(trade.price)
        if outcome == "YES":
            dir_price = price  # buying YES at this price
        else:
            dir_price = 1.0 - price  # buying NO = selling YES, directional = 1-price

        # L2: Price filter
        if dir_price < self._min_dir_price or dir_price >= self._max_dir_price:
            return None

        # L4 + L7: Trader metadata filters (specialization, streak)
        trader_meta: dict[str, TraderMeta] | None = await ctx.get_features(
            "s1_trader_meta"
        )
        if trader_meta is not None:
            meta = trader_meta.get(maker_lower)
            if meta is not None:
                if meta.specialization < self._min_spec:
                    return None
                if meta.last5_correct < self._min_last5:
                    return None

        # Determine the trader's directional side
        side_str = str(trade.side)
        if side_str == "BUY":
            copy_outcome = outcome
        else:
            copy_outcome = "NO" if outcome == "YES" else "YES"

        # L3: Consensus filter -- require N qualified traders on the same side
        # The FeatureProvider updates consensus counts as qualified trades arrive.
        # We only enter when enough traders have independently agreed.
        consensus_count = 0
        consensus: dict[str, ConsensusState] | None = await ctx.get_features(
            "s1_consensus"
        )
        if consensus is not None:
            state = consensus.get(trade.condition_id)
            if state is None:
                return None
            consensus_count = state.count_for(copy_outcome)
            if consensus_count < self._min_consensus:
                return None

        # Already have a position in this market? Skip.
        existing = await ctx.get_position(trade.condition_id)
        if existing is not None and (existing.qty_yes > 0 or existing.qty_no > 0):
            return None

        return [
            TradeIntent(
                strategy=self.name,
                condition_id=trade.condition_id,
                side="BUY",
                outcome=copy_outcome,
                size_usd=self._size_usd,
                urgency="patient",
                max_price=dir_price + 0.02,
                reason=(
                    f"copy {maker_lower[:8]}... "
                    f"dir_price={dir_price:.3f} "
                    f"outcome={copy_outcome} "
                    f"consensus={consensus_count}"
                ),
                signal_time=trade.published_at,
                asset_id=trade.asset_id,
            ),
        ]

    async def on_market_update(
        self, update: Any, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None
