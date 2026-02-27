"""Consensus-copy strategy: event-driven and vectorized implementations.

Follows skilled-trader agreement to generate trade signals.  BUY = buying
YES tokens (betting YES), SELL = selling YES tokens (betting NO).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import polars as pl

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.strategies.protocol import StrategyContext
from polymarket_pipeline.strategies.types import TradeIntent
from polymarket_pipeline.strategies_impl.consensus_copy.config import ConsensusCopyConfig

# ---------------------------------------------------------------------------
# Internal per-market state (event-driven path)
# ---------------------------------------------------------------------------


@dataclass
class _MarketState:
    """Mutable per-market accumulator for the event-driven path."""

    n_yes: int = 0
    n_no: int = 0
    seen_traders: set[str] = field(default_factory=set)
    signal_fired: bool = False


# ---------------------------------------------------------------------------
# ConsensusCopyStrategy
# ---------------------------------------------------------------------------


class ConsensusCopyStrategy:
    """Consensus-copy strategy implementing both Strategy and VectorizedStrategy.

    Event-driven (``on_trade``): tracks per-market skilled-trader arrivals
    and fires a signal when enough traders agree on a direction.

    Vectorized (``compute_signals``): same logic over a full Polars DataFrame.
    """

    name: str = "consensus_copy"

    def __init__(self, config: ConsensusCopyConfig) -> None:
        self._cfg = config
        self._states: dict[str, _MarketState] = {}

    # ------------------------------------------------------------------
    # Event-driven path
    # ------------------------------------------------------------------

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        """Process a single trade and potentially emit a signal."""
        maker = trade.maker
        skilled = await ctx.get_features("skilled_traders")
        if skilled is None:
            skilled = self._cfg.skilled_traders  # fallback to config
        if maker is None or maker not in skilled:
            return None

        cid = trade.condition_id
        state = self._states.get(cid)
        if state is None:
            state = _MarketState()
            self._states[cid] = state

        if state.signal_fired:
            return None

        if maker in state.seen_traders:
            return None
        state.seen_traders.add(maker)

        # BUY = buying YES tokens = betting YES
        # SELL = selling YES tokens = betting NO
        if trade.side == "BUY":
            state.n_yes += 1
        else:
            state.n_no += 1

        n_traders = state.n_yes + state.n_no
        if n_traders < self._cfg.min_traders:
            return None

        agreement = max(state.n_yes, state.n_no) / n_traders
        if agreement < self._cfg.agreement_pct:
            return None

        signal_direction: Literal["YES", "NO"] = "YES" if state.n_yes >= state.n_no else "NO"

        if self._cfg.direction != "both" and signal_direction != self._cfg.direction:
            return None

        state.signal_fired = True

        # max_price from triggering trade: directional entry + slippage
        trade_price = float(trade.price)
        is_yes = signal_direction == "YES"
        entry = trade_price if is_yes else 1.0 - trade_price
        max_price = min(entry + 0.05, 0.99)

        intent = TradeIntent(
            strategy=self.name,
            condition_id=cid,
            side="BUY",
            outcome=signal_direction,
            size_usd=self._cfg.base_bet_usd,
            urgency="patient",
            max_price=max_price,
            reason=(
                f"consensus_copy: {n_traders} skilled traders, "
                f"{agreement:.0%} agree {signal_direction}"
            ),
            signal_time=trade.published_at,
        )
        return [intent]

    async def on_market_update(self, update: Any, ctx: StrategyContext) -> list[TradeIntent] | None:
        """Not used by consensus-copy."""
        return None

    async def on_timer(self, now: float, ctx: StrategyContext) -> list[TradeIntent] | None:
        """Not used by consensus-copy."""
        return None

    # ------------------------------------------------------------------
    # Vectorized path
    # ------------------------------------------------------------------

    def compute_signals(self, trades: pl.LazyFrame, markets: pl.LazyFrame) -> pl.DataFrame:
        """Compute consensus-copy signals over historical trade data.

        Returns a DataFrame with columns:
        ``condition_id``, ``signal_time``, ``side``, ``outcome``, ``size_usd``.
        """
        skilled = list(self._cfg.skilled_traders)

        # 1. Filter to skilled traders
        df = trades.filter(pl.col("maker").is_in(skilled))

        # 2. Sort by condition_id, published_at
        df = df.sort(["condition_id", "published_at"])

        # 3. Deduplicate: first trade per (maker, condition_id)
        df = df.unique(subset=["maker", "condition_id"], keep="first")

        # Re-sort after dedup (unique may not preserve order)
        df = df.sort(["condition_id", "published_at"])

        # 4. Infer bet direction: BUY = YES, SELL = NO
        df = df.with_columns(
            (pl.col("side") == "BUY").alias("bet_yes"),
        )

        # 5. Cumulative counts per condition_id
        #    Note: pl.lit(1).cum_sum() produces garbage in Polars lazy mode;
        #    use cum_count() on an existing column instead.
        df = df.with_columns(
            pl.col("maker").cum_count().over("condition_id").cast(pl.Int64).alias("n_traders"),
            pl.col("bet_yes").cast(pl.Int64).cum_sum().over("condition_id").alias("n_yes"),
            (~pl.col("bet_yes")).cast(pl.Int64).cum_sum().over("condition_id").alias("n_no"),
        )

        # 6. Agreement fraction
        df = df.with_columns(
            (
                pl.max_horizontal("n_yes", "n_no").cast(pl.Float64)
                / pl.col("n_traders").cast(pl.Float64)
            ).alias("agreement_frac"),
        )

        # 7. Signal direction
        df = df.with_columns(
            pl.when(pl.col("n_yes") >= pl.col("n_no"))
            .then(pl.lit("YES"))
            .otherwise(pl.lit("NO"))
            .alias("signal_direction"),
        )

        # 8. Filter: threshold + direction
        df = df.filter(
            (pl.col("n_traders") >= self._cfg.min_traders)
            & (pl.col("agreement_frac") >= self._cfg.agreement_pct)
        )

        if self._cfg.direction != "both":
            df = df.filter(pl.col("signal_direction") == self._cfg.direction)

        # 9. First qualifying row per condition_id
        df = df.sort(["condition_id", "published_at"])
        df = df.unique(subset=["condition_id"], keep="first")

        # 10. Return output columns
        result = df.select(
            pl.col("condition_id"),
            pl.col("published_at").alias("signal_time"),
            pl.lit("BUY").alias("side"),
            pl.col("signal_direction").alias("outcome"),
            pl.lit(self._cfg.base_bet_usd).alias("size_usd"),
        )

        return result.collect()
