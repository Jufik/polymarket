"""Initial schema — events, markets, tags, token_map, positions, fills.

Revision ID: 001
Revises: None
Create Date: 2026-02-24
"""
from __future__ import annotations

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        slug TEXT,
        title TEXT,
        description TEXT,
        category TEXT,
        start_date TEXT,
        end_date TEXT,
        closed BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS markets (
        condition_id TEXT PRIMARY KEY,
        event_id TEXT REFERENCES events(id),
        question TEXT,
        slug TEXT,
        active BOOLEAN DEFAULT TRUE,
        closed BOOLEAN DEFAULT FALSE,
        resolved BOOLEAN DEFAULT FALSE,
        accepting_orders BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS tags (
        id SERIAL PRIMARY KEY,
        event_id TEXT REFERENCES events(id),
        label TEXT NOT NULL,
        UNIQUE(event_id, label)
    );

    CREATE TABLE IF NOT EXISTS token_market_map (
        asset_id TEXT PRIMARY KEY,
        condition_id TEXT NOT NULL,
        outcome TEXT NOT NULL,
        winner BOOLEAN
    );

    CREATE TABLE IF NOT EXISTS positions (
        condition_id TEXT PRIMARY KEY,
        asset_id TEXT NOT NULL DEFAULT '',
        side TEXT NOT NULL,
        size DOUBLE PRECISION NOT NULL DEFAULT 0,
        avg_entry DOUBLE PRECISION NOT NULL DEFAULT 0,
        cost_basis DOUBLE PRECISION NOT NULL DEFAULT 0,
        last_price DOUBLE PRECISION NOT NULL DEFAULT 0,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS fills (
        id SERIAL PRIMARY KEY,
        intent_id TEXT NOT NULL,
        strategy TEXT NOT NULL,
        condition_id TEXT NOT NULL,
        asset_id TEXT NOT NULL DEFAULT '',
        side TEXT NOT NULL,
        outcome TEXT NOT NULL,
        price DOUBLE PRECISION NOT NULL,
        size_usd DOUBLE PRECISION NOT NULL,
        fee_usd DOUBLE PRECISION NOT NULL,
        filled_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_fills_condition ON fills(condition_id);
    CREATE INDEX IF NOT EXISTS idx_fills_strategy ON fills(strategy);
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS fills;
    DROP TABLE IF EXISTS positions;
    DROP TABLE IF EXISTS tags;
    DROP TABLE IF EXISTS token_market_map;
    DROP TABLE IF EXISTS markets;
    DROP TABLE IF EXISTS events;
    """)
