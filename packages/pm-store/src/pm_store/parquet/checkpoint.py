"""File-based checkpoint implementation for resume support."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class FileCheckpoint:
    """File-based checkpoint implementing pm_core.protocols.Checkpoint.

    Uses atomic write-then-rename for crash safety.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def save(self, cursor: str, progress: int, metadata: dict[str, Any]) -> None:
        """Atomically save checkpoint state.

        Writes to a temporary file first, then renames to the target path.
        This ensures the checkpoint is never in a partial state.
        """
        data = {
            "cursor": cursor,
            "progress": progress,
            "metadata": metadata,
        }

        # Write to temp file in same directory, then atomic rename
        dir_path = self._path.parent
        dir_path.mkdir(parents=True, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp_path, str(self._path))
        except BaseException:
            # Clean up temp file on any error
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def load(self) -> tuple[str, int, dict[str, Any]] | None:
        """Load checkpoint state. Returns None if no checkpoint exists."""
        if not self._path.exists():
            return None

        try:
            with open(self._path) as f:
                data = json.load(f)
            return (
                data["cursor"],
                data["progress"],
                data.get("metadata", {}),
            )
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def clear(self) -> None:
        """Remove the checkpoint file."""
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass

    @property
    def exists(self) -> bool:
        """Whether a checkpoint file exists."""
        return self._path.exists()
