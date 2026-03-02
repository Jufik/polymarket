"""S1 Hit-Rate Copy-Trading Strategy — production implementation.

Copies trades from historically high-hit-rate specialist traders when their
directional entry price falls in a configurable band AND at least N qualified
traders agree on the same side (consensus filter).

Validated filter stack (8-month OOS walk-forward):
  L1: HR >= 0.75, min_pos >= 20, 9-month lookback, non-gambling
  L2: dir_entry_price in [min_dir_price, max_dir_price)
  L3: Consensus >= min_consensus qualified traders on same side
  L4: Category specialization >= min_specialization
  L7: Skip traders with fewer than min_last5 correct in last 5

All heavy computation lives in ClickHouse (via S1HitRateProvider).
The strategy only reads pre-computed features from StrategyContext.

Feature keys consumed (set by S1HitRateProvider):
  s1_qualified_traders : set[str]        — lowercase trader addresses
  s1_trader_meta       : dict[str, dict] — per-trader {spec, dom_cat, last5}
  s1_token_outcomes    : dict[str, str]  — asset_id -> "YES" | "NO"
  s1_gambling_cids     : set[str]        — gambling market condition_ids
  s1_consensus         : dict[str, dict] — condition_id -> {yes: int, no: int}
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polymarket_pipeline.strategies.types import TradeIntent

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import StrategyContext


class S1HitRateCopyStrategy:
    """Copy high-hit-rate specialist traders on directional entries."""

    name: str = "s1_hitrate_copy"

    def __init__(
        self,
        size_usd: float = 10.0,
        min_dir_price: float = 0.60,
        max_dir_price: float = 0.90,
        min_specialization: float = 0.70,
        min_last5: int = 1,
        min_consensus: int = 4,
        sizing_mode: str = "fixed",
        sizing_fraction: float = 0.02,
        max_bet_usd: float = 500.0,
        max_hold_hours: float = 168.0,
        **_extra: Any,
    ) -> None:
        self._size_usd = size_usd
        self._min_dir_price = min_dir_price
        self._max_dir_price = max_dir_price
        self._min_spec = min_specialization
        self._min_last5 = min_last5
        self._min_consensus = min_consensus
        self._max_hold_s = max_hold_hours * 3600.0
        # Sizing: "fixed" = constant size_usd, "fractional" = bankroll * fraction
        self._sizing_mode = sizing_mode
        self._sizing_fraction = sizing_fraction
        self._max_bet = max_bet_usd

    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: StrategyContext,
    ) -> list[TradeIntent] | None:
        # Skip trades without a maker
        if trade.maker is None:
            return None

        # Only BUY trades are directional signals. SELL = exit, not new bet.
        if str(trade.side) != "BUY":
            return None

        maker_lower = trade.maker.lower()

        # ── L1: qualified pool ──────────────────────────────────────
        qualified: set[str] | None = await ctx.get_features("s1_qualified_traders")
        if qualified is None or maker_lower not in qualified:
            return None

        # ── Gambling exclusion ──────────────────────────────────────
        gambling: set[str] | None = await ctx.get_features("s1_gambling_cids")
        if gambling is not None and trade.condition_id in gambling:
            return None

        # ── Hold-time filter: skip long-dated markets ──────────────
        close_epochs: dict[str, float] | None = await ctx.get_features(
            "s1_market_close_epochs"
        )
        if close_epochs is not None:
            close_epoch = close_epochs.get(trade.condition_id, 0.0)
            if close_epoch > 0:
                remaining_s = close_epoch - trade.published_at
                if remaining_s > self._max_hold_s:
                    return None

        # ── Resolve outcome from asset_id ───────────────────────────
        token_outcomes: dict[str, str] | None = await ctx.get_features("s1_token_outcomes")
        if token_outcomes is None:
            return None
        outcome = token_outcomes.get(trade.asset_id)
        if outcome is None:
            return None

        # ── Trader's fill price (signal quality check) ──────────────
        trader_price = float(trade.price)
        trader_dir_price = trader_price if outcome == "YES" else 1.0 - trader_price

        # Quick reject: if the trader entered outside our band, skip.
        # This filters signal quality before we hit the orderbook.
        if trader_dir_price < self._min_dir_price or trader_dir_price >= self._max_dir_price:
            return None

        # ── L4 + L7: trader metadata (specialization, streak) ──────
        trader_meta: dict[str, dict[str, Any]] | None = await ctx.get_features(
            "s1_trader_meta"
        )
        if trader_meta is not None:
            meta = trader_meta.get(maker_lower)
            if meta is not None:
                if meta.get("spec", 0.0) < self._min_spec:
                    return None
                if meta.get("last5", 0) < self._min_last5:
                    return None

        # ── Copy direction = token outcome (BUY-only, so always same side) ──
        copy_outcome = outcome

        # ── L3: consensus filter ────────────────────────────────────
        consensus: dict[str, dict[str, int]] | None = await ctx.get_features(
            "s1_consensus"
        )
        consensus_count = 0
        if consensus is not None:
            state = consensus.get(trade.condition_id)
            if state is None:
                return None
            key = "yes" if copy_outcome == "YES" else "no"
            consensus_count = len(state.get(key, set()))
            if consensus_count < self._min_consensus:
                return None

        # ── Dedup: one position per market ──────────────────────────
        existing = await ctx.get_position(trade.condition_id)
        if existing is not None and (existing.qty_yes > 0 or existing.qty_no > 0):
            return None

        # ── Our entry price from orderbook ──────────────────────────
        # The trader got their price; ours comes from the current book.
        # PaperExecutor resolves the final fill price from the orderbook,
        # but we set max_price as our limit to avoid overpaying.
        ob = await ctx.get_orderbook(trade.condition_id)
        if ob is not None and ob.best_ask is not None:
            our_price = ob.best_ask if copy_outcome == outcome else 1.0 - ob.best_bid
            # L2 on OUR price: reject if the book has moved too far
            our_dir = our_price if copy_outcome == "YES" else 1.0 - our_price
            if our_dir >= self._max_dir_price:
                return None
            max_price = our_price + 0.01
        else:
            # No orderbook yet — use trader's price with a small buffer.
            # PaperExecutor will verify via CLOB REST before filling.
            our_dir = trader_dir_price
            max_price = trader_dir_price + 0.02

        # ── Position sizing ──────────────────────────────────────────
        if self._sizing_mode == "fractional":
            # Compute bet from current equity (deployed + realized PnL)
            bankroll: float | None = await ctx.get_features("s1_bankroll")
            if bankroll is None:
                bankroll = self._size_usd / self._sizing_fraction  # fallback
            bet = min(bankroll * self._sizing_fraction, self._max_bet)
            bet = max(bet, 1.0)  # floor at $1
        else:
            bet = self._size_usd

        return [
            TradeIntent(
                strategy=self.name,
                condition_id=trade.condition_id,
                side="BUY",
                outcome=copy_outcome,
                size_usd=bet,
                urgency="patient",
                max_price=max_price,
                reason=(
                    f"copy {maker_lower[:10]} "
                    f"trader={trader_dir_price:.3f} "
                    f"book={our_dir:.3f} "
                    f"out={copy_outcome} "
                    f"cons={consensus_count} "
                    f"bet=${bet:.0f}"
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
