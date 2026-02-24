-- Add published_at column (for existing deployments that don't have it)
ALTER TABLE polymarket.trades_raw ADD COLUMN IF NOT EXISTS published_at Float64 DEFAULT 0;
