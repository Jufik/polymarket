"""pm-migrate CLI — run database migrations."""

from __future__ import annotations

import subprocess
import sys


def main() -> None:
    """Run Alembic migrations (upgrade head)."""
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        capture_output=False,
    )
    sys.exit(result.returncode)
