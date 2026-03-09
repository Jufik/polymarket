"""Consensus copy strategy v2 — fires when N pool traders agree on direction.

Designed for tick-by-tick validation via SyncReplayRunner.

Each strategy tracks which pool traders have entered each market and fires
a TradeIntent when the consensus threshold is met. Only BUY trades are
tracked (SELL is ambiguous — could be exit or split-entry on opposite side).

Direction is determined by:
  - BUY of YES token → YES direction
  - BUY of NO token → NO direction
  - SELL trades → ignored

Vol-weighted direction: majority wins by sum USD in each direction.

Usage:
    from research.strategies.consensus_v2 import ConsensusStrategy
    from research.hypotheses.scorecard_v2_strategies.scripts.build_pools import (
        build_politics_composite_pool, build_crypto_hr_pool, build_sports_composite_pool,
    )

    pool, tag_markets, gambling_markets = build_politics_composite_pool()
    strategy = ConsensusStrategy(
        name="politics_composite_k100_n5",
        pool=pool,
        tag_markets=tag_markets,
        gambling_markets=gambling_markets,
        n_threshold=5,
        direction_filter=None,  # YES+NO for Politics
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from polymarket_pipeline.strategies.types import TradeIntent

if TYPE_CHECKING:
    from research.fast_replay import ReplayTick
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import StrategyContext


class ConsensusStrategy:
    """Generic consensus copy strategy — fires when N pool traders agree on direction.

    Parameters
    ----------
    name:
        Strategy identifier (used in TradeIntent and ledger).
    pool:
        Set of trader addresses (lowercase) considered elite.
    tag_markets:
        Set of condition_ids for this strategy's tag (test period).
    gambling_markets:
        Set of condition_ids to exclude from signal generation.
    token_map:
        Mapping condition_id → {"YES": asset_id, "NO": asset_id}.
        Used to determine direction from asset_id.
        If None, falls back to asset_id comparison (less reliable).
    n_threshold:
        Minimum number of distinct pool traders required to fire.
    direction_filter:
        If "YES", only fire on YES-direction consensus.
        If "NO", only fire on NO-direction consensus.
        If None, fire on either direction (vol-weighted majority).
    size_usd:
        Position size in USD per signal.
    max_price:
        Maximum fill price. If None, uses the triggering trade's price + 0.02.
    max_entry_price:
        Reject signals where triggering_price exceeds this threshold.
        Acts as an entry filter — only enters cheap/longshot markets.
        If None, no entry price filtering is applied.
    """

    def __init__(
        self,
        name: str,
        pool: set[str],
        tag_markets: set[str],
        gambling_markets: set[str],
        n_threshold: int,
        direction_filter: str | None = None,
        size_usd: float = 100.0,
        max_price: float | None = None,
        max_entry_price: float | None = None,
    ) -> None:
        self.name = name
        self._pool = {addr.lower() for addr in pool}
        self._tag_markets = tag_markets
        self._gambling_markets = gambling_markets
        self._n_threshold = n_threshold
        self._direction_filter = direction_filter
        self._size_usd = size_usd
        self._max_price = max_price
        self._max_entry_price = max_entry_price

        # State: per condition_id, accumulate pool trader entries
        # {condition_id: {trader: {"YES": usd, "NO": usd}}}
        self._market_state: dict[str, dict[str, dict[str, float]]] = {}
        # {condition_id} — markets we've already fired on (don't fire again)
        self._signaled: set[str] = set()
        # Cache: token_map will be populated on first use from context
        # {asset_id: ("YES" | "NO", condition_id)}
        self._asset_direction_cache: dict[str, tuple[str, str]] = {}

    def _get_direction(self, asset_id: str, condition_id: str, ctx: object) -> str | None:
        """Determine YES/NO direction from asset_id via context token_map."""
        if asset_id in self._asset_direction_cache:
            cached_cid, cached_dir = self._asset_direction_cache[asset_id]
            if cached_cid == condition_id:
                return cached_dir

        # Try to get from context features (token_map may be stored there)
        token_map: dict | None = None
        if hasattr(ctx, "get_features"):
            try:
                import asyncio
                features = asyncio.get_event_loop().run_until_complete(ctx.get_features())
                token_map = features.get("token_map")
            except Exception:
                pass
        if token_map is None and hasattr(ctx, "_features"):
            token_map = ctx._features.get("token_map")

        if token_map and condition_id in token_map:
            cid_tokens = token_map[condition_id]
            yes_asset = cid_tokens.get("YES", "")
            no_asset = cid_tokens.get("NO", "")
            if asset_id == yes_asset:
                direction = "YES"
            elif asset_id == no_asset:
                direction = "NO"
            else:
                return None
            self._asset_direction_cache[asset_id] = (condition_id, direction)
            return direction

        # Fallback: no token_map available — can't determine direction
        return None

    def _get_direction_sync(
        self, asset_id: str, condition_id: str, token_map: dict | None
    ) -> str | None:
        """Sync version of direction lookup — used by on_trade_sync."""
        if asset_id in self._asset_direction_cache:
            cached_cid, cached_dir = self._asset_direction_cache[asset_id]
            if cached_cid == condition_id:
                return cached_dir

        if token_map and condition_id in token_map:
            cid_tokens = token_map[condition_id]
            yes_asset = cid_tokens.get("YES", "")
            no_asset = cid_tokens.get("NO", "")
            if asset_id == yes_asset:
                direction = "YES"
            elif asset_id == no_asset:
                direction = "NO"
            else:
                return None
            self._asset_direction_cache[asset_id] = (condition_id, direction)
            return direction
        return None

    def _maybe_fire(
        self,
        condition_id: str,
        triggering_asset_id: str,
        triggering_price: float,
        signal_time: float,
    ) -> list[TradeIntent] | None:
        """Check consensus and fire if threshold met. Marks market as signaled."""
        if condition_id in self._signaled:
            return None

        state = self._market_state.get(condition_id)
        if state is None:
            return None

        # Aggregate USD by direction across all pool traders in this market
        yes_usd = 0.0
        no_usd = 0.0
        yes_traders: set[str] = set()
        no_traders: set[str] = set()
        for trader, dirs in state.items():
            if dirs.get("YES", 0) > 0:
                yes_traders.add(trader)
                yes_usd += dirs["YES"]
            if dirs.get("NO", 0) > 0:
                no_traders.add(trader)
                no_usd += dirs["NO"]

        # Count distinct traders by their dominant direction
        # A trader can appear in both YES and NO (they traded both) — use their max
        trader_directions: dict[str, str] = {}
        for trader, dirs in state.items():
            yes = dirs.get("YES", 0)
            no = dirs.get("NO", 0)
            if yes > 0 or no > 0:
                trader_directions[trader] = "YES" if yes >= no else "NO"

        n_yes = sum(1 for d in trader_directions.values() if d == "YES")
        n_no = sum(1 for d in trader_directions.values() if d == "NO")
        n_total = len(trader_directions)

        if n_total < self._n_threshold:
            return None

        # Vol-weighted direction
        consensus_dir = "YES" if yes_usd >= no_usd else "NO"

        # Apply direction filter
        if self._direction_filter is not None and consensus_dir != self._direction_filter:
            return None

        # Entry price filter — reject signals where market is too expensive
        if self._max_entry_price is not None and triggering_price > self._max_entry_price:
            return None

        # Fire signal
        self._signaled.add(condition_id)

        # Determine outcome and asset for the intent
        outcome = consensus_dir
        if outcome == "YES":
            # Find YES asset_id for this market from cache
            asset_id = self._find_asset_id(condition_id, "YES", triggering_asset_id)
        else:
            asset_id = self._find_asset_id(condition_id, "NO", triggering_asset_id)

        max_price = self._max_price if self._max_price is not None else min(triggering_price + 0.02, 0.99)

        n_traders_str = f"yes={n_yes},no={n_no},total={n_total}"
        return [
            TradeIntent(
                strategy=self.name,
                condition_id=condition_id,
                side="BUY",
                outcome=outcome,
                size_usd=self._size_usd,
                urgency="patient",
                max_price=max_price,
                reason=f"consensus:{n_traders_str}|dir={consensus_dir}|yes_usd={yes_usd:.1f}",
                signal_time=signal_time,
                asset_id=asset_id,
            )
        ]

    def _find_asset_id(
        self, condition_id: str, direction: str, fallback_asset_id: str
    ) -> str:
        """Find asset_id for a given direction from cache."""
        for asset_id, (cid, dir_) in self._asset_direction_cache.items():
            if cid == condition_id and dir_ == direction:
                return asset_id
        return fallback_asset_id

    def on_trade_sync(
        self,
        trade: ReplayTick,
        ctx: object,
    ) -> list[TradeIntent] | None:
        """Synchronous hot-path called by SyncReplayRunner."""
        condition_id = trade.condition_id

        # Gate 1: must be in our tag markets
        if condition_id not in self._tag_markets:
            return None

        # Gate 2: must not be a gambling market
        if condition_id in self._gambling_markets:
            return None

        # Gate 3: already signaled
        if condition_id in self._signaled:
            return None

        # Gate 4: must be a BUY (SELL is ambiguous — skip)
        if trade.side != "BUY":
            return None

        # Gate 5: must be a pool trader
        maker = trade.maker
        if maker is None:
            return None
        maker_lower = maker.lower()
        if maker_lower not in self._pool:
            return None

        # Determine direction from asset_id
        token_map: dict | None = getattr(ctx, "_features", {}).get("token_map") if hasattr(ctx, "_features") else None
        direction = self._get_direction_sync(trade.asset_id, condition_id, token_map)
        if direction is None:
            # Can't determine direction — skip
            return None

        # Accumulate state
        if condition_id not in self._market_state:
            self._market_state[condition_id] = {}
        if maker_lower not in self._market_state[condition_id]:
            self._market_state[condition_id][maker_lower] = {"YES": 0.0, "NO": 0.0}
        self._market_state[condition_id][maker_lower][direction] += float(trade.amount_usd)

        # Check consensus
        return self._maybe_fire(
            condition_id,
            trade.asset_id,
            float(trade.price),
            trade.published_at,
        )

    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: StrategyContext,
    ) -> list[TradeIntent] | None:
        """Async fallback — delegates to sync version."""
        return self.on_trade_sync(trade, ctx)

    async def on_market_update(
        self, update: object, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None


class TokenMapStrategy(ConsensusStrategy):
    """ConsensusStrategy with a pre-loaded token_map for direction resolution.

    Pass token_map at construction time so direction lookup works without
    relying on context features (which may not be populated in replay).

    token_map format: {condition_id: {"YES": asset_id, "NO": asset_id}}
    """

    def __init__(
        self,
        name: str,
        pool: set[str],
        tag_markets: set[str],
        gambling_markets: set[str],
        n_threshold: int,
        token_map: dict[str, dict[str, str]],
        direction_filter: str | None = None,
        size_usd: float = 100.0,
        max_price: float | None = None,
        max_entry_price: float | None = None,
    ) -> None:
        super().__init__(
            name=name,
            pool=pool,
            tag_markets=tag_markets,
            gambling_markets=gambling_markets,
            n_threshold=n_threshold,
            direction_filter=direction_filter,
            size_usd=size_usd,
            max_price=max_price,
            max_entry_price=max_entry_price,
        )
        # Pre-populate asset_direction_cache from token_map
        for cid, outcomes in token_map.items():
            for outcome, asset_id in outcomes.items():
                if outcome in ("YES", "NO") and asset_id:
                    self._asset_direction_cache[asset_id] = (cid, outcome)
