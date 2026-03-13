"""Backward-compat shim -- re-exports from pm_strategy.impl.consensus_v2.strategy."""

from pm_strategy.impl.consensus_v2.strategy import (  # noqa: F401
    ConsensusV2Strategy,
    create_consensus_v2_strategy,
)

__all__ = ["ConsensusV2Strategy", "create_consensus_v2_strategy"]
