"""Consensus copy strategy for tick-by-tick validation.

Signal: Fire BUY YES when N distinct qualified traders have entered YES positions
in the same market within the test window (phantom filter: first_trade >= test_start).

For Tennis, an optional time window W limits how long consensus can form
(max(first_trade) - min(first_trade) <= W hours).

BUY-only variant: SELL trades are ignored entirely (not directional signals).
"""

from __future__ import annotations

from polymarket_pipeline.strategies.types import TradeIntent

# YES asset_ids for the qualified YES token in each market
# Filled externally before the replay run.
_YES_ASSET_IDS: dict[str, str] = {}   # condition_id -> yes_asset_id


class ConsensusStrategy:
    """Tick-by-tick consensus copy strategy.

    Parameters
    ----------
    qualified_traders : set[str]
        Traders who passed pool qualification in the training window.
    yes_asset_ids : dict[str, str]
        Maps condition_id -> YES asset_id (for signal routing).
    consensus_n : int
        Number of distinct qualified traders required to fire.
    price_ceil : float
        Maximum entry price — skip signal if avg qualified entry price > price_ceil.
    window_hours : float | None
        Tennis: max hours from first to last qualified entry. None = no limit.
    test_start_epoch : float
        Phantom filter — only count trades after this timestamp (epoch seconds).
    size_usd : float
        Position size in USD per signal.
    """

    name: str = "consensus_copy"

    def __init__(
        self,
        qualified_traders: set[str],
        yes_asset_ids: dict[str, str],
        consensus_n: int = 5,
        price_ceil: float = 1.0,
        window_hours: float | None = None,
        test_start_epoch: float = 0.0,
        size_usd: float = 100.0,
    ) -> None:
        self._qualified = qualified_traders
        self._yes_asset_ids = yes_asset_ids
        self._n = consensus_n
        self._price_ceil = price_ceil
        self._window_s = window_hours * 3600 if window_hours else None
        self._test_start = test_start_epoch
        self._size_usd = size_usd

        # Per-market state
        # traders: condition_id -> set of qualified traders who entered
        self._traders: dict[str, set[str]] = {}
        # timestamps: condition_id -> list of entry timestamps
        self._timestamps: dict[str, list[float]] = {}
        # fired: condition_id -> True once signal emitted (no re-entry)
        self._fired: set[str] = set()

    def reset(self) -> None:
        """Reset per-market state between folds."""
        self._traders.clear()
        self._timestamps.clear()
        self._fired.clear()

    async def on_trade(self, trade: object, ctx: object) -> list[TradeIntent] | None:
        # BUY-only: ignore SELL trades entirely
        if trade.side != "BUY":  # type: ignore[union-attr]
            return None

        cid = trade.condition_id  # type: ignore[union-attr]
        maker = trade.maker  # type: ignore[union-attr]
        ts = trade.published_at  # type: ignore[union-attr]

        # Phantom filter: only count entries in test window
        if ts < self._test_start:
            return None

        # Only count qualified makers
        if not maker or maker not in self._qualified:
            return None

        # Skip if already fired for this market
        if cid in self._fired:
            return None

        # Only count YES-side trades (asset_id match)
        expected_yes = self._yes_asset_ids.get(cid)
        if expected_yes and trade.asset_id != expected_yes:  # type: ignore[union-attr]
            return None

        # Accumulate consensus
        if cid not in self._traders:
            self._traders[cid] = set()
            self._timestamps[cid] = []

        traders = self._traders[cid]
        timestamps = self._timestamps[cid]

        if maker not in traders:
            traders.add(maker)
            timestamps.append(ts)

        # Check consensus threshold
        if len(traders) < self._n:
            return None

        # Check time window (Tennis)
        if self._window_s is not None and len(timestamps) >= 2:
            window_elapsed = max(timestamps) - min(timestamps)
            if window_elapsed > self._window_s:
                # Window exceeded — reset this market's consensus
                self._traders[cid] = set()
                self._timestamps[cid] = []
                return None

        # Check signal-level price ceiling
        signal_price = trade.price  # type: ignore[union-attr]
        if signal_price > self._price_ceil:
            return None

        # Fire signal
        self._fired.add(cid)

        yes_asset_id = self._yes_asset_ids.get(cid, "")

        return [
            TradeIntent(
                strategy=self.name,
                condition_id=cid,
                side="BUY",
                outcome="YES",
                size_usd=self._size_usd,
                urgency="patient",
                max_price=min(signal_price + 0.02, self._price_ceil),
                reason=f"consensus_n={self._n} traders={len(traders)}",
                signal_time=ts,
                asset_id=yes_asset_id,
            )
        ]

    async def on_market_update(self, update: object, ctx: object) -> list[TradeIntent] | None:
        return None

    async def on_timer(self, now: float, ctx: object) -> list[TradeIntent] | None:
        return None
