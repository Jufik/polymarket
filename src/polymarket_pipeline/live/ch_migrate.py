"""ClickHouse migration runner — apply sequential SQL files."""

from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger()

MIGRATIONS_TABLE = "_ch_migrations"
CREATE_MIGRATIONS_TABLE = f"""
CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (
    version String,
    applied_at DateTime DEFAULT now()
) ENGINE = MergeTree() ORDER BY version
"""


def run_ch_migrations(
    ch_client: object,
    migrations_dir: Path = Path("docker/clickhouse/migrations"),
) -> int:
    """Apply pending ClickHouse migrations.

    Returns the number of migrations applied.
    """
    # Ensure migrations tracking table exists
    ch_client.command(CREATE_MIGRATIONS_TABLE)  # type: ignore[attr-defined]

    # Get already applied migrations
    rows = ch_client.query(  # type: ignore[attr-defined]
        f"SELECT version FROM {MIGRATIONS_TABLE}"
    ).result_rows
    applied = {row[0] for row in rows}

    # Find and sort migration files
    if not migrations_dir.exists():
        log.warning("ch_migrate.no_dir", path=str(migrations_dir))
        return 0

    sql_files = sorted(migrations_dir.glob("*.sql"))
    count = 0

    for sql_file in sql_files:
        version = sql_file.stem
        if version in applied:
            continue

        log.info("ch_migrate.applying", version=version)
        sql = sql_file.read_text()

        # Execute each statement separately
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                ch_client.command(stmt)  # type: ignore[attr-defined]

        # Record migration
        ch_client.command(  # type: ignore[attr-defined]
            f"INSERT INTO {MIGRATIONS_TABLE} (version) VALUES ('{version}')"
        )
        count += 1
        log.info("ch_migrate.applied", version=version)

    return count
