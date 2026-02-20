"""CLI entry point for the live sync pipeline."""

from __future__ import annotations

import sys


def main() -> None:
    """Run the FastStream live pipeline.

    Equivalent to: faststream run polymarket_pipeline.live.app:app
    """
    from faststream.cli.main import cli

    sys.argv = ["faststream", "run", "polymarket_pipeline.live.app:app"]
    cli()


if __name__ == "__main__":
    main()
