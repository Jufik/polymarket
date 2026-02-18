"""Run the consistency_copy backtester.

Usage:
    uv run python -m strategies.consistency_copy.backtester                    # consensus (default)
    uv run python -m strategies.consistency_copy.backtester --mode portfolio   # portfolio replication
    uv run python -m strategies.consistency_copy.backtester --config path.toml
"""

import argparse
from pathlib import Path

parser = argparse.ArgumentParser(description="Consistency copy backtester")
parser.add_argument(
    "--config",
    type=Path,
    default=None,
    help="Path to sweep config TOML (default: strategies/consistency_copy/sweep_config.toml)",
)
parser.add_argument(
    "--mode",
    choices=["consensus", "portfolio"],
    default="consensus",
    help="Backtester mode: consensus (default) or portfolio replication",
)

args = parser.parse_args()

if args.mode == "portfolio":
    from strategies.consistency_copy.backtester.portfolio_runner import main
else:
    from strategies.consistency_copy.backtester.runner import main

main(config_path=args.config)
