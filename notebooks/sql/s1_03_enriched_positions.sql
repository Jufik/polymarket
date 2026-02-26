-- s1_03_enriched_positions.sql
-- Join positions with resolution to classify correctness.
--
-- Output columns:
--   trader, condition_id,
--   yes_bought, yes_bought_vwap, yes_sold, yes_sold_vwap,
--   no_bought, no_bought_vwap, no_sold, no_sold_vwap,
--   net_yes, net_no,
--   position    -- YES | NO | HEDGED | CLOSED
--   dir_entry   -- effective entry price for the trader's direction
--   correct     -- did the position win?
--   market_volume, trade_count, resolved_at, month
--
-- Position classification:
--   LONG YES (net_yes > 0, net_no <= 0): correct if YES won
--   LONG NO  (net_no > 0, net_yes <= 0): correct if NO won
--   HEDGED   (both > 0): use dominant side
--   CLOSED   (both <= 0): excluded (sold before resolution)
--
-- Entry price:
--   YES bettors: wavg_yes_price (what they paid for YES)
--   NO bettors:  1 - wavg_yes_price (complementary)
--
-- Depends on: _tmp_s1_positions, resolution query (s1_02)

SELECT
    p.trader,
    p.condition_id,

    -- Granular flows (for drill-down)
    p.yes_bought,  p.yes_bought_vwap,
    p.yes_sold,    p.yes_sold_vwap,
    p.no_bought,   p.no_bought_vwap,
    p.no_sold,     p.no_sold_vwap,

    p.net_yes,
    p.net_no,

    -- Direction: what is the trader betting on?
    CASE
        WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN 'YES'
        WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN 'NO'
        WHEN p.net_yes > 0.01 AND p.net_no > 0.01  THEN 'HEDGED'
        ELSE 'CLOSED'
    END AS position,

    -- Directional entry price
    CASE
        WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN p.wavg_yes_price
        WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN 1.0 - p.wavg_yes_price
        WHEN p.net_yes >= p.net_no                  THEN p.wavg_yes_price
        ELSE 1.0 - p.wavg_yes_price
    END AS dir_entry,

    -- Was the position correct?
    CASE
        WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN r.yes_won
        WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN NOT r.yes_won
        WHEN p.net_yes >= p.net_no                  THEN r.yes_won
        ELSE NOT r.yes_won
    END AS correct,

    p.volume AS market_volume,
    p.n_trades AS trade_count,
    toDateTime64(r.resolved_at, 3, 'UTC') AS resolved_at,
    formatDateTime(r.resolved_at, '%Y-%m') AS month

FROM _tmp_s1_positions p
JOIN (
    -- << s1_02_resolution.sql is inlined here >>
    SELECT
        m.condition_id, m.resolved_at, m.winner_outcome,
        coalesce(t.yes_won, false) AS yes_won
    FROM markets m
    LEFT JOIN (
        SELECT condition_id, true AS yes_won
        FROM token_market_map
        WHERE outcome = 'YES' AND winner = true
    ) t ON m.condition_id = t.condition_id
    WHERE m.resolution_value = 1
) r ON p.condition_id = r.condition_id

-- Exclude CLOSED positions (sold everything before resolution)
WHERE NOT (p.net_yes <= 0.01 AND p.net_no <= 0.01)
