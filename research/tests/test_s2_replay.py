# research/tests/test_s2_replay.py
"""Test the S2 replay helper."""
from __future__ import annotations

import asyncio

from research.strategies.s2_hitrate_copy import S2Config, S2HitRateCopy
from research.strategies.s2_replay import run_s2_replay


def test_run_s2_replay_returns_summary(sample_trades, permissive_config):
    """Replay should run without error and return a summary."""
    strat = S2HitRateCopy(S2Config(seed_threshold=1, scale_threshold=2))
    strat.set_qualified_traders({"0xmaker"})  # matches sample_trades maker

    result, summary = asyncio.run(
        run_s2_replay(
            strategy=strat,
            trades=sample_trades,
            config=permissive_config,
            resolutions={"cid_A": ("YES", 1700000000.0)},
            token_map={"cid_A": {"YES": "asset_1", "NO": "asset_2"}},
        )
    )
    # Should have processed trades and produced some fills
    assert result.total_trades == len(sample_trades)
    assert summary is not None
