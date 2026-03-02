# research/tests/test_s2_sweep.py
"""Test sweep engine utilities."""
from research.strategies.s2_sweep import build_sweep_grid, config_label


def test_build_sweep_grid():
    grid = build_sweep_grid(
        sweep_params={"a": [1, 2], "b": ["x", "y"]},
        fixed_params={"c": True},
    )
    assert len(grid) == 4
    assert grid[0] == {"a": 1, "b": "x", "c": True}
    assert grid[3] == {"a": 2, "b": "y", "c": True}


def test_config_label():
    label = config_label({"min_excess_hr": 0.10, "scale_threshold": 4, "direction": "YES"})
    assert label == "ehr10_s4_Y"
