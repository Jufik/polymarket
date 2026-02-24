"""Parameter sweep support.

When a refinement is type="parameter" with one varying param,
run the same pipeline N times with different configs. No Claude
invocation per variant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polymarket_pipeline.exploration.pipeline import StagePipeline
from polymarket_pipeline.exploration.tree import (
    ExplorationStage,
    ProposedRefinement,
    RefinementType,
    StageMetrics,
    SweepResult,
    SweepVariantResult,
)


@dataclass
class ParameterSweep:
    """Definition of a parameter sweep."""

    base_pipeline: StagePipeline
    sweep_param: str  # e.g., "max_maker_volume_fraction"
    sweep_values: list[Any]  # e.g., [0.05, 0.10, 0.15, 0.20]
    comparison_metric: str = "unique_traders"  # metric to use for comparison


@dataclass
class SweepRunner:
    """Execute a parameter sweep across pipeline variants."""

    def run_sweep(
        self,
        sweep: ParameterSweep,
        strategy_root: Path,
        outputs_dir: Path,
    ) -> SweepResult:
        """Execute pipeline N times, varying one config param.

        Returns SweepResult with per-variant metrics and best value.
        """
        outputs_dir.mkdir(parents=True, exist_ok=True)
        variants: list[SweepVariantResult] = []

        for value in sweep.sweep_values:
            variant_dir = outputs_dir / f"{sweep.sweep_param}_{value}"
            variant_dir.mkdir(parents=True, exist_ok=True)

            # Create a modified pipeline with the swept parameter
            modified_pipeline = self._modify_pipeline(
                sweep.base_pipeline,
                sweep.sweep_param,
                value,
            )

            try:
                summary = modified_pipeline.run(strategy_root, variant_dir)
                metrics_data = summary.get("metrics", {})

                # Extract StageMetrics
                metrics = None
                if isinstance(metrics_data, dict):
                    metrics = StageMetrics(**metrics_data)

                variants.append(
                    SweepVariantResult(
                        param_value=value,
                        metrics=metrics,
                        outputs_path=str(variant_dir),
                    )
                )

                # Save variant summary
                (variant_dir / "summary.json").write_text(
                    json.dumps(summary, indent=2, default=str)
                )

            except Exception as e:
                variants.append(
                    SweepVariantResult(
                        param_value=value,
                        metrics=None,
                        outputs_path=str(variant_dir),
                    )
                )
                (variant_dir / "error.txt").write_text(str(e))

        # Determine best value
        best_value = self._find_best(variants, sweep.comparison_metric)

        result = SweepResult(
            sweep_param=sweep.sweep_param,
            sweep_values=sweep.sweep_values,
            variants=variants,
            best_value=best_value,
            comparison_metric=sweep.comparison_metric,
        )

        # Save sweep summary
        (outputs_dir / "sweep_summary.json").write_text(result.model_dump_json(indent=2))

        return result

    def _modify_pipeline(
        self,
        pipeline: StagePipeline,
        param: str,
        value: Any,
    ) -> StagePipeline:
        """Create a copy of the pipeline with one config param changed."""
        from copy import deepcopy

        from polymarket_pipeline.exploration.pipeline import HookStep, PipelineStep

        new_steps: list[PipelineStep | HookStep] = []
        for step in pipeline.steps:
            if isinstance(step, PipelineStep):
                config_dict = step.config.model_dump()
                if param in config_dict:
                    config_dict[param] = value
                    new_config = step.config.__class__(**config_dict)
                    new_steps.append(
                        PipelineStep(
                            name=step.name,
                            component_fn=step.component_fn,
                            config=new_config,
                            depends_on=list(step.depends_on),
                        )
                    )
                else:
                    new_steps.append(deepcopy(step))
            else:
                new_steps.append(deepcopy(step))

        return StagePipeline(
            stage_id=f"{pipeline.stage_id}_sweep_{param}_{value}",
            steps=new_steps,
        )

    def _find_best(
        self,
        variants: list[SweepVariantResult],
        metric: str,
    ) -> Any | None:
        """Find the variant with the best value for the comparison metric."""
        best_val: float | None = None
        best_param = None

        for variant in variants:
            if variant.metrics is None:
                continue
            # Check in standard fields
            val = getattr(variant.metrics, metric, None)
            # Check in custom dict
            if val is None and variant.metrics.custom:
                val = variant.metrics.custom.get(metric)
            if val is not None and (best_val is None or float(val) > float(best_val)):
                best_val = float(val)
                best_param = variant.param_value

        return best_param


def detect_parameter_sweep(
    parent: ExplorationStage,
    refinement: ProposedRefinement,
) -> ParameterSweep | None:
    """Detect if a refinement can be expressed as a parameter sweep.

    Returns a ParameterSweep if the refinement is type=parameter and
    has explicit filter_conditions with sweep values, otherwise None.
    """
    if refinement.refinement_type != RefinementType.PARAMETER:
        return None

    if not refinement.filter_conditions:
        return None

    # Look for a key with a list of values
    for key, value in refinement.filter_conditions.items():
        if isinstance(value, list) and len(value) >= 2:
            # This is a sweep
            return ParameterSweep(
                base_pipeline=StagePipeline(stage_id="sweep", steps=[]),
                sweep_param=key,
                sweep_values=value,
            )

    return None
