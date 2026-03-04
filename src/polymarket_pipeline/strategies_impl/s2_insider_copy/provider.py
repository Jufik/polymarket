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

# Lightweight query: condition_id → primary_category for all markets in
# market_susceptibility. Used by strategy to filter by category.
_MARKET_CATEGORIES_SQL = """\
SELECT condition_id, primary_category
FROM market_susceptibility
"""

# Bootstrap: pre-load insider BUY consensus from the full lookback period.
# Returns unique (condition_id, maker, asset_id) triples — aggregated at CH level
# to keep result set small (~200K rows for 12 months, vs millions of raw trades).
# Without this, the strategy starts cold and needs N unique insiders to trade
# a market AFTER deployment before consensus is met.
_BOOTSTRAP_SIGNALS_SQL = """\
SELECT
    t.condition_id,
    lower(t.maker) AS maker,
    any(t.asset_id) AS asset_id,
    min(t.timestamp) AS timestamp
FROM trades_raw AS t
WHERE t.side = 'BUY'
  AND t.timestamp >= now() - INTERVAL {lookback_months} MONTH
  AND lower(t.maker) IN ({pool_addresses})
GROUP BY t.condition_id, lower(t.maker)
ORDER BY min(t.timestamp) ASC
"""

INSIDER_POOL_SQL = """\
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
      AND (CASE WHEN p.position = 'YES'
                THEN p.avg_yes_price
                ELSE 1.0 - p.avg_yes_price
           END) < {max_entry_price}
),
trader_stats AS (
    SELECT
        trader,
        countIf(position = 'YES' AND correct = 1) AS yes_wins,
        countIf(position = 'YES') AS yes_total,
        countIf(position = 'NO' AND correct = 1) AS no_wins,
        countIf(position = 'NO') AS no_total,
        count(*) AS total_positions,
        countIf(susceptibility = 'HIGH') / count(*) AS high_pct,
        sum(realized_pnl) AS total_pnl,
        avg(market_volume) AS avg_volume
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
        ) AS hr_excess,
        greatest(
            if(yes_total > 0, yes_wins * 1.0 / yes_total, 0),
            if(no_total > 0, no_wins * 1.0 / no_total, 0)
        ) AS raw_hr
    FROM trader_stats
)
SELECT
    lower(trader) AS trader, effective_hr, best_direction, hr_excess,
    high_pct, total_positions,
    yes_wins, yes_total, no_wins, no_total,
    total_pnl, avg_volume
FROM scored
WHERE effective_hr >= {min_hr}
  AND effective_hr < {max_hr}
  AND raw_hr < {max_raw_hr}
  AND high_pct >= {min_high_pct}
ORDER BY hr_excess DESC
"""

# Category-specific pool: scored on specific categories only, NO high_pct filter.
# Used for categories where HIGH susceptibility markets are rare (e.g. sports)
# and high_pct would exclude genuine category specialists.
CATEGORY_POOL_SQL = """\
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
      AND ms.primary_category IN ({categories})
      AND p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= toDate(now()) - INTERVAL {lookback} MONTH
      AND (CASE WHEN p.position = 'YES'
                THEN p.avg_yes_price
                ELSE 1.0 - p.avg_yes_price
           END) < {max_entry_price}
),
trader_stats AS (
    SELECT
        trader,
        countIf(position = 'YES' AND correct = 1) AS yes_wins,
        countIf(position = 'YES') AS yes_total,
        countIf(position = 'NO' AND correct = 1) AS no_wins,
        countIf(position = 'NO') AS no_total,
        count(*) AS total_positions,
        0.0 AS high_pct,
        sum(realized_pnl) AS total_pnl,
        avg(market_volume) AS avg_volume
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
        ) AS hr_excess,
        greatest(
            if(yes_total > 0, yes_wins * 1.0 / yes_total, 0),
            if(no_total > 0, no_wins * 1.0 / no_total, 0)
        ) AS raw_hr
    FROM trader_stats
)
SELECT
    lower(trader) AS trader, effective_hr, best_direction, hr_excess,
    high_pct, total_positions,
    yes_wins, yes_total, no_wins, no_total,
    total_pnl, avg_volume
FROM scored
WHERE effective_hr >= {min_hr}
  AND effective_hr < {max_hr}
  AND raw_hr < {max_raw_hr}
ORDER BY hr_excess DESC
"""


# Default pool query parameters — shared with API to prevent drift.
POOL_DEFAULTS: dict[str, int | float] = {
    "lookback": 12,
    "min_positions": 3,
    "min_hr": 0.75,
    "max_hr": 0.99,
    "max_raw_hr": 0.99,
    "min_high_pct": 0.20,
    "max_entry_price": 0.95,
}

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
        max_pool_entry_price: float = 0.95,
        max_raw_hr: float = 0.99,
        category_pools: list[str] | None = None,
    ) -> None:
        self._lookback_months = lookback_months
        self._min_positions = min_positions
        self._min_bayesian_hr = min_bayesian_hr
        self._min_high_pct = min_high_pct
        self._max_hr = max_hr
        self._max_pool_entry_price = max_pool_entry_price
        self._max_raw_hr = max_raw_hr
        self._category_pools = category_pools or []
        self._pool: InsiderPool = {}
        self._signals: dict[str, dict[str, Any]] = {}
        self._market_categories: dict[str, str] = {}  # condition_id → primary_category
        self._asset_id_to_outcome: dict[str, str] = {}  # asset_id → "YES"/"NO"
        # condition_id → {"YES": asset_id, "NO": asset_id}
        self._token_map: dict[str, dict[str, str]] = {}

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
        Direction is derived from the actual trade's asset_id via token_map,
        NOT the pool's aggregate best_direction.
        """
        if trade.side != "BUY":
            return
        maker = (trade.maker or "").lower()
        if maker not in self._pool:
            return

        cid = trade.condition_id
        info = self._pool[maker]

        # Derive actual outcome from asset_id via token_map
        trade_outcome = self._asset_id_to_outcome.get(trade.asset_id)
        if trade_outcome is None:
            # Fallback to pool's best_direction if token_map not loaded yet
            trade_outcome = info["direction"]

        if cid not in self._signals:
            self._signals[cid] = {
                "direction": trade_outcome,
                "insiders": set(),
                "consensus_count": 0,
                "first_signal_time": trade.published_at,
                "max_score": info["score"],
                "asset_id": trade.asset_id,
            }

        sig = self._signals[cid]
        if maker not in sig["insiders"]:
            sig["insiders"].add(maker)
            sig["consensus_count"] = len(sig["insiders"])
            sig["max_score"] = max(sig["max_score"], info["score"])

    def get_features(self) -> dict[str, Any]:
        return {
            self.name: {
                "insider_pool": self._pool,
                "insider_signals": self._signals,
                "pool_size": len(self._pool),
                "market_categories": self._market_categories,
                "token_map": self._token_map,
            },
        }

    async def _load_pool(self, backend: FeatureBackend) -> None:
        sql = INSIDER_POOL_SQL.format(
            lookback=self._lookback_months,
            min_positions=self._min_positions,
            min_hr=self._min_bayesian_hr,
            max_hr=self._max_hr,
            max_raw_hr=self._max_raw_hr,
            min_high_pct=self._min_high_pct,
            max_entry_price=self._max_pool_entry_price,
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

        # Category-specific pool: scored on specific categories, no high_pct.
        # Merges into pool — new traders added, conflicts keep higher score.
        cat_pool_size = 0
        if self._category_pools:
            cat_in = ", ".join(f"'{c}'" for c in self._category_pools)
            cat_sql = CATEGORY_POOL_SQL.format(
                lookback=self._lookback_months,
                min_positions=self._min_positions,
                min_hr=self._min_bayesian_hr,
                max_hr=self._max_hr,
                max_raw_hr=self._max_raw_hr,
                max_entry_price=self._max_pool_entry_price,
                categories=cat_in,
            )
            try:
                cat_df = await backend.query_custom(cat_sql)
                for row in cat_df.iter_rows(named=True):
                    addr = row["trader"].lower()
                    entry = {
                        "score": row["effective_hr"],
                        "direction": row["best_direction"],
                        "hr_excess": row["hr_excess"],
                        "high_pct": row["high_pct"],
                    }
                    if addr not in pool or entry["score"] > pool[addr]["score"]:
                        pool[addr] = entry
                        cat_pool_size += 1
                logger.info(
                    "insider_copy_provider.category_pool_merged",
                    categories=self._category_pools,
                    added=cat_pool_size,
                )
            except Exception:
                logger.warning(
                    "insider_copy_provider.category_pool_failed",
                    categories=self._category_pools,
                )

        old_pool = self._pool
        self._pool = pool

        # Load market → category mapping (for strategy-side filtering)
        try:
            cat_df = await backend.query_custom(_MARKET_CATEGORIES_SQL)
            cats: dict[str, str] = {}
            for row in cat_df.iter_rows(named=True):
                cats[row["condition_id"]] = row["primary_category"]
            self._market_categories = cats
            logger.info(
                "insider_copy_provider.categories_loaded",
                count=len(cats),
            )
        except Exception:
            logger.warning("insider_copy_provider.categories_load_failed")

        # Load asset_id → outcome mapping from token_market_map (PG-replicated)
        try:
            token_df = await backend.query_custom(
                "SELECT asset_id, condition_id, outcome FROM token_market_map"
            )
            asset_map: dict[str, str] = {}
            tok_map: dict[str, dict[str, str]] = {}
            for row in token_df.iter_rows(named=True):
                asset_map[row["asset_id"]] = row["outcome"]
                tok_map.setdefault(row["condition_id"], {})[row["outcome"]] = row["asset_id"]
            self._asset_id_to_outcome = asset_map
            self._token_map = tok_map
            logger.info(
                "insider_copy_provider.token_map_loaded",
                count=len(asset_map),
            )
        except Exception:
            logger.warning("insider_copy_provider.token_map_load_failed")

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

        # Bootstrap signals from recent CH trades (cold-start fix).
        # On first load (no old_pool), seed consensus counters so the strategy
        # doesn't need to wait for N insiders to trade after deployment.
        if not old_pool and pool:
            await self._bootstrap_signals(backend, pool)

        logger.info(
            "insider_copy_provider.pool_loaded",
            size=len(pool),
            category_pool_added=cat_pool_size,
            lookback_months=self._lookback_months,
            signals=len(self._signals),
        )

    async def _bootstrap_signals(
        self, backend: FeatureBackend, pool: InsiderPool
    ) -> None:
        """Seed consensus counters from the full lookback period.

        Queries unique (condition_id, maker) pairs from the same lookback
        window used to build the pool, so consensus matches the research
        backtests which accumulate over the full history.
        """
        addresses = list(pool.keys())
        pool_in = ", ".join(f"'{a}'" for a in addresses)
        sql = _BOOTSTRAP_SIGNALS_SQL.format(
            lookback_months=self._lookback_months, pool_addresses=pool_in
        )
        try:
            df = await backend.query_custom(sql)
        except Exception:
            logger.warning("insider_copy_provider.bootstrap_failed")
            return

        seeded = 0
        for row in df.iter_rows(named=True):
            maker = row["maker"]
            cid = row["condition_id"]
            if maker not in pool:
                continue

            info = pool[maker]
            trade_outcome = self._asset_id_to_outcome.get(row["asset_id"])
            if trade_outcome is None:
                trade_outcome = info["direction"]

            if cid not in self._signals:
                self._signals[cid] = {
                    "direction": trade_outcome,
                    "insiders": set(),
                    "consensus_count": 0,
                    "first_signal_time": row["timestamp"],
                    "max_score": info["score"],
                    "asset_id": row["asset_id"],
                }

            sig = self._signals[cid]
            if maker not in sig["insiders"]:
                sig["insiders"].add(maker)
                sig["consensus_count"] = len(sig["insiders"])
                sig["max_score"] = max(sig["max_score"], info["score"])
                seeded += 1

        logger.info(
            "insider_copy_provider.bootstrap_complete",
            lookback_months=self._lookback_months,
            markets=len(self._signals),
            seeded_entries=seeded,
        )
