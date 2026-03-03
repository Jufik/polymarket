"""S2 tag data module — tag-specific base rates and CH materialization queries.

Tag base rates vary from 9% YES (Elections) to 73% YES (Earnings).
A global base rate (38%/62%) is wrong for most tags. This module provides
per-tag base rates and CH SQL to materialize tag→market mappings as native
Memory tables (avoiding slow PG engine joins).
"""
from __future__ import annotations

# 2025 data from research/knowledge/data/tag_base_rates.md
TAG_BASE_RATES: dict[str, dict[str, float]] = {
    "Earnings":       {"YES": 0.729, "NO": 0.271},
    "Earnings Calls": {"YES": 0.588, "NO": 0.412},
    "Tariffs":        {"YES": 0.479, "NO": 0.521},
    "Courts":         {"YES": 0.211, "NO": 0.789},
    "Geopolitics":    {"YES": 0.270, "NO": 0.730},
    "Trump":          {"YES": 0.341, "NO": 0.659},
    "Politics":       {"YES": 0.232, "NO": 0.768},
    "Crypto":         {"YES": 0.289, "NO": 0.711},
    "Finance":        {"YES": 0.353, "NO": 0.647},
    "Economy":        {"YES": 0.234, "NO": 0.766},
    "Culture":        {"YES": 0.117, "NO": 0.883},
    "Elections":      {"YES": 0.090, "NO": 0.910},
    "Tech":           {"YES": 0.124, "NO": 0.876},
    "AI":             {"YES": 0.100, "NO": 0.900},
    "Music":          {"YES": 0.088, "NO": 0.912},
    "Movies":         {"YES": 0.097, "NO": 0.903},
}

GLOBAL_FALLBACK: dict[str, float] = {"YES": 0.381, "NO": 0.619}

# Tag priority: most specific (niche) first. When a market has multiple tags,
# we pick the highest-priority one. E.g. a market tagged both "Trump" and
# "Politics" gets assigned to "Trump" (higher priority, more specific).
TAG_PRIORITY: tuple[str, ...] = (
    "Tariffs", "Courts", "Earnings", "Earnings Calls",
    "AI", "Trump", "Geopolitics", "Elections",
    "Music", "Movies", "Crypto", "Tech",
    "Economy", "Finance", "Culture", "Politics",
)


def get_base_rate(tag: str, direction: str) -> float:
    """Return the base rate for a tag+direction. Falls back to global."""
    return TAG_BASE_RATES.get(tag, GLOBAL_FALLBACK)[direction]


def materialize_tag_markets_sql(
    table_name: str = "_tmp_tag_markets",
) -> str:
    """CH SQL to create a native Memory table mapping condition_id → primary_tag.

    Uses TAG_PRIORITY to pick the most specific tag when a market has multiple.
    Must be executed ONCE before running tag-aware queries. Uses PG engine views
    (markets → events → event_tags → tags) so it takes ~30s, but the result is
    a native table that's fast for subsequent JOINs.
    """
    # Build CASE expression for priority ordering
    tag_list = ", ".join(f"'{t}'" for t in TAG_PRIORITY)
    # Assign priority: lower number = higher priority
    when_clauses = "\n".join(
        f"            WHEN t.label = '{tag}' THEN {i}"
        for i, tag in enumerate(TAG_PRIORITY)
    )

    return f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            condition_id String,
            tag String
        ) ENGINE = Memory AS
        SELECT condition_id, tag FROM (
            SELECT
                m.condition_id,
                t.label AS tag,
                CASE
{when_clauses}
                    ELSE 999
                END AS priority,
                ROW_NUMBER() OVER (PARTITION BY m.condition_id ORDER BY
                    CASE
{when_clauses}
                        ELSE 999
                    END ASC
                ) AS rn
            FROM markets AS m
            INNER JOIN events AS e ON m.event_id = e.id
            INNER JOIN event_tags AS et ON e.id = et.event_id
            INNER JOIN tags AS t ON et.tag_id = t.id
            WHERE t.label IN ({tag_list})
        )
        WHERE rn = 1
    """


def materialize_tag_base_rates_sql(
    table_name: str = "_tmp_tag_base_rates",
) -> str:
    """CH SQL to create a native Memory table with hardcoded tag base rates.

    No PG engine joins — just INSERTs from the hardcoded TAG_BASE_RATES dict.
    Creates (tag String, direction String, base_rate Float64).
    """
    rows = []
    for tag, rates in TAG_BASE_RATES.items():
        rows.append(f"('{tag}', 'YES', {rates['YES']})")
        rows.append(f"('{tag}', 'NO', {rates['NO']})")
    values = ", ".join(rows)

    return f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            tag String,
            direction String,
            base_rate Float64
        ) ENGINE = Memory;
        INSERT INTO {table_name} VALUES {values}
    """
