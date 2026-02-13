"""Stage: {stage_id} — {stage_name}

Hypothesis: {hypothesis}
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from polymarket_pipeline.exploration.data import ExplorationDataSource
from polymarket_pipeline.exploration.tree import StageMetrics

STAGE_ID = "{stage_id}"


def run(strategy_root: Path, outputs_dir: Path) -> dict:
    """Execute this stage.

    Args:
        strategy_root: Path to the strategy directory (e.g., strategies/skilled_traders/)
        outputs_dir: Path to save output files (e.g., stages/00_initial/outputs/)

    Returns:
        Summary dict for Claude review and MLflow logging.
    """
    db = ExplorationDataSource()

    # ------------------------------------------------------------------
    # 1. Query ClickHouse (push aggregation to SQL)
    # ------------------------------------------------------------------
    # TODO: Replace with your analysis queries
    df = db.query_df("""
        SELECT 1 as placeholder
    """)

    # ------------------------------------------------------------------
    # 2. Exploratory analysis in Polars (prototyping, not productized)
    # ------------------------------------------------------------------
    # TODO: Add Polars transforms here

    # ------------------------------------------------------------------
    # 3. Save outputs
    # ------------------------------------------------------------------
    # df.write_parquet(outputs_dir / "results.parquet")

    # ------------------------------------------------------------------
    # 4. Compute metrics
    # ------------------------------------------------------------------
    metrics = StageMetrics(
        sample_size=len(df),
    )

    # ------------------------------------------------------------------
    # 5. Return summary for Claude review
    # ------------------------------------------------------------------
    return {
        "stage_id": STAGE_ID,
        "metrics": metrics.model_dump(exclude_none=True),
        "findings": {
            # TODO: Add key findings
        },
    }


if __name__ == "__main__":
    root = Path(__file__).parent.parent.parent
    out = Path(__file__).parent / "outputs"
    out.mkdir(exist_ok=True)
    summary = run(root, out)
    print(json.dumps(summary, indent=2, default=str))
