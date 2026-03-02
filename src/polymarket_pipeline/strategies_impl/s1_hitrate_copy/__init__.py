"""S1 Hit-Rate Copy-Trading Strategy.

Production implementation: copies high-hit-rate specialist traders when
consensus of qualified traders agree on the same directional position.

See research/S1_STRATEGY.md for full design documentation.
"""

from polymarket_pipeline.strategies_impl.s1_hitrate_copy.provider import (
    S1HitRateProvider,
)
from polymarket_pipeline.strategies_impl.s1_hitrate_copy.strategy import (
    S1HitRateCopyStrategy,
)

__all__ = ["S1HitRateCopyStrategy", "S1HitRateProvider"]
