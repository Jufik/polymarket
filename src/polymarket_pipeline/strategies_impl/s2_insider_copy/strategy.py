"""S2 Insider Copy Strategy — copy high-conviction insider BUY trades.

Identifies traders with abnormally high hit rates on insider-susceptible
markets (politics, regulatory, corporate) and mirrors their entries.

==========================================================================
VALIDATED RESULTS (tick-by-tick, 3 test months, walk-forward)
==========================================================================

    Config                 | HR    | PnL (3mo)  | Kelly EV  | Compounding
    -----------------------+-------+------------+-----------+------------
    C>=3, price<0.65       | 57.3% | +$784K     | +$1.57/$1 | 12.37
    C>=2, no price filter  | 66.8% | +$254K     | NEGATIVE  | 7.01
    C>=5, price<0.65       | 59.0% | +$698K     | +$1.81/$1 | 12.14

    Base rate: ~44%. Vectorized-to-tick degradation: 18-29pp (expected 20-40pp).

    CRITICAL: Configs WITHOUT entry price filter have NEGATIVE Kelly —
    at avg entry 0.71, a +40% win doesn't offset -100% losses at 65% HR.
    The max_entry_price < 0.65 filter is MANDATORY for positive expectation.

==========================================================================
VIABLE CONFIGURATIONS
==========================================================================

    MAX COMPOUNDING (fastest capital recycling):
        min_consensus = 3
        max_entry_price = 0.65
        size_usd = quarter-Kelly (~11% of bankroll)
        Expected: 57% HR, +$1.57/$ risked, compound score 12.37

    HIGHER CONSENSUS (more selective, slightly better HR):
        min_consensus = 5
        max_entry_price = 0.65
        size_usd = quarter-Kelly
        Expected: 59% HR, +$1.81/$ risked, compound score 12.14

==========================================================================
REQUIRED DEPENDENCIES
==========================================================================

    ClickHouse:
        - market_susceptibility VIEW (007_market_susceptibility.sql)
        - trader_positions_resolved VIEW (005/006 migrations)
        - markets, events, event_tags, tags (PG-replicated)

    Kafka topics:
        - trades.raw (main feed — triggers on_trade)
        - orderbooks.raw (PaperExecutor price source)

    Provider:
        - insider_copy_provider (loads pool from CH, tracks consensus)

==========================================================================
FUTURE WORK
==========================================================================

    - Gliding stop-loss: tighten stop as position profits (trailing stop)
    - Exit on insider reversal: sell if pool members start SELLing same market
    - Category-specific sizing: politics gets larger allocation than esports
    - Feature weight optimization: F1 (HR excess) dominates at 3x; simplify to F1+F6

==========================================================================
TWO-POOL DEPLOYMENT (fast + slow capital recycling)
==========================================================================

    Run two instances of this strategy with different TOML configs:

    [strategy.s2_insider_fast]   → esports only, hold to resolution
    [strategy.s2_insider_slow]   → politics+culture+finance, take-profit at 3%/day

    See configs/s2_insider_copy.toml for complete setup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from polymarket_pipeline.strategies.types import TradeIntent

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import StrategyContext

logger = structlog.get_logger(__name__)


@dataclass
class InsiderCopyConfig:
    """Strategy parameters — populated from TOML [strategy.*.params]."""

    min_consensus: int = 3
    size_usd: float = 50.0
    stop_loss_pct: float = 0.50
    max_entry_price: float = 0.65
    categories: list[str] = field(default_factory=list)
    take_profit_daily_pct: float = 0.0
    min_days_to_take_profit: int = 7


class InsiderCopyStrategy:
    """Copy trades from identified insiders.

    Entry: When min_consensus unique insiders BUY into a market at price
    below max_entry_price. Exit: Hold to resolution + stop-loss + optional
    take-profit (earnings/day metric for slow pool).

    The insider pool is provided by InsiderCopyProvider via context features.
    """

    name: str = "s2_insider_copy"

    def __init__(self, cfg: InsiderCopyConfig, name: str = "s2_insider_copy") -> None:
        self._cfg = cfg
        self.name = name
        self._entries: dict[str, dict[str, Any]] = {}
        self._signals: dict[str, dict[str, Any]] = {}

    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: StrategyContext,
    ) -> list[TradeIntent] | None:
        cid = trade.condition_id
        price = float(trade.price)

        # --- Stop-loss check on existing positions ---
        if cid in self._entries:
            pos = await ctx.get_position(cid)
            if pos and (pos.qty_yes > 0 or pos.qty_no > 0):
                entry = self._entries[cid]
                entry_price = entry["entry_price"]
                outcome = entry["outcome"]

                current_price = await ctx.get_price(cid, outcome)
                if current_price is None:
                    current_price = price

                if current_price < entry_price * (1 - self._cfg.stop_loss_pct):
                    qty = pos.qty_yes if outcome == "YES" else pos.qty_no
                    del self._entries[cid]
                    return [
                        TradeIntent(
                            strategy=self.name,
                            condition_id=cid,
                            side="SELL",
                            outcome=outcome,
                            size_usd=qty * current_price,
                            urgency="immediate",
                            max_price=current_price,
                            reason=f"stop-loss: {current_price:.3f} < "
                                   f"{entry_price:.3f} * "
                                   f"{1 - self._cfg.stop_loss_pct:.2f}",
                            signal_time=trade.published_at,
                            asset_id=trade.asset_id,
                        ),
                    ]
            return None

        # --- Entry logic: only BUY trades from insiders ---
        if trade.side != "BUY":
            return None

        # Entry price gate (MANDATORY for positive Kelly expectation)
        if price >= self._cfg.max_entry_price:
            return None

        # Look up insider pool from provider features
        pool = await self._get_pool(ctx)
        if not pool:
            return None

        maker = (trade.maker or "").lower()
        if maker not in pool:
            return None

        # Update inline consensus (unique traders per market)
        insider_info = pool[maker]
        direction = insider_info["direction"]

        if cid not in self._signals:
            self._signals[cid] = {
                "direction": direction,
                "insiders": set(),
                "consensus_count": 0,
            }
        sig = self._signals[cid]
        sig["insiders"].add(maker)
        sig["consensus_count"] = len(sig["insiders"])

        # Also merge provider-tracked signals (if available)
        provider_signals = await self._get_provider_signals(ctx)
        effective = provider_signals.get(cid, sig)
        consensus = effective.get("consensus_count", 0)

        if consensus < self._cfg.min_consensus:
            return None

        # No duplicate positions
        pos = await ctx.get_position(cid)
        if pos and (pos.qty_yes > 0 or pos.qty_no > 0):
            return None

        outcome = effective.get("direction", direction)

        # Record entry for stop-loss / take-profit tracking
        self._entries[cid] = {
            "entry_price": price,
            "outcome": outcome,
            "entry_time": trade.published_at,
        }

        return [
            TradeIntent(
                strategy=self.name,
                condition_id=cid,
                side="BUY",
                outcome=outcome,
                size_usd=self._cfg.size_usd,
                urgency="patient",
                max_price=price + 0.02,
                reason=f"insider copy: {consensus} insiders, "
                       f"direction={outcome}, entry={price:.3f}",
                signal_time=trade.published_at,
                asset_id=trade.asset_id,
            ),
        ]

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        """Check take-profit on open positions (earnings/day metric).

        Only fires for slow-pool configs where take_profit_daily_pct > 0.
        """
        if self._cfg.take_profit_daily_pct <= 0:
            return None

        intents: list[TradeIntent] = []
        to_remove: list[str] = []

        for cid, entry in self._entries.items():
            pos = await ctx.get_position(cid)
            if not pos:
                continue
            outcome = entry["outcome"]
            qty = pos.qty_yes if outcome == "YES" else pos.qty_no
            if qty <= 0:
                continue

            current_price = await ctx.get_price(cid, outcome)
            if current_price is None:
                continue

            entry_price = entry["entry_price"]
            days_held = (now - entry["entry_time"]) / 86400.0
            if days_held < self._cfg.min_days_to_take_profit:
                continue

            pnl_pct = (current_price - entry_price) / entry_price
            daily_return = pnl_pct / max(days_held, 0.01)

            if daily_return >= self._cfg.take_profit_daily_pct:
                to_remove.append(cid)
                intents.append(
                    TradeIntent(
                        strategy=self.name,
                        condition_id=cid,
                        side="SELL",
                        outcome=outcome,
                        size_usd=qty * current_price,
                        urgency="patient",
                        max_price=current_price,
                        reason=f"take-profit: {daily_return:.1%}/day "
                               f"over {days_held:.0f}d "
                               f"(threshold {self._cfg.take_profit_daily_pct:.1%})",
                        signal_time=now,
                        asset_id=None,
                    ),
                )

        for cid in to_remove:
            del self._entries[cid]

        return intents if intents else None

    async def on_market_update(
        self, update: object, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    # --- helpers ---

    async def _get_pool(self, ctx: StrategyContext) -> dict[str, Any]:
        feats = await ctx.get_features("insider_copy_provider")
        if isinstance(feats, dict) and "insider_pool" in feats:
            return feats["insider_pool"]
        return {}

    async def _get_provider_signals(self, ctx: StrategyContext) -> dict[str, Any]:
        feats = await ctx.get_features("insider_copy_provider")
        if isinstance(feats, dict) and "insider_signals" in feats:
            return feats["insider_signals"]
        return {}


def create_insider_copy_strategy(config: Any) -> InsiderCopyStrategy:
    """Factory function for CLI registry.

    Reads [strategy.*.params] from TOML and constructs InsiderCopyStrategy.
    """
    params = config.params if hasattr(config, "params") else {}
    cfg = InsiderCopyConfig(
        min_consensus=params.get("min_consensus", 3),
        size_usd=params.get("size_usd", 50.0),
        stop_loss_pct=params.get("stop_loss_pct", 0.50),
        max_entry_price=params.get("max_entry_price", 0.65),
        categories=params.get("categories", []),
        take_profit_daily_pct=params.get("take_profit_daily_pct", 0.0),
        min_days_to_take_profit=params.get("min_days_to_take_profit", 7),
    )
    # Use strategy section name from config for multi-instance support
    name = getattr(config, "name", "s2_insider_copy")
    return InsiderCopyStrategy(cfg, name=name)
