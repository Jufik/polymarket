"""S2 Insider Copy Strategy — copy high-conviction insider BUY trades.

Identifies traders with abnormally high hit rates on insider-susceptible
markets (politics, regulatory, corporate) and mirrors their entries.

==========================================================================
VALIDATED RESULTS (per-tag tick-by-tick, 2026-03-02)
==========================================================================

    Tag                 | HR     | NO excess | Gap  | Verdict
    --------------------+--------+-----------+------+-----------
    Sports              | 74.3%  | +13.5pp   |  4pp | GO
    Politics+Other      | 69.3%  |  -5.1pp   |  --  | CONDITIONAL
    Culture+Weather+Fin | 66-72% |  varies   |  --  | CONDITIONAL

    Base rate: ~62% NO. Vectorized-to-tick gap: 4-29pp (expected 20-40pp).

    EXCLUDED: crypto (negative excess HR), esports (near-zero PnL).

    NOTE: Entry price filter < 0.65 confirmed SUBOPTIMAL per-category.
    Lowers HR 8-10pp everywhere. Removed from all pools.

==========================================================================
THREE-POOL DEPLOYMENT (per-tag tuned, configs/s2_insider_copy.toml)
==========================================================================

    [strategy.s2_insider_sports]   → consensus >= 4, hold to resolution
    [strategy.s2_insider_politics] → consensus >= 3, take-profit 2%/day
    [strategy.s2_insider_misc]     → consensus >= 2, take-profit 2%/day

    Capital: sports $600, politics $250, misc $150 (total $1000).
    All pools: size_usd = $10, stop_loss = 50%, no entry price filter.

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
CATEGORY SAFETY
==========================================================================

    Hardcoded _EXCLUDED_CATEGORIES = {"crypto", "esports"} — belt-and-suspenders
    on top of TOML categories filter. Crypto has negative excess HR and produces
    only losses (confirmed: 20/23 losses in first deployment were crypto 5-min).
"""

from __future__ import annotations

import time as _time
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
    max_entry_price: float = 1.00
    categories: list[str] = field(default_factory=list)
    take_profit_daily_pct: float = 0.0
    min_days_to_take_profit: int = 7


# Categories that must NEVER be traded — hardcoded safety net on top of
# TOML config.  These have negative excess HR and produce only losses.
_EXCLUDED_CATEGORIES = frozenset({"crypto", "esports"})


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
        self._allowed_categories: frozenset[str] | None = (
            frozenset(cfg.categories) if cfg.categories else None
        )
        self._entries: dict[str, dict[str, Any]] = {}
        self._signals: dict[str, dict[str, Any]] = {}
        self._debug_counters: dict[str, int] = {
            "total": 0, "not_buy": 0, "price_gate": 0, "no_pool": 0,
            "not_insider": 0, "low_consensus": 0, "dup_position": 0,
            "category_blocked": 0, "emitted": 0,
        }
        self._debug_last_log: float = 0.0

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
                    # No reliable price for our outcome — skip stop-loss check
                    return None

                if current_price < entry_price * (1 - self._cfg.stop_loss_pct):
                    qty = pos.qty_yes if outcome == "YES" else pos.qty_no
                    entry_asset_id = entry.get("asset_id")
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
                            asset_id=entry_asset_id,
                        ),
                    ]
            return None

        # --- Debug: periodic summary every 60s ---
        self._debug_counters["total"] += 1
        now_mono = _time.monotonic()
        if now_mono - self._debug_last_log > 60.0:
            self._debug_last_log = now_mono
            logger.warning(
                "strategy.debug_filter_stats",
                strategy=self.name,
                min_consensus=self._cfg.min_consensus,
                max_entry_price=self._cfg.max_entry_price,
                signals_tracked=len(self._signals),
                top_consensus=max(
                    (s.get("consensus_count", 0) for s in self._signals.values()),
                    default=0,
                ),
                **self._debug_counters,
            )

        # --- Entry logic: only BUY trades from insiders ---
        if trade.side != "BUY":
            self._debug_counters["not_buy"] += 1
            return None

        # Entry price gate (disabled by default — set to 1.00 per research)
        if price >= self._cfg.max_entry_price:
            self._debug_counters["price_gate"] += 1
            return None

        # Look up insider pool from provider features
        pool = await self._get_pool(ctx)
        if not pool:
            self._debug_counters["no_pool"] += 1
            return None

        maker = (trade.maker or "").lower()
        if maker not in pool:
            self._debug_counters["not_insider"] += 1
            return None

        # --- Category gate: exclude crypto/esports + enforce TOML categories ---
        market_categories = await self._get_market_categories(ctx)
        category = market_categories.get(cid, "unknown")
        if category in _EXCLUDED_CATEGORIES:
            self._debug_counters["category_blocked"] += 1
            return None
        if self._allowed_categories and category not in self._allowed_categories:
            self._debug_counters["category_blocked"] += 1
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
            self._debug_counters["low_consensus"] += 1
            return None

        # No duplicate positions
        pos = await ctx.get_position(cid)
        if pos and (pos.qty_yes > 0 or pos.qty_no > 0):
            self._debug_counters["dup_position"] += 1
            return None

        outcome = effective.get("direction", direction)
        self._debug_counters["emitted"] += 1
        logger.warning(
            "strategy.INTENT_EMITTED",
            strategy=self.name,
            condition_id=cid,
            consensus=consensus,
            price=price,
            outcome=outcome,
        )

        # Record entry for stop-loss / take-profit tracking
        self._entries[cid] = {
            "entry_price": price,
            "outcome": outcome,
            "entry_time": trade.published_at,
            "asset_id": trade.asset_id,
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
                        asset_id=entry.get("asset_id"),
                    ),
                )

        for cid in to_remove:
            del self._entries[cid]

        return intents if intents else None

    async def on_market_update(
        self, update: object, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        # Clean up internal state when a market resolves
        if isinstance(update, dict) and update.get("type") == "resolution":
            cid = update.get("condition_id")
            if cid:
                self._entries.pop(cid, None)
                self._signals.pop(cid, None)
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

    async def _get_market_categories(self, ctx: StrategyContext) -> dict[str, str]:
        feats = await ctx.get_features("insider_copy_provider")
        if isinstance(feats, dict) and "market_categories" in feats:
            return feats["market_categories"]
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
        max_entry_price=params.get("max_entry_price", 1.00),
        categories=params.get("categories", []),
        take_profit_daily_pct=params.get("take_profit_daily_pct", 0.0),
        min_days_to_take_profit=params.get("min_days_to_take_profit", 7),
    )
    return InsiderCopyStrategy(cfg, name=config.name)
