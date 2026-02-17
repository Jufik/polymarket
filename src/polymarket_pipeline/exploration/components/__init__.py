"""Reusable exploration components.

Public API for composing stages from building blocks.
"""

from polymarket_pipeline.exploration.components.base import (
    ComponentConfig,
    ComponentHook,
    ComponentResult,
)
from polymarket_pipeline.exploration.components.maker_fraction import (
    MakerFractionConfig,
    compute_mvf,
    maker_volume_fraction_cte,
)
from polymarket_pipeline.exploration.components.oos import (
    OOSConfig,
    oos_evaluate,
    out_of_sample_split,
)
from polymarket_pipeline.exploration.components.overlap import (
    OverlapConfig,
    parent_overlap_analysis,
)
from polymarket_pipeline.exploration.components.pnl import (
    PnLConfig,
    compute_market_pnl,
    compute_trader_aggregates,
)
from polymarket_pipeline.exploration.components.positions import (
    PositionsConfig,
    positions_cte,
)
from polymarket_pipeline.exploration.components.resolved_markets import (
    ResolvedMarketsConfig,
    query_resolved_markets,
    resolved_markets_cte,
)
from polymarket_pipeline.exploration.components.scoring import (
    ScoringConfig,
    bootstrap_filter,
    pnl_per_dollar_filter,
    z_score_filter,
)
from polymarket_pipeline.exploration.components.temporal import (
    TemporalConfig,
    empirical_baseline,
    temporal_consistency,
)
from polymarket_pipeline.exploration.components.validation import (
    ValidationConfig,
    dedup_check,
    fee_coverage_check,
)

__all__ = [
    # Base types
    "ComponentConfig",
    "ComponentHook",
    "ComponentResult",
    # Resolved markets
    "ResolvedMarketsConfig",
    "query_resolved_markets",
    "resolved_markets_cte",
    # Positions
    "PositionsConfig",
    "positions_cte",
    # PnL
    "PnLConfig",
    "compute_market_pnl",
    "compute_trader_aggregates",
    # Maker fraction
    "MakerFractionConfig",
    "compute_mvf",
    "maker_volume_fraction_cte",
    # Scoring
    "ScoringConfig",
    "z_score_filter",
    "pnl_per_dollar_filter",
    "bootstrap_filter",
    # Temporal
    "TemporalConfig",
    "temporal_consistency",
    "empirical_baseline",
    # Validation
    "ValidationConfig",
    "dedup_check",
    "fee_coverage_check",
    # Overlap
    "OverlapConfig",
    "parent_overlap_analysis",
    # Out-of-sample
    "OOSConfig",
    "out_of_sample_split",
    "oos_evaluate",
]
