"""Redis key conventions for dynamic config."""

PREFIX = "pm:config"
CHANGED_CHANNEL = f"{PREFIX}:changed"
CHANGELOG_STREAM = f"{PREFIX}:changelog"


def section_key(section: str) -> str:
    """Return Redis HASH key for a config section."""
    return f"{PREFIX}:{section}"
