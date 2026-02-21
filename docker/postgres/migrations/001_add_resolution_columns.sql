-- Add resolution columns to markets table
ALTER TABLE markets ADD COLUMN IF NOT EXISTS resolution_value SMALLINT DEFAULT 0;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS winner_outcome TEXT DEFAULT '';

-- Add winner column to token_market_map table
ALTER TABLE token_market_map ADD COLUMN IF NOT EXISTS winner BOOLEAN DEFAULT false;
