-- s1_01_positions.sql
-- Build per-(trader, market) position table with granular buy/sell flows.
--
-- Output columns:
--   trader, condition_id,
--   yes_bought, yes_bought_vwap   -- YES tokens acquired + avg price paid
--   yes_sold,   yes_sold_vwap     -- YES tokens disposed + avg price received
--   no_bought,  no_bought_vwap    -- NO tokens acquired + avg price paid
--   no_sold,    no_sold_vwap      -- NO tokens disposed + avg price received
--   net_yes, net_no               -- net token holdings at resolution
--   wavg_yes_price                -- volume-weighted implied YES probability
--   volume, n_trades              -- total USD volume + trade count
--
-- Depends on: trades_raw, token_market_map, markets

CREATE TABLE IF NOT EXISTS _tmp_s1_positions
ENGINE = MergeTree()
ORDER BY (trader, condition_id)
AS
WITH

-- Step A: Map each asset_id to its market + YES/NO side
token_info AS (
    SELECT asset_id, condition_id,
           outcome = 'YES' AS is_yes
    FROM token_market_map
),

-- Step B: Only resolved markets (no open positions)
resolved AS (
    SELECT condition_id
    FROM markets
    WHERE resolution_value = 1
),

-- Step C: Enrich every trade with token side
-- price = price of the specific token traded (YES or NO)
enriched AS (
    SELECT
        t.condition_id, t.side, t.price, t.size,
        t.amount_usd, t.maker, t.taker,
        ti.is_yes
    FROM trades_raw t
    JOIN token_info ti ON t.asset_id = ti.asset_id
    WHERE t.condition_id IN (SELECT condition_id FROM resolved)
),

-- Step D: Split each trade into participant-level directional flows
--
-- side is from MAKER's perspective:
--   BUY  = maker receives tokens, taker gives tokens
--   SELL = maker gives tokens, taker receives tokens
--
-- So: maker BUY on YES → maker buys YES, taker sells YES
--     maker SELL on YES → maker sells YES, taker buys YES
--     (same logic for NO tokens)

participants AS (
    -- Maker flows
    SELECT
        assumeNotNull(maker)                              AS trader,
        condition_id,
        if(is_yes AND side='BUY',  size, 0)              AS yes_buy_qty,
        if(is_yes AND side='BUY',  price * size, 0)      AS yes_buy_cost,
        if(is_yes AND side='SELL', size, 0)              AS yes_sell_qty,
        if(is_yes AND side='SELL', price * size, 0)      AS yes_sell_cost,
        if(NOT is_yes AND side='BUY',  size, 0)          AS no_buy_qty,
        if(NOT is_yes AND side='BUY',  price * size, 0)  AS no_buy_cost,
        if(NOT is_yes AND side='SELL', size, 0)          AS no_sell_qty,
        if(NOT is_yes AND side='SELL', price * size, 0)  AS no_sell_cost,
        amount_usd,
        if(is_yes, price, 1.0 - price) * amount_usd     AS yes_px_vol
    FROM enriched
    WHERE maker IS NOT NULL AND maker != ''

    UNION ALL

    -- Taker flows (opposite of maker)
    SELECT
        assumeNotNull(taker),
        condition_id,
        if(is_yes AND side='SELL', size, 0),              -- taker buys YES when maker sells
        if(is_yes AND side='SELL', price * size, 0),
        if(is_yes AND side='BUY',  size, 0),              -- taker sells YES when maker buys
        if(is_yes AND side='BUY',  price * size, 0),
        if(NOT is_yes AND side='SELL', size, 0),
        if(NOT is_yes AND side='SELL', price * size, 0),
        if(NOT is_yes AND side='BUY',  size, 0),
        if(NOT is_yes AND side='BUY',  price * size, 0),
        amount_usd,
        if(is_yes, price, 1.0 - price) * amount_usd
    FROM enriched
    WHERE taker IS NOT NULL AND taker != ''
)

-- Step E: Aggregate to one row per (trader, market)
SELECT
    trader,
    condition_id,

    -- Granular YES flows
    sum(yes_buy_qty)                                     AS yes_bought,
    sum(yes_buy_cost) / nullIf(sum(yes_buy_qty), 0)     AS yes_bought_vwap,
    sum(yes_sell_qty)                                    AS yes_sold,
    sum(yes_sell_cost) / nullIf(sum(yes_sell_qty), 0)   AS yes_sold_vwap,

    -- Granular NO flows
    sum(no_buy_qty)                                      AS no_bought,
    sum(no_buy_cost) / nullIf(sum(no_buy_qty), 0)       AS no_bought_vwap,
    sum(no_sell_qty)                                     AS no_sold,
    sum(no_sell_cost) / nullIf(sum(no_sell_qty), 0)     AS no_sold_vwap,

    -- Net positions (what the trader holds at resolution)
    sum(yes_buy_qty) - sum(yes_sell_qty)                 AS net_yes,
    sum(no_buy_qty) - sum(no_sell_qty)                   AS net_no,

    -- Volume-weighted implied YES price (for directional entry)
    sum(yes_px_vol) / nullIf(sum(amount_usd), 0)        AS wavg_yes_price,
    sum(amount_usd)                                      AS volume,
    toUInt32(count())                                    AS n_trades

FROM participants
GROUP BY trader, condition_id
