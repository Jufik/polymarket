"""S2 Insider Copy — FeatureProvider for insider pool and consensus signals.

Loads a qualified insider pool from ClickHouse at startup and on refresh.
Tracks per-market consensus (unique insider BUY trades) in the hot path.

Required ClickHouse objects:
    - market_susceptibility VIEW (007_market_susceptibility.sql)
    - trader_positions_resolved VIEW (005/006 migrations)
    - markets, events, event_tags, tags (PG-replicated)

Features published to context:
    insider_pool    dict[trader_addr → {score, direction, hr_excess, high_pct}]
    insider_signals dict[condition_id → {direction, insiders, consensus_count, ...}]
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# SQL: insider pool scored (parameterized — cannot be a CH VIEW)
# Uses market_susceptibility VIEW for classification.
# ---------------------------------------------------------------------------

_INSIDER_POOL_SQL = """\
WITH resolved_susceptible AS (
    SELECT
        p.trader,
        p.condition_id,
        p.position,
        p.correct,
        p.realized_pnl,
        p.market_volume,
        p.avg_yes_price,
        p.resolved_at,
        ms.susceptibility
    FROM (SELECT * FROM trader_positions_resolved) AS p
    INNER JOIN market_susceptibility AS ms ON p.condition_id = ms.condition_id
    WHERE ms.susceptibility != 'LOW'
      AND p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= toDate(now()) - INTERVAL {lookback} MONTH
),
trader_stats AS (
    SELECT
        trader,
        countIf(position = 'YES' AND correct = 1) AS yes_wins,
        countIf(position = 'YES') AS yes_total,
        countIf(position = 'NO' AND correct = 1) AS no_wins,
        countIf(position = 'NO') AS no_total,
        count(*) AS total_positions,
        countIf(susceptibility = 'HIGH') / count(*) AS high_pct
    FROM resolved_susceptible
    GROUP BY trader
    HAVING count(*) >= {min_positions}
),
scored AS (
    SELECT
        *,
        greatest(
            (3.81 + yes_wins) / (10.0 + yes_total),
            (6.19 + no_wins) / (10.0 + no_total)
        ) AS effective_hr,
        if(
            (3.81 + yes_wins) / (10.0 + yes_total)
                >= (6.19 + no_wins) / (10.0 + no_total),
            'YES', 'NO'
        ) AS best_direction,
        greatest(
            (3.81 + yes_wins) / (10.0 + yes_total) - 0.381,
            (6.19 + no_wins) / (10.0 + no_total) - 0.619
        ) AS hr_excess
    FROM trader_stats
)
SELECT
    trader, effective_hr, best_direction, hr_excess,
    high_pct, total_positions
FROM scored
WHERE effective_hr >= {min_hr}
  AND effective_hr < {max_hr}
  AND high_pct >= {min_high_pct}
ORDER BY hr_excess DESC
"""


InsiderPool = dict[str, dict[str, Any]]


class InsiderCopyProvider:
    """Qualified insider pool + per-market consensus tracker.

    Lifecycle:
        compute()   → query CH for insider pool (startup)
        on_trade()  → track BUY trades from pool members (hot path, O(1))
        refresh()   → re-query CH for updated pool (periodic)
        get_features() → return pool + signals for strategy consumption
    """

    name: str = "insider_copy_provider"

    def __init__(
        self,
        lookback_months: int = 12,
        min_positions: int = 3,
        min_bayesian_hr: float = 0.75,
        min_high_pct: float = 0.20,
        max_hr: float = 0.99,
    ) -> None:
        self._lookback_months = lookback_months
        self._min_positions = min_positions
        self._min_bayesian_hr = min_bayesian_hr
        self._min_high_pct = min_high_pct
        self._max_hr = max_hr
        self._pool: InsiderPool = {}
        self._signals: dict[str, dict[str, Any]] = {}

    async def compute(self, backend: FeatureBackend) -> None:
        """Initial pool load from ClickHouse."""
        await self._load_pool(backend)

    async def refresh(self, backend: FeatureBackend) -> None:
        """Periodic pool refresh — atomic swap."""
        await self._load_pool(backend)

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """Track BUY trades from insider pool members (hot path).

        CRITICAL: SELL is exit, not directional signal — always filtered.
        Consensus counts unique traders, not trade events.
        """
        if trade.side != "BUY":
            return
        maker = (trade.maker or "").lower()
        if maker not in self._pool:
            return

        cid = trade.condition_id
        info = self._pool[maker]

        if cid not in self._signals:
            self._signals[cid] = {
                "direction": info["direction"],
                "insiders": set(),
                "consensus_count": 0,
                "first_signal_time": trade.published_at,
                "max_score": info["score"],
            }

        sig = self._signals[cid]
        if maker not in sig["insiders"]:
            sig["insiders"].add(maker)
            sig["consensus_count"] = len(sig["insiders"])
            sig["max_score"] = max(sig["max_score"], info["score"])

    def get_features(self) -> dict[str, Any]:
        return {
            "insider_pool": self._pool,
            "insider_signals": self._signals,
            "pool_size": len(self._pool),
        }

    async def _load_pool(self, backend: FeatureBackend) -> None:
        sql = _INSIDER_POOL_SQL.format(
            lookback=self._lookback_months,
            min_positions=self._min_positions,
            min_hr=self._min_bayesian_hr,
            max_hr=self._max_hr,
            min_high_pct=self._min_high_pct,
        )
        df = await backend.query_custom(sql)
        pool: InsiderPool = {}
        for row in df.iter_rows(named=True):
            pool[row["trader"].lower()] = {
                "score": row["effective_hr"],
                "direction": row["best_direction"],
                "hr_excess": row["hr_excess"],
                "high_pct": row["high_pct"],
            }
        old_pool = self._pool
        self._pool = pool

        # Prune signals: drop insiders no longer in the pool, keep the rest
        if old_pool:
            pruned = 0
            for cid, sig in list(self._signals.items()):
                sig["insiders"] = {a for a in sig["insiders"] if a in pool}
                sig["consensus_count"] = len(sig["insiders"])
                if not sig["insiders"]:
                    del self._signals[cid]
                    pruned += 1
            logger.info(
                "insider_copy_provider.signals_pruned",
                kept=len(self._signals),
                pruned=pruned,
            )

        logger.info(
            "insider_copy_provider.pool_loaded",
            size=len(pool),
            lookback_months=self._lookback_months,
        )
