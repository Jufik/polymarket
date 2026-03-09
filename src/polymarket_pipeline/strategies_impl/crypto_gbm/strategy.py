"""Crypto GBM mid-window repricing strategy.

Watches BTC exchange prices during Polymarket's "Up or Down" windows.
When GBM fair value diverges from PM orderbook price by > threshold,
buys the underpriced side.
"""

from __future__ import annotations

from typing import Any

import structlog

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.strategies.types import TradeIntent
from polymarket_pipeline.strategies_impl.crypto_gbm.config import CryptoGBMConfig
from polymarket_pipeline.strategies_impl.crypto_gbm.gbm import compute_gbm_p_up
from polymarket_pipeline.strategies_impl.crypto_gbm.window import WindowInfo

log = structlog.get_logger()


class CryptoGBMStrategy:
    """GBM mid-window repricing — signal via on_timer(), not on_trade()."""

    name: str = "crypto_gbm"

    def __init__(self, config: CryptoGBMConfig) -> None:
        self._cfg = config
        self._signaled: set[str] = set()  # condition_ids already traded this window

    # ── Strategy protocol ─────────────────────────────────────────────────

    async def on_trade(
        self, trade: NormalizedTrade, ctx: Any
    ) -> list[TradeIntent] | None:
        return None

    async def on_market_update(
        self, update: Any, ctx: Any
    ) -> list[TradeIntent] | None:
        return None

    async def on_timer(
        self, now: float, ctx: Any
    ) -> list[TradeIntent] | None:
        """Primary signal path. Called every ~5 seconds."""
        windows: dict[str, WindowInfo] = await ctx.get_features("crypto_windows") or {}
        s0_prices: dict[str, float] = await ctx.get_features("crypto_window_s0") or {}
        btc_price: float = await ctx.get_features("exchange_btc_price") or 0.0
        sigma: float | None = await ctx.get_features("exchange_btc_sigma")

        if btc_price <= 0 or sigma is None or sigma <= self._cfg.min_sigma:
            return None

        intents: list[TradeIntent] = []

        for cid, window in windows.items():
            if cid in self._signaled:
                continue

            # Capture S₀ at window open
            if cid not in s0_prices:
                if window.is_active and window.minutes_elapsed < 0.2:
                    # Window just opened — latch current exchange price as S₀
                    s0_provider = await ctx.get_features("crypto_window_s0")
                    if s0_provider is not None and cid not in s0_provider:
                        self._record_s0(ctx, cid, btc_price)
                    continue
                elif not window.is_active and window.minutes_remaining > 0:
                    # Window hasn't opened yet
                    continue
                else:
                    # Missed the open, skip
                    continue

            if not window.is_active:
                continue

            s0 = s0_prices.get(cid, 0.0)
            if s0 <= 0:
                continue

            elapsed = window.minutes_elapsed
            remaining = window.minutes_remaining

            if self._cfg.skip_minute_zero and elapsed < 1.0:
                continue
            if remaining < self._cfg.min_time_remaining_min:
                continue

            # GBM fair value
            gbm_p_up = compute_gbm_p_up(s0, btc_price, sigma, remaining)

            if abs(gbm_p_up - 0.5) < self._cfg.min_gbm_deviation:
                continue  # BTC hasn't moved enough

            # PM price from orderbook
            pm_p_up = await self._get_pm_p_up(ctx, window)
            if pm_p_up is None:
                continue

            # Spread filter
            ob = await ctx.get_orderbook(cid)
            if ob is not None and ob.spread > self._cfg.max_spread:
                continue

            lag = gbm_p_up - pm_p_up

            if lag > self._cfg.threshold:
                intent = TradeIntent(
                    strategy=self.name,
                    condition_id=cid,
                    side="BUY",
                    outcome="YES",
                    size_usd=self._cfg.base_bet_usd,
                    urgency="immediate",
                    max_price=min(pm_p_up + 0.02, 0.95),
                    reason=(
                        f"gbm={gbm_p_up:.3f} pm={pm_p_up:.3f} lag={lag:.3f} "
                        f"min={elapsed:.1f}/{window.duration_min} "
                        f"s0={s0:.2f} st={btc_price:.2f} σ={sigma:.6f}"
                    ),
                    signal_time=now,
                    asset_id=window.token_yes,
                )
                intents.append(intent)
                self._signaled.add(cid)
                log.info(
                    "crypto_gbm.signal",
                    side="UP",
                    cid=cid[:16],
                    gbm=f"{gbm_p_up:.3f}",
                    pm=f"{pm_p_up:.3f}",
                    lag=f"{lag:.3f}",
                    minute=f"{elapsed:.1f}",
                )

            elif lag < -self._cfg.threshold:
                pm_p_down = 1.0 - pm_p_up
                intent = TradeIntent(
                    strategy=self.name,
                    condition_id=cid,
                    side="BUY",
                    outcome="NO",
                    size_usd=self._cfg.base_bet_usd,
                    urgency="immediate",
                    max_price=min(pm_p_down + 0.02, 0.95),
                    reason=(
                        f"gbm={gbm_p_up:.3f} pm={pm_p_up:.3f} lag={lag:.3f} "
                        f"min={elapsed:.1f}/{window.duration_min} "
                        f"s0={s0:.2f} st={btc_price:.2f} σ={sigma:.6f}"
                    ),
                    signal_time=now,
                    asset_id=window.token_no,
                )
                intents.append(intent)
                self._signaled.add(cid)
                log.info(
                    "crypto_gbm.signal",
                    side="DOWN",
                    cid=cid[:16],
                    gbm=f"{gbm_p_up:.3f}",
                    pm=f"{pm_p_up:.3f}",
                    lag=f"{lag:.3f}",
                    minute=f"{elapsed:.1f}",
                )

        return intents if intents else None

    # ── Helpers ───────────────────────────────────────────────────────────

    def _record_s0(self, ctx: Any, cid: str, price: float) -> None:
        """Inject S₀ into the CryptoWindowProvider."""
        for provider in getattr(ctx, "_providers", []):
            if hasattr(provider, "record_s0"):
                provider.record_s0(cid, price)
                log.info("crypto_gbm.s0_captured", cid=cid[:16], s0=f"{price:.2f}")
                return
        # Fallback: store in ctx features directly
        s0_map = ctx._features.get("crypto_window_s0")
        if isinstance(s0_map, dict):
            s0_map[cid] = price
            log.info("crypto_gbm.s0_captured", cid=cid[:16], s0=f"{price:.2f}")

    async def _get_pm_p_up(self, ctx: Any, window: WindowInfo) -> float | None:
        """Derive P(Up) from PM orderbook data."""
        # Try UP token orderbook first
        ob_up = await ctx.get_orderbook(window.condition_id)
        if ob_up is not None and ob_up.best_ask > 0:
            return ob_up.best_ask

        # Try price from context
        price = await ctx.get_price(window.condition_id, "YES")
        if price is not None and price > 0:
            return price

        return None
