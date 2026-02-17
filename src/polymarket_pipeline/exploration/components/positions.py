"""Trade positions component.

Computes per-trader, per-market positions using dual-perspective (maker + taker)
UNION ALL approach. Each trade generates two position rows:
  - Maker: side as recorded, zero fee
  - Taker: side INVERTED, fee attributed to taker
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from polymarket_pipeline.exploration.components.base import ComponentConfig


class PositionsConfig(ComponentConfig):
    """Config for positions computation."""

    lookback_start: str = "2025-08-01"
    perspective: Literal["both", "taker_only", "maker_only"] = "both"
    # For taker-only: filter traders by max maker volume fraction
    max_maker_volume_fraction: float | None = None

    model_config = ConfigDict(frozen=True)


def positions_cte(cfg: PositionsConfig, resolved_cte_name: str = "resolved") -> str:
    """Return the positions CTE as a SQL string.

    Depends on a prior CTE named *resolved_cte_name* being in scope.
    """
    if cfg.perspective == "taker_only":
        trader_trades = f"""
        SELECT
            taker AS trader,
            condition_id,
            asset_id,
            if(side = 'BUY', 'SELL', 'BUY') AS trader_side,
            size,
            amount_usd,
            fee_usd AS trader_fee_usd,
            price,
            timestamp
        FROM polymarket.trades_raw FINAL
        WHERE timestamp >= '{cfg.lookback_start}'
          AND taker IS NOT NULL"""
    elif cfg.perspective == "maker_only":
        trader_trades = f"""
        SELECT
            maker AS trader,
            condition_id,
            asset_id,
            side AS trader_side,
            size,
            amount_usd,
            toFloat32(0.0) AS trader_fee_usd,
            price,
            timestamp
        FROM polymarket.trades_raw FINAL
        WHERE timestamp >= '{cfg.lookback_start}'
          AND maker IS NOT NULL"""
    else:
        trader_trades = f"""
        -- MAKER perspective: side as recorded, zero fee
        SELECT
            maker AS trader,
            condition_id,
            asset_id,
            side AS trader_side,
            size,
            amount_usd,
            toFloat32(0.0) AS trader_fee_usd,
            price,
            timestamp
        FROM polymarket.trades_raw FINAL
        WHERE timestamp >= '{cfg.lookback_start}'
          AND maker IS NOT NULL

        UNION ALL

        -- TAKER perspective: side INVERTED, fee attributed to taker
        SELECT
            taker AS trader,
            condition_id,
            asset_id,
            if(side = 'BUY', 'SELL', 'BUY') AS trader_side,
            size,
            amount_usd,
            fee_usd AS trader_fee_usd,
            price,
            timestamp
        FROM polymarket.trades_raw FINAL
        WHERE timestamp >= '{cfg.lookback_start}'
          AND taker IS NOT NULL"""

    return f"""positions AS (
    SELECT
        tt.trader AS trader,
        tt.condition_id AS condition_id,
        tm.outcome AS outcome,
        sumIf(tt.size, tt.trader_side = 'BUY')
            - sumIf(tt.size, tt.trader_side = 'SELL') AS net_tokens,
        sumIf(tt.amount_usd, tt.trader_side = 'BUY') AS total_spent,
        sumIf(tt.amount_usd, tt.trader_side = 'SELL') AS total_received,
        sum(tt.trader_fee_usd) AS total_fees,
        sum(tt.amount_usd) AS market_volume,
        count() AS trade_count,
        sumIf(tt.price * tt.amount_usd, tt.trader_side = 'BUY')
            / greatest(sumIf(tt.amount_usd, tt.trader_side = 'BUY'), 0.01)
            AS avg_entry_price,
        min(tt.timestamp) AS first_trade,
        max(tt.timestamp) AS last_trade
    FROM ({trader_trades}
    ) tt
    INNER JOIN polymarket.token_market_map tm ON tt.asset_id = tm.asset_id
    WHERE tt.condition_id IN (SELECT condition_id FROM {resolved_cte_name})
    GROUP BY trader, condition_id, outcome
)"""
