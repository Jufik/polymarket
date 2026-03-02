"""S2 Hit-Rate Copy — production strategy (delegates to research impl)."""
from __future__ import annotations

from typing import Any

from research.strategies.s2_hitrate_copy import S2Config, S2HitRateCopy


def create_s2_strategy(config: Any) -> S2HitRateCopy:
    """Factory function for CLI registry."""
    params = config.params if hasattr(config, "params") else {}
    cfg = S2Config(
        min_positions=params.get("min_positions", 30),
        min_excess_hr=params.get("min_excess_hr", 0.10),
        seed_threshold=params.get("seed_threshold", 1),
        scale_threshold=params.get("scale_threshold", 4),
        seed_pct=params.get("seed_pct", 0.25),
        seed_timeout_hours=params.get("seed_timeout_hours"),
        direction=params.get("direction", "BOTH"),
        recency_months=params.get("recency_months", 6),
        position_size_usd=params.get("position_size_usd", 100.0),
    )
    return S2HitRateCopy(cfg)
