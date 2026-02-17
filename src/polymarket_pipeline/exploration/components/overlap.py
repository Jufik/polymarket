"""Cross-stage overlap analysis component.

Computes Jaccard similarity and set overlap between trader sets
from different stages or filtering approaches.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from polymarket_pipeline.exploration.components.base import ComponentConfig, ComponentResult


class OverlapConfig(ComponentConfig):
    """Config for overlap analysis."""

    parent_stage_outputs: str | None = None  # path to parent's skilled_traders parquet
    top_n_comparison: int = 50  # top N for PnL vs volume overlap


def parent_overlap_analysis(
    db: object,
    config: ComponentConfig,
    outputs_dir: Path,
    prior_results: dict[str, ComponentResult] | None = None,
) -> ComponentResult:
    """Compute overlap between current skilled set and parent's skilled set.

    Also computes PnL rank vs volume rank correlation.
    """
    cfg = config if isinstance(config, OverlapConfig) else OverlapConfig()
    prior = prior_results or {}

    skill_result = prior.get("skill")
    agg_result = prior.get("trader_aggregates") or prior.get("pnl")

    if not skill_result or not agg_result:
        return ComponentResult(
            name="overlap",
            metrics={"error": "missing required prior results"},
        )

    df_skilled = skill_result.dataframes.get("skilled_traders", pl.DataFrame())
    df_all = agg_result.dataframes.get("trader_pnl_features", pl.DataFrame())

    metrics: dict[str, object] = {
        "skilled_count": len(df_skilled),
    }

    # PnL vs volume rank correlation
    if len(df_all) > 0:
        df_ranks = df_all.with_columns([
            pl.col("estimated_pnl").rank(descending=True).alias("pnl_rank"),
            pl.col("resolved_volume_usd").rank(descending=True).alias("vol_rank"),
        ])
        rank_corr = float(df_ranks.select(pl.corr("pnl_rank", "vol_rank")).item())
        metrics["pnl_vs_volume_rank_correlation"] = round(rank_corr, 4)

        # Top-N overlap
        n = cfg.top_n_comparison
        top_pnl = set(
            df_all.sort("estimated_pnl", descending=True).head(n)["trader"].to_list()
        )
        top_vol = set(
            df_all.sort("resolved_volume_usd", descending=True).head(n)["trader"].to_list()
        )
        metrics[f"top{n}_pnl_vs_volume_overlap"] = len(top_pnl & top_vol)

    # Parent overlap (Jaccard)
    if cfg.parent_stage_outputs:
        parent_path = Path(cfg.parent_stage_outputs)
        if parent_path.exists():
            df_parent = pl.read_parquet(parent_path)
            parent_set = set(df_parent["trader"].to_list())
            current_set = set(df_skilled["trader"].to_list())
            intersection = len(parent_set & current_set)
            union = len(parent_set | current_set)
            jaccard = intersection / max(union, 1)
            metrics["parent_skilled_count"] = len(parent_set)
            metrics["intersection_count"] = intersection
            metrics["jaccard_similarity"] = round(jaccard, 4)
            metrics["overlap_pct_of_current"] = round(
                intersection / max(len(current_set), 1) * 100, 2
            )

    return ComponentResult(
        name="overlap",
        metrics=metrics,
    )
