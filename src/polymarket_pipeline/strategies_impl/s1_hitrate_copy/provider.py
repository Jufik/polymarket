"""S1 Hit-Rate Copy — FeatureProvider (ClickHouse-backed).

Maintains five feature keys in StrategyContext:
  s1_qualified_traders — set[str] of qualified trader addresses (lowercase)
  s1_trader_meta       — dict[str, dict] per-trader metadata
  s1_token_outcomes    — dict[str, str] asset_id → "YES" | "NO"
  s1_gambling_cids     — set[str] gambling market condition_ids
  s1_consensus         — dict[str, dict] per-market consensus counts

Lifecycle:
  compute()   — initial bulk load from ClickHouse at startup
  on_trade()  — O(1) consensus counter update per trade (hot path)
  refresh()   — periodic re-query of CH for updated trader stats
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend

logger = structlog.get_logger(__name__)

# ── SQL queries ─────────────────────────────────────────────────────────
# All heavy computation happens in ClickHouse.
# Results are small (hundreds to low-thousands of rows).

_QUALIFIED_TRADERS_SQL = """
SELECT
    lower(p.trader) AS trader,
    countIf(p.correct = 1) AS wins,
    count(*) AS total,
    countIf(p.correct = 1) / count(*) AS hit_rate
FROM (
    SELECT * FROM trader_positions_resolved
    WHERE position IN ('YES', 'NO')
      AND toDate(resolved_at) >= toDate(now()) - INTERVAL {lookback_months} MONTH
) AS p
INNER JOIN markets AS m ON p.condition_id = m.condition_id
WHERE m.question NOT LIKE '%Up or Down%'
  AND m.question NOT LIKE '%up or down%'
GROUP BY trader
HAVING count(*) >= {min_positions}
   AND hit_rate >= {min_hr}
"""

# Specialization: fraction of positions in the dominant category
_TRADER_SPEC_SQL = """
SELECT trader, max(cnt) / sum(cnt) AS spec, argMax(cat, cnt) AS dom_cat
FROM (
    SELECT
        lower(p.trader) AS trader,
        multiIf(
            m.question LIKE '%Up or Down%', 'gambling',
            m.question LIKE '%NFL%' OR m.question LIKE '%NBA%'
                OR m.question LIKE '%MLB%' OR m.question LIKE '%NHL%'
                OR m.question LIKE '%MMA%' OR m.question LIKE '%UFC%'
                OR m.question LIKE '%vs.%' OR m.question LIKE '%Winner%'
                OR m.question LIKE '%Serie A%' OR m.question LIKE '%EPL%'
                OR m.question LIKE '%La Liga%' OR m.question LIKE '%Bundesliga%',
            'sports',
            m.question LIKE '%Bitcoin%' OR m.question LIKE '%Ethereum%'
                OR m.question LIKE '%crypto%' OR m.question LIKE '%BTC%'
                OR m.question LIKE '%ETH %' OR m.question LIKE '%SOL %',
            'crypto',
            m.question LIKE '%Trump%' OR m.question LIKE '%Biden%'
                OR m.question LIKE '%election%' OR m.question LIKE '%president%',
            'politics',
            'other'
        ) AS cat,
        count(*) AS cnt
    FROM (
        SELECT * FROM trader_positions_resolved
        WHERE position IN ('YES', 'NO')
          AND toDate(resolved_at) >= toDate(now()) - INTERVAL {lookback_months} MONTH
    ) AS p
    INNER JOIN markets AS m ON p.condition_id = m.condition_id
    WHERE m.question NOT LIKE '%Up or Down%'
    GROUP BY trader, cat
)
GROUP BY trader
"""

# Last-5 streak: correct count in 5 most recent resolved positions
_TRADER_STREAK_SQL = """
SELECT trader, countIf(correct = 1) AS last5
FROM (
    SELECT
        lower(trader) AS trader,
        correct,
        ROW_NUMBER() OVER (PARTITION BY lower(trader) ORDER BY resolved_at DESC) AS rn
    FROM trader_positions_resolved
    WHERE position IN ('YES', 'NO')
      AND toDate(resolved_at) >= toDate(now()) - INTERVAL {lookback_months} MONTH
)
WHERE rn <= 5
GROUP BY trader
"""

_TOKEN_OUTCOMES_SQL = """
SELECT asset_id, outcome
FROM token_market_map
"""

_GAMBLING_CIDS_SQL = """
SELECT condition_id
FROM markets
WHERE question LIKE '%Up or Down%'
   OR question LIKE '%up or down%'
"""

_MARKET_CLOSE_TIMES_SQL = """
SELECT condition_id, toFloat64(closed_at) AS close_epoch
FROM markets
WHERE closed_at IS NOT NULL AND closed_at > '1970-01-02'
"""


class S1HitRateProvider:
    """FeatureProvider that queries ClickHouse for qualified traders.

    Parameters (from TOML [provider.s1_hitrate.params]):
      lookback_months : int   — training window (default 9)
      min_positions   : int   — minimum resolved positions (default 20)
      min_hr          : float — minimum hit rate (default 0.75)
    """

    name: str = "s1_hitrate"

    def __init__(
        self,
        lookback_months: int = 9,
        min_positions: int = 20,
        min_hr: float = 0.75,
        initial_bankroll: float = 500.0,
        max_consensus_markets: int = 10_000,
        **_extra: Any,
    ) -> None:
        self._lookback = lookback_months
        self._min_pos = min_positions
        self._min_hr = min_hr
        self._max_consensus_markets = max_consensus_markets

        # Feature state
        self._qualified: set[str] = set()
        self._trader_meta: dict[str, dict[str, Any]] = {}
        self._token_outcomes: dict[str, str] = {}
        self._gambling_cids: set[str] = set()
        self._market_close_epochs: dict[str, float] = {}
        self._consensus: dict[str, dict[str, int]] = {}

        # Bankroll tracking for fractional sizing
        self._bankroll = initial_bankroll
        self._realized_pnl = 0.0

    def _sql_params(self) -> dict[str, Any]:
        return {
            "lookback_months": self._lookback,
            "min_positions": self._min_pos,
            "min_hr": self._min_hr,
        }

    async def compute(self, backend: FeatureBackend) -> None:
        """Bulk load from ClickHouse at startup."""
        params = self._sql_params()

        # 1. Qualified traders
        df = await backend.query_custom(
            _QUALIFIED_TRADERS_SQL.format(**params)
        )
        self._qualified = set(df["trader"].to_list()) if len(df) > 0 else set()
        logger.info(
            "s1.qualified_traders",
            count=len(self._qualified),
            lookback=self._lookback,
            min_pos=self._min_pos,
            min_hr=self._min_hr,
        )

        # 2a. Specialization (event-based)
        df_spec = await backend.query_custom(
            _TRADER_SPEC_SQL.format(**params)
        )
        spec_map: dict[str, tuple[float, str]] = {}
        for row in df_spec.iter_rows(named=True):
            if row["trader"] in self._qualified:
                spec_map[row["trader"]] = (float(row["spec"]), str(row["dom_cat"]))

        # 2b. Last-5 streak (expensive — skip if it OOMs, default to 3)
        streak_map: dict[str, int] = {}
        try:
            df_streak = await backend.query_custom(
                _TRADER_STREAK_SQL.format(**params)
            )
            for row in df_streak.iter_rows(named=True):
                if row["trader"] in self._qualified:
                    streak_map[row["trader"]] = int(row["last5"])
            logger.info("s1.streaks_loaded", count=len(streak_map))
        except Exception:
            logger.warning("s1.streaks_skipped", reason="query_failed")
            # Default all qualified traders to last5=3 (passes min_last5=1 filter)

        # 2c. Merge into trader_meta
        self._trader_meta = {}
        for trader in self._qualified:
            spec_val, dom_cat = spec_map.get(trader, (0.0, "unknown"))
            self._trader_meta[trader] = {
                "dom_cat": dom_cat,
                "spec": spec_val,
                "last5": streak_map.get(trader, 3),  # default 3 if streak query failed
            }
        logger.info(
            "s1.trader_meta",
            count=len(self._trader_meta),
            avg_spec=sum(v["spec"] for v in self._trader_meta.values())
            / max(len(self._trader_meta), 1),
        )

        # 3. Token outcomes (asset_id → YES/NO)
        df_tok = await backend.query_custom(_TOKEN_OUTCOMES_SQL)
        self._token_outcomes = {}
        for row in df_tok.iter_rows(named=True):
            self._token_outcomes[row["asset_id"]] = row["outcome"]
        logger.info("s1.token_outcomes", count=len(self._token_outcomes))

        # 4. Gambling market CIDs
        df_gam = await backend.query_custom(_GAMBLING_CIDS_SQL)
        self._gambling_cids = set(df_gam["condition_id"].to_list()) if len(df_gam) > 0 else set()
        logger.info("s1.gambling_cids", count=len(self._gambling_cids))

        # 5. Market close times (condition_id → epoch)
        df_close = await backend.query_custom(_MARKET_CLOSE_TIMES_SQL)
        self._market_close_epochs = {}
        for row in df_close.iter_rows(named=True):
            self._market_close_epochs[row["condition_id"]] = float(row["close_epoch"])
        logger.info("s1.market_close_times", count=len(self._market_close_epochs))

        # 6. Consensus starts empty — built from on_trade()
        self._consensus = {}

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """O(1) consensus counter update on the hot path.

        When a qualified trader enters a market, increment the YES or NO
        consensus counter for that market. Non-qualified traders are ignored.
        """
        if trade.maker is None:
            return

        maker = trade.maker.lower()
        if maker not in self._qualified:
            return

        # Determine outcome
        outcome = self._token_outcomes.get(trade.asset_id)
        if outcome is None:
            return

        # Only BUY trades are directional signals. SELL = position exit, not new bet.
        if str(trade.side) != "BUY":
            return

        # BUY token X → directional outcome = X
        dir_outcome = outcome

        cid = trade.condition_id
        if cid not in self._consensus:
            if len(self._consensus) >= self._max_consensus_markets:
                return
            self._consensus[cid] = {"yes": set(), "no": set()}

        key = "yes" if dir_outcome == "YES" else "no"
        self._consensus[cid][key].add(maker)

    async def refresh(self, backend: FeatureBackend) -> None:
        """Periodic full re-query of CH for updated trader stats.

        Called by LiveRunner's refresh loop (default every 900s / 15 min).
        Resets consensus counters to avoid stale accumulation.
        """
        await self.compute(backend)

    def update_bankroll(self, pnl_delta: float) -> None:
        """Update bankroll after a fill resolves. Called by runner."""
        self._realized_pnl += pnl_delta
        self._bankroll += pnl_delta

    def get_features(self) -> dict[str, Any]:
        """Return current feature values for context injection."""
        return {
            "s1_qualified_traders": self._qualified,
            "s1_trader_meta": self._trader_meta,
            "s1_token_outcomes": self._token_outcomes,
            "s1_gambling_cids": self._gambling_cids,
            "s1_consensus": self._consensus,
            "s1_market_close_epochs": self._market_close_epochs,
            "s1_bankroll": self._bankroll,
        }
