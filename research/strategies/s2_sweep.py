"""Parameter sweep engine for S2 strategy."""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

import polars as pl


@dataclass(frozen=True)
class SweepResult:
    """Results for a single sweep config across all periods."""

    config_label: str
    config: dict[str, Any]
    periods_profitable: int
    total_periods: int
    mean_excess_hr: float
    std_excess_hr: float
    mean_sharpe: float
    mean_edge: float
    mean_hold_days: float
    total_fills: int
    total_pnl: float
    compounding_score: float


def build_sweep_grid(
    sweep_params: dict[str, list[Any]],
    fixed_params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build parameter grid from sweep and fixed params.

    Parameters
    ----------
    sweep_params:
        Dict of param_name -> list of values to sweep.
    fixed_params:
        Dict of param_name -> fixed value.

    Returns list of dicts, each a complete parameter set.
    """
    keys = list(sweep_params.keys())
    values = list(sweep_params.values())
    grid = []
    for combo in itertools.product(*values):
        params = dict(fixed_params)
        for k, v in zip(keys, combo):
            params[k] = v
        grid.append(params)
    return grid


def config_label(params: dict[str, Any]) -> str:
    """Generate a short label for a parameter set."""
    parts = []
    if "min_excess_hr" in params:
        parts.append(f"ehr{int(params['min_excess_hr']*100)}")
    if "scale_threshold" in params:
        parts.append(f"s{params['scale_threshold']}")
    if "direction" in params:
        parts.append(params["direction"][0])  # Y, N, or B
    if "min_positions" in params:
        parts.append(f"p{params['min_positions']}")
    if "max_entry_price" in params and params["max_entry_price"] != 0.85:
        parts.append(f"ep{int(params['max_entry_price']*100)}")
    if "max_signal_price" in params and params["max_signal_price"] != 0.85:
        parts.append(f"sp{int(params['max_signal_price']*100)}")
    if "use_bayesian_hr" in params and params["use_bayesian_hr"]:
        parts.append("bay")
    if "yes_weight" in params and params["yes_weight"] != 1.0:
        parts.append(f"yw{params['yes_weight']}")
    if "no_weight" in params and params["no_weight"] != 1.0:
        parts.append(f"nw{params['no_weight']}")
    return "_".join(parts) if parts else "default"


def aggregate_period_summaries(
    config_params: dict[str, Any],
    summaries: list[Any],  # list of LedgerSummary or None
    base_hr: float = 0.381,
) -> SweepResult:
    """Aggregate LedgerSummary across periods into SweepResult."""
    valid = [s for s in summaries if s is not None and s.total_fills > 0]
    n_periods = len(summaries)

    if not valid:
        return SweepResult(
            config_label=config_label(config_params),
            config=config_params,
            periods_profitable=0,
            total_periods=n_periods,
            mean_excess_hr=0.0,
            std_excess_hr=0.0,
            mean_sharpe=0.0,
            mean_edge=0.0,
            mean_hold_days=0.0,
            total_fills=0,
            total_pnl=0.0,
            compounding_score=0.0,
        )

    excess_hrs = [s.hit_rate - base_hr for s in valid]
    sharpes = [s.sharpe for s in valid]
    edges = [s.avg_edge for s in valid]
    hold_days = [s.avg_hold_duration_s / 86400 for s in valid]
    profitable = sum(1 for s in valid if s.total_pnl_net > 0)

    mean_excess = sum(excess_hrs) / len(excess_hrs) if excess_hrs else 0
    std_excess = (
        (sum((x - mean_excess) ** 2 for x in excess_hrs) / len(excess_hrs)) ** 0.5
        if len(excess_hrs) > 1
        else 0.0
    )
    mean_hold = sum(hold_days) / len(hold_days) if hold_days else 0
    mean_edge_val = sum(edges) / len(edges) if edges else 0

    compound = (
        mean_excess * mean_edge_val / max(mean_hold, 0.1)
        if mean_edge_val > 0
        else 0
    )

    return SweepResult(
        config_label=config_label(config_params),
        config=config_params,
        periods_profitable=profitable,
        total_periods=n_periods,
        mean_excess_hr=mean_excess,
        std_excess_hr=std_excess,
        mean_sharpe=sum(sharpes) / len(sharpes) if sharpes else 0,
        mean_edge=mean_edge_val,
        mean_hold_days=mean_hold,
        total_fills=sum(s.total_fills for s in valid),
        total_pnl=sum(s.total_pnl_net for s in valid),
        compounding_score=compound,
    )


def sweep_results_to_polars(results: list[SweepResult]) -> pl.DataFrame:
    """Convert sweep results to a Polars DataFrame for display."""
    rows = []
    for r in results:
        rows.append({
            "config": r.config_label,
            "fills": r.total_fills,
            "profitable": f"{r.periods_profitable}/{r.total_periods}",
            "excess_hr": f"{r.mean_excess_hr:+.1%}",
            "std_hr": f"{r.std_excess_hr:.1%}",
            "sharpe": f"{r.mean_sharpe:.2f}",
            "pnl": f"${r.total_pnl:,.2f}",
            "edge": f"${r.mean_edge:,.4f}",
            "hold_d": f"{r.mean_hold_days:.1f}",
            "compound": f"{r.compounding_score:.3f}",
        })
    return pl.DataFrame(rows).sort("compound", descending=True)
