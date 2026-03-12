"""Crypto GBM scalp strategy.

Buys the underpriced PM side when GBM fair value diverges from PM price
by > threshold. Sells when PM converges (scalp) or near window end (time-stop).

Modes:
  scalp_enabled=True  → exit on convergence (default, validated at +4.2% median)
  scalp_enabled=False → hold to resolution (original behavior)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.strategies.types import TradeIntent
from polymarket_pipeline.strategies_impl.crypto_gbm.config import CryptoGBMConfig
from polymarket_pipeline.strategies_impl.crypto_gbm.gbm import compute_gbm_p_up
from polymarket_pipeline.strategies_impl.crypto_gbm.window import WindowInfo

log = structlog.get_logger()


@dataclass
class _OpenPosition:
    """Tracks an open scalp position for exit logic."""

    condition_id: str
    outcome: str  # "YES" or "NO"
    asset_id: str
    entry_price: float
    entry_time: float
    entry_gbm: float


class CryptoGBMStrategy:
    """GBM mid-window repricing with scalp exits."""

    name: str = "crypto_gbm"

    def __init__(self, config: CryptoGBMConfig) -> None:
        self._cfg = config
        self._signaled: set[str] = set()  # condition_ids already traded this window
        self._positions: dict[str, _OpenPosition] = {}  # cid → open position

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

        # Debug: periodic state dump (every ~60s = 12 timer ticks)
        self._timer_count = getattr(self, "_timer_count", 0) + 1
        if self._timer_count % 12 == 1:
            ob_count = len(getattr(ctx, "_orderbooks", {}))
            ob_asset_count = len(getattr(ctx, "_orderbooks_by_asset", {}))
            log.warning(
                "crypto_gbm.debug_state",
                windows=len(windows),
                s0_tracked=len(s0_prices),
                btc_price=f"{btc_price:.2f}",
                sigma=f"{sigma:.8f}",
                signaled=len(self._signaled),
                positions=len(self._positions),
                ctx_orderbooks=ob_count,
                ctx_orderbooks_by_asset=ob_asset_count,
            )

        intents: list[TradeIntent] = []

        # ── Check exits first (scalp mode) ──────────────────────────────
        if self._cfg.scalp_enabled:
            exit_intents = await self._check_exits(now, windows, s0_prices, btc_price, sigma, ctx)
            intents.extend(exit_intents)

        # ── Check entries ────────────────────────────────────────────────
        for cid, window in windows.items():
            if cid in self._signaled:
                continue

            # Capture S₀ at window open
            if cid not in s0_prices:
                if window.is_active and window.minutes_elapsed < 0.2:
                    self._record_s0(ctx, cid, btc_price)
                    continue
                elif not window.is_active and window.minutes_remaining > 0:
                    continue
                else:
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

            # Don't enter too close to window end (scalp needs time to exit)
            if self._cfg.scalp_enabled:
                remaining_s = remaining * 60
                if remaining_s < self._cfg.no_entry_within_s:
                    continue

            # GBM fair value
            gbm_p_up = compute_gbm_p_up(s0, btc_price, sigma, remaining)

            if abs(gbm_p_up - 0.5) < self._cfg.min_gbm_deviation:
                continue

            # PM price from orderbook
            pm_p_up = await self._get_pm_p_up(ctx, window)
            if pm_p_up is None:
                continue

            # Spread filter
            ob = await ctx.get_orderbook(cid)
            if ob is not None and ob.spread > self._cfg.max_spread:
                continue

            lag = gbm_p_up - pm_p_up

            # Debug: log every evaluation (throttled via timer_count)
            if getattr(self, "_timer_count", 0) % 12 == 1:
                log.warning(
                    "crypto_gbm.eval",
                    cid=cid[:16],
                    gbm=f"{gbm_p_up:.3f}",
                    pm=f"{pm_p_up:.3f}",
                    lag=f"{lag:.3f}",
                    threshold=self._cfg.threshold,
                    elapsed=f"{elapsed:.1f}",
                    remaining=f"{remaining:.1f}",
                    spread=f"{ob.spread:.4f}" if ob else "no_ob",
                )

            if lag > self._cfg.threshold:
                # PM underprices UP → buy YES
                entry_price = min(pm_p_up + 0.02, 0.95)
                intent = TradeIntent(
                    strategy=self.name,
                    condition_id=cid,
                    side="BUY",
                    outcome="YES",
                    size_usd=self._cfg.base_bet_usd,
                    urgency="immediate",
                    max_price=entry_price,
                    reason=(
                        f"scalp gbm={gbm_p_up:.3f} pm={pm_p_up:.3f} lag={lag:.3f} "
                        f"min={elapsed:.1f}/{window.duration_min} "
                        f"s0={s0:.2f} st={btc_price:.2f} σ={sigma:.6f}"
                    ),
                    signal_time=now,
                    asset_id=window.token_yes,
                )
                intents.append(intent)
                self._signaled.add(cid)
                self._positions[cid] = _OpenPosition(
                    condition_id=cid,
                    outcome="YES",
                    asset_id=window.token_yes,
                    entry_price=entry_price,
                    entry_time=now,
                    entry_gbm=gbm_p_up,
                )
                log.info(
                    "crypto_gbm.entry",
                    side="UP/YES",
                    cid=cid[:16],
                    gbm=f"{gbm_p_up:.3f}",
                    pm=f"{pm_p_up:.3f}",
                    lag=f"{lag:.3f}",
                    minute=f"{elapsed:.1f}",
                )

            elif lag < -self._cfg.threshold:
                # PM underprices DOWN → buy NO
                pm_p_down = 1.0 - pm_p_up
                entry_price = min(pm_p_down + 0.02, 0.95)
                intent = TradeIntent(
                    strategy=self.name,
                    condition_id=cid,
                    side="BUY",
                    outcome="NO",
                    size_usd=self._cfg.base_bet_usd,
                    urgency="immediate",
                    max_price=entry_price,
                    reason=(
                        f"scalp gbm={gbm_p_up:.3f} pm={pm_p_up:.3f} lag={lag:.3f} "
                        f"min={elapsed:.1f}/{window.duration_min} "
                        f"s0={s0:.2f} st={btc_price:.2f} σ={sigma:.6f}"
                    ),
                    signal_time=now,
                    asset_id=window.token_no,
                )
                intents.append(intent)
                self._signaled.add(cid)
                self._positions[cid] = _OpenPosition(
                    condition_id=cid,
                    outcome="NO",
                    asset_id=window.token_no,
                    entry_price=entry_price,
                    entry_time=now,
                    entry_gbm=gbm_p_up,
                )
                log.info(
                    "crypto_gbm.entry",
                    side="DOWN/NO",
                    cid=cid[:16],
                    gbm=f"{gbm_p_up:.3f}",
                    pm=f"{pm_p_up:.3f}",
                    lag=f"{lag:.3f}",
                    minute=f"{elapsed:.1f}",
                )

        # Clean up expired windows
        self._cleanup_expired(windows)

        return intents if intents else None

    # ── Scalp exit logic ───────────────────────────────────────────────

    async def _check_exits(
        self,
        now: float,
        windows: dict[str, WindowInfo],
        s0_prices: dict[str, float],
        btc_price: float,
        sigma: float,
        ctx: Any,
    ) -> list[TradeIntent]:
        """Check open positions for convergence (take-profit) or time-stop exits.

        Convergence = the gap between GBM fair value and PM price has shrunk
        below exit_threshold. Since we entered when the gap was > threshold,
        PM catching up means our position is in profit.
        """
        exits: list[TradeIntent] = []
        to_close: list[str] = []

        for cid, pos in self._positions.items():
            window = windows.get(cid)
            if window is None:
                to_close.append(cid)
                continue

            if not window.is_active:
                to_close.append(cid)
                continue

            s0 = s0_prices.get(cid, 0.0)
            if s0 <= 0:
                continue

            remaining = window.minutes_remaining
            remaining_s = remaining * 60

            # Current GBM fair value
            gbm_p_up = compute_gbm_p_up(s0, btc_price, sigma, remaining)

            # Current PM price (the actual market price, not a proxy)
            pm_p_up = await self._get_pm_p_up(ctx, window)

            # Time-stop: exit if < 30s remaining (regardless of PM price)
            if remaining_s < self._cfg.exit_min_time_remaining_s:
                exit_intent = await self._make_exit_intent(pos, now, ctx,
                    f"time_stop remaining={remaining_s:.0f}s pm={pm_p_up}")
                if exit_intent:
                    exits.append(exit_intent)
                to_close.append(cid)
                log.info(
                    "crypto_gbm.exit_time_stop",
                    cid=cid[:16],
                    outcome=pos.outcome,
                    remaining_s=f"{remaining_s:.0f}",
                    hold_s=f"{now - pos.entry_time:.0f}",
                    pm=f"{pm_p_up:.3f}" if pm_p_up else "none",
                )
                continue

            if pm_p_up is None:
                continue

            # Convergence check: has the gap between GBM and PM shrunk?
            # Entry was: |gbm - pm| > threshold  (e.g. 0.15)
            # Exit when: |gbm - pm| < exit_threshold  (e.g. 0.02)
            # This means PM has caught up → our token appreciated → take profit
            current_lag = abs(gbm_p_up - pm_p_up)

            # Debug (throttled)
            if getattr(self, "_timer_count", 0) % 12 == 1:
                log.info(
                    "crypto_gbm.exit_eval",
                    cid=cid[:16],
                    outcome=pos.outcome,
                    entry=f"{pos.entry_price:.3f}",
                    gbm=f"{gbm_p_up:.3f}",
                    pm=f"{pm_p_up:.3f}",
                    lag=f"{current_lag:.3f}",
                    exit_thresh=self._cfg.exit_threshold,
                    hold_s=f"{now - pos.entry_time:.0f}",
                )

            if current_lag < self._cfg.exit_threshold:
                exit_intent = await self._make_exit_intent(pos, now, ctx,
                    f"take_profit lag={current_lag:.3f} gbm={gbm_p_up:.3f} "
                    f"pm={pm_p_up:.3f} entry={pos.entry_price:.3f}")
                if exit_intent:
                    exits.append(exit_intent)
                to_close.append(cid)
                log.info(
                    "crypto_gbm.exit_take_profit",
                    cid=cid[:16],
                    outcome=pos.outcome,
                    entry=f"{pos.entry_price:.3f}",
                    pm=f"{pm_p_up:.3f}",
                    gbm=f"{gbm_p_up:.3f}",
                    lag=f"{current_lag:.3f}",
                    hold_s=f"{now - pos.entry_time:.0f}",
                )

        for cid in to_close:
            self._positions.pop(cid, None)

        return exits

    async def _make_exit_intent(
        self, pos: _OpenPosition, now: float, ctx: Any, reason: str
    ) -> TradeIntent | None:
        """Create a SELL intent with resolved position size."""
        # Look up actual position to get size_usd
        position = await ctx.get_position(pos.condition_id)
        if position is None:
            log.debug("crypto_gbm.exit_no_position", cid=pos.condition_id[:16])
            return None
        qty = position.qty_yes if pos.outcome == "YES" else position.qty_no
        if qty <= 0:
            log.debug("crypto_gbm.exit_zero_qty", cid=pos.condition_id[:16])
            return None
        avg_entry = (
            position.avg_entry_yes if pos.outcome == "YES"
            else position.avg_entry_no
        )
        return TradeIntent(
            strategy=self.name,
            condition_id=pos.condition_id,
            side="SELL",
            outcome=pos.outcome,
            size_usd=qty * max(avg_entry, 0.01),
            urgency="immediate",
            max_price=0.01,  # sell at any price
            reason=f"scalp_exit {reason}",
            signal_time=now,
            asset_id=pos.asset_id,
        )

    def _cleanup_expired(self, active_windows: dict[str, WindowInfo]) -> None:
        """Remove signaled markers for windows no longer active."""
        active_cids = set(active_windows.keys())
        expired = [cid for cid in self._signaled if cid not in active_cids]
        for cid in expired:
            self._signaled.discard(cid)
            self._positions.pop(cid, None)

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
        # 1. Try orderbook by asset_id (YES token — most specific)
        if hasattr(ctx, "get_orderbook_by_asset"):
            ob_yes = await ctx.get_orderbook_by_asset(window.token_yes)
            if ob_yes is not None and ob_yes.best_ask > 0:
                return ob_yes.best_ask

        # 2. Try orderbook by condition_id
        ob_up = await ctx.get_orderbook(window.condition_id)
        if ob_up is not None and ob_up.best_ask > 0:
            return ob_up.best_ask

        # 3. Try context price
        price = await ctx.get_price(window.condition_id, "YES")
        if price is not None and price > 0:
            return price

        # 4. CLOB REST API fallback
        pm_price = await self._fetch_clob_price(window.token_yes)
        if pm_price is not None:
            return pm_price

        # Debug: log failure details (throttled)
        self._pm_miss_count = getattr(self, "_pm_miss_count", 0) + 1
        if self._pm_miss_count <= 5 or self._pm_miss_count % 50 == 0:
            log.warning(
                "crypto_gbm.pm_price_miss",
                cid=window.condition_id[:16],
                token_yes=window.token_yes[:16] if window.token_yes else "none",
                miss_count=self._pm_miss_count,
            )
        return None

    async def _fetch_clob_price(self, token_id: str) -> float | None:
        """Fetch price from CLOB REST API (fallback when orderbook not in ctx)."""
        if not token_id:
            return None
        if not hasattr(self, "_http"):
            import httpx

            self._http = httpx.AsyncClient(
                base_url="https://clob.polymarket.com",
                timeout=5.0,
            )
        try:
            resp = await self._http.get(
                "/price",
                params={"token_id": token_id, "side": "BUY"},
            )
            resp.raise_for_status()
            price_str = resp.json().get("price")
            if price_str:
                return float(price_str)
        except Exception:
            log.debug("crypto_gbm.clob_price_failed", token_id=token_id[:16])
        return None
