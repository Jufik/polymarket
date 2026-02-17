"""Run the consensus copy backtester sweep.

Usage:
    uv run python -m strategies.consistency_copy.backtester
    uv run python -m strategies.consistency_copy.backtester --config path/to/config.toml
"""

import argparse
from pathlib import Path

from strategies.consistency_copy.backtester.runner import main

parser = argparse.ArgumentParser(description="Consensus copy backtester sweep")
parser.add_argument(
    "--config",
    type=Path,
    default=None,
    help="Path to sweep config TOML (default: strategies/consistency_copy/sweep_config.toml)",
)

args = parser.parse_args()
main(config_path=args.config)
