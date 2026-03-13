"""ConsensusV2Strategy -- fire when N composite-ranked pool traders agree on direction."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pm_strategy.types import TradeIntent

if TYPE_CHECKING:
    from pm_core.models import NormalizedTrade

    from pm_strategy.config import StrategyConfig
    from pm_strategy.protocols import StrategyContext

logger = logging.getLogger(__name__)


class ConsensusV2Strategy:
    """Consensus copy strategy -- fires when N pool traders agree on direction."""

    def __init__(
        self,
        cfg: StrategyConfig,
        n_threshold: int = 3,
        direction_filter: str | None = "YES",
        size_usd: float | None = None,
        max_price: float | None = None,
        max_entry_price: float | None = None,
        **kwargs: Any,
    ) -> None:
        self.name: str = cfg.name
        self._cfg = cfg
        self._n_threshold = n_threshold
        self._direction_filter = direction_filter
        self._size_usd = size_usd or cfg.max_position_usd
        self._max_price = max_price
        self._max_entry_price = max_entry_price
        self._feature_key = cfg.features[0] if cfg.features else "consensus_v2"

        self._market_state: dict[str, dict[str, dict[str, float]]] = {}
        self._signaled: set[str] = set()

    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: StrategyContext,
    ) -> list[TradeIntent] | None:
        condition_id = trade.condition_id

        if condition_id in self._signaled:
            return None

        if trade.side != "BUY":
            return None

        maker = trade.maker
        if not maker:
            return None
        maker_lower = maker.lower()

        features = await ctx.get_features(self._feature_key)
        if features is None:
            return None

        pool: frozenset[str] = features.get("pool", frozenset())
        tag_markets: frozenset[str] = features.get("tag_markets", frozenset())
        gambling_markets: frozenset[str] = features.get("gambling_markets", frozenset())
        token_map: dict[str, dict[str, str]] = features.get("token_map", {})

        if condition_id not in tag_markets:
            return None
        if condition_id in gambling_markets:
            return None

        if maker_lower not in pool:
            return None

        direction = self._get_direction(trade.asset_id, condition_id, token_map)
        if direction is None:
            return None

        if condition_id not in self._market_state:
            self._market_state[condition_id] = {}
        if maker_lower not in self._market_state[condition_id]:
            self._market_state[condition_id][maker_lower] = {"YES": 0.0, "NO": 0.0}
        self._market_state[condition_id][maker_lower][direction] += float(trade.amount_usd)

        return self._maybe_fire(
            condition_id,
            trade.asset_id,
            float(trade.price),
            trade.published_at,
            token_map,
        )

    def _get_direction(
        self,
        asset_id: str,
        condition_id: str,
        token_map: dict[str, dict[str, str]],
    ) -> str | None:
        if condition_id not in token_map:
            return None
        cid_tokens = token_map[condition_id]
        if asset_id == cid_tokens.get("YES", ""):
            return "YES"
        if asset_id == cid_tokens.get("NO", ""):
            return "NO"
        return None

    def _maybe_fire(
        self,
        condition_id: str,
        triggering_asset_id: str,
        triggering_price: float,
        signal_time: float,
        token_map: dict[str, dict[str, str]],
    ) -> list[TradeIntent] | None:
        if condition_id in self._signaled:
            return None

        state = self._market_state.get(condition_id)
        if state is None:
            return None

        trader_directions: dict[str, str] = {}
        yes_usd = 0.0
        no_usd = 0.0
        for trader, dirs in state.items():
            y, n = dirs.get("YES", 0), dirs.get("NO", 0)
            if y > 0 or n > 0:
                trader_directions[trader] = "YES" if y >= n else "NO"
                yes_usd += y
                no_usd += n

        if len(trader_directions) < self._n_threshold:
            return None

        consensus_dir = "YES" if yes_usd >= no_usd else "NO"

        if self._direction_filter is not None and consensus_dir != self._direction_filter:
            return None

        if self._max_entry_price is not None and triggering_price > self._max_entry_price:
            return None

        self._signaled.add(condition_id)

        outcome = consensus_dir
        asset_id = triggering_asset_id
        cid_tokens = token_map.get(condition_id, {})
        if outcome == "YES" and cid_tokens.get("YES"):
            asset_id = cid_tokens["YES"]
        elif outcome == "NO" and cid_tokens.get("NO"):
            asset_id = cid_tokens["NO"]

        max_price = (
            self._max_price if self._max_price is not None else min(triggering_price + 0.02, 0.99)
        )

        n_yes = sum(1 for d in trader_directions.values() if d == "YES")
        n_no = sum(1 for d in trader_directions.values() if d == "NO")

        return [
            TradeIntent(
                strategy=self.name,
                condition_id=condition_id,
                side="BUY",
                outcome=outcome,
                size_usd=self._size_usd,
                urgency="patient",
                max_price=max_price,
                reason=f"consensus_v2:yes={n_yes},no={n_no}|dir={consensus_dir}|usd={yes_usd:.0f}",
                signal_time=signal_time,
                asset_id=asset_id,
            )
        ]

    async def on_market_update(
        self,
        update: Any,
        ctx: StrategyContext,
    ) -> list[TradeIntent] | None:
        return None

    async def on_timer(
        self,
        now: float,
        ctx: StrategyContext,
    ) -> list[TradeIntent] | None:
        return None


def create_consensus_v2_strategy(cfg: StrategyConfig, **kwargs: Any) -> ConsensusV2Strategy:
    """Factory function for strategy registry."""
    params = cfg.params or {}
    return ConsensusV2Strategy(cfg, **params)
