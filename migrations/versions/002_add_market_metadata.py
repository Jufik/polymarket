"""Add enriched metadata fields to markets table.

New fields:
- end_date: market end date (from CLOB/Gamma)
- description: resolution criteria text (from Gamma)
- resolution_source: URL for resolution verification (from Gamma)
- outcomes: JSON-encoded outcome labels (from Gamma)
- market_volume: total traded volume (from Gamma)

Revision ID: 002
Revises: 001
Create Date: 2026-03-13
"""
from __future__ import annotations

from alembic import op

revision: str = "002"
down_revision: str = "001"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE markets ADD COLUMN IF NOT EXISTS end_date TIMESTAMPTZ;
    ALTER TABLE markets ADD COLUMN IF NOT EXISTS description TEXT DEFAULT '';
    ALTER TABLE markets ADD COLUMN IF NOT EXISTS resolution_source TEXT DEFAULT '';
    ALTER TABLE markets ADD COLUMN IF NOT EXISTS outcomes TEXT DEFAULT '';
    ALTER TABLE markets ADD COLUMN IF NOT EXISTS market_volume DOUBLE PRECISION DEFAULT 0;
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE markets DROP COLUMN IF EXISTS end_date;
    ALTER TABLE markets DROP COLUMN IF EXISTS description;
    ALTER TABLE markets DROP COLUMN IF EXISTS resolution_source;
    ALTER TABLE markets DROP COLUMN IF EXISTS outcomes;
    ALTER TABLE markets DROP COLUMN IF EXISTS market_volume;
    """)
