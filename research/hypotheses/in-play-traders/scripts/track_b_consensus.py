"""
Track B: In-Play Consensus Signal Discovery
============================================

Hypothesis: When N>=3 distinct traders all enter the same market within
the final 2-4 hours before resolution, their consensus predicts outcome.

This is distinct from scorecard strategies which pre-qualify a trader pool.
Here, the timing itself is the quality filter (urgency = information).

UPPER BOUND: All results are vectorized and will degrade 20-40pp in tick-by-tick.

Key pitfalls addressed:
1. Counting unit: aggregate to MARKET level, use count(DISTINCT trader)
2. In-play contamination: this IS in-play by design — report as upper bound
3. Signal entry = max(first_trade) across in-play traders
4. Price contamination gate: exclude markets where avg entry > gate (certainty)
5. Phantom test signals: first_trade >= test_start in test window
6. Gambling exclusion: slug NOT LIKE '%updown%' etc.
7. markets.status = 'closed' (not 'resolved')
8. events.id is the PK (join: m.event_id = e.id)

Run: PYTHONPATH=. uv run python3 research/hypotheses/in-play-traders/scripts/track_b_consensus.py
"""

import sys
import json
import time
from pathlib import Path

sys.path.insert(0, '/mnt/nvme/git/polymarket/polymarket')

import pandas as pd
from research.db import db

pd.set_option('display.max_rows', 60)
pd.set_option('display.max_columns', 20)
pd.set_option('display.width', 160)

LOG = Path('/mnt/nvme/git/polymarket/polymarket/research/hypotheses/in-play-traders/discovery/track_b_log.txt')
OUT_DIR = Path('/mnt/nvme/git/polymarket/polymarket/research/hypotheses/in-play-traders/discovery')
OUT_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    print(msg)
    with open(LOG, 'a') as f:
        f.write(msg + '\n')


def main() -> None:
    LOG.unlink(missing_ok=True)
    log("=== Track B: In-Play Consensus Discovery ===")
    log(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    con = db().con
    log("DB loaded.")

    # -------------------------------------------------------------------------
    # Step 0: Setup — valid markets and tag assignments
    # -------------------------------------------------------------------------
    log("\n--- Step 0: Setup ---")

    # Gambling exclusion: slug patterns. markets.status = 'closed' (confirmed)
    con.execute("""
    CREATE OR REPLACE TABLE _tb_valid_markets AS
    SELECT DISTINCT m.condition_id, m.event_id, m.slug
    FROM markets m
    WHERE
        m.slug NOT LIKE '%updown%'
        AND m.slug NOT LIKE '%up-or-down%'
        AND m.slug NOT LIKE '%-above-%'
        AND m.slug NOT LIKE '%-below-%'
        AND m.slug NOT LIKE '%multistrike%'
        AND m.status = 'closed'
    """)
    n_valid = con.execute("SELECT count() FROM _tb_valid_markets").fetchone()[0]
    log(f"Valid (non-gambling, closed) markets: {n_valid:,}")

    # Primary tag per market: skip system/gambling tags, min(label) for determinism
    SKIP_TAGS = "('Hide From New','Recurring','Up or Down','5M','15M','1H','Crypto Prices')"
    con.execute(f"""
    CREATE OR REPLACE TABLE _tb_market_tags AS
    SELECT
        m.condition_id,
        et.primary_tag
    FROM _tb_valid_markets m
    JOIN events e ON m.event_id = e.id
    JOIN (
        SELECT event_id, min(label) AS primary_tag
        FROM event_tags
        WHERE label NOT IN {SKIP_TAGS}
        GROUP BY event_id
    ) et ON e.id = et.event_id
    """)
    n_tagged = con.execute("SELECT count() FROM _tb_market_tags").fetchone()[0]
    log(f"Markets with non-gambling primary tag: {n_tagged:,}")

    tag_dist = con.execute("""
    SELECT primary_tag, count() as n FROM _tb_market_tags
    GROUP BY primary_tag ORDER BY n DESC LIMIT 20
    """).df()
    log(f"Top tags:\n{tag_dist.to_string(index=False)}")

    # -------------------------------------------------------------------------
    # Step 1: Build in-play positions table
    # -------------------------------------------------------------------------
    log("\n--- Step 1: Building in-play positions table ---")

    con.execute("""
    CREATE OR REPLACE TABLE _tb_inplay_positions AS
    SELECT
        p.trader,
        p.condition_id,
        p.position,
        p.yes_won,
        p.resolved_at,
        p.first_trade,
        p.volume,
        p.net_usd,
        CAST(date_diff('minute', p.first_trade, p.resolved_at) AS DOUBLE) / 60.0 AS hold_hours,
        mt.primary_tag
    FROM maker_positions p
    JOIN _tb_market_tags mt ON p.condition_id = mt.condition_id
    WHERE
        p.resolved_at IS NOT NULL
        AND p.first_trade IS NOT NULL
        AND p.first_trade <= p.resolved_at
    """)
    n_pos = con.execute("SELECT count() FROM _tb_inplay_positions").fetchone()[0]
    log(f"Total valid positions (all hold durations): {n_pos:,}")

    hold_dist = con.execute("""
    SELECT
        count() AS total,
        countIf(hold_hours <= 1) AS lte_1h,
        countIf(hold_hours <= 2) AS lte_2h,
        countIf(hold_hours <= 4) AS lte_4h,
        countIf(hold_hours <= 24) AS lte_24h,
        countIf(hold_hours > 24) AS gt_24h
    FROM _tb_inplay_positions
    """).fetchone()
    log(f"Hold distribution: <=1h={hold_dist[1]:,}, <=2h={hold_dist[2]:,}, "
        f"<=4h={hold_dist[3]:,}, <=24h={hold_dist[4]:,}, >24h={hold_dist[5]:,}")

    # -------------------------------------------------------------------------
    # Step 2: Join YES entry prices
    # -------------------------------------------------------------------------
    log("\n--- Step 2: Joining entry prices ---")

    con.execute("""
    CREATE OR REPLACE TABLE _tb_inplay_with_price AS
    SELECT
        ip.*,
        CASE
            WHEN ip.position = 'YES'
                THEN COALESCE(yed.price_x_vol / NULLIF(yed.volume, 0), 0.5)
            ELSE NULL
        END AS yes_entry_price
    FROM _tb_inplay_positions ip
    LEFT JOIN yes_entry_data yed
        ON ip.trader = yed.trader AND ip.condition_id = yed.condition_id
    """)
    price_cov = con.execute("""
    SELECT
        countIf(position = 'YES' AND yes_entry_price IS NOT NULL) AS yes_with_price,
        countIf(position = 'YES') AS yes_total,
        countIf(position = 'NO') AS no_total
    FROM _tb_inplay_with_price
    """).fetchone()
    log(f"YES positions with price: {price_cov[0]:,}/{price_cov[1]:,} "
        f"({100*price_cov[0]/max(price_cov[1],1):.1f}%), NO positions: {price_cov[2]:,}")

    # -------------------------------------------------------------------------
    # Step 3: Base rates
    # -------------------------------------------------------------------------
    log("\n--- Step 3: Base rates ---")

    base = con.execute("""
    SELECT
        count(DISTINCT condition_id) AS n_markets,
        avg(CAST(yes_won AS DOUBLE)) FILTER (WHERE position = 'YES') AS yes_position_win_rate
    FROM (
        SELECT condition_id, first(yes_won) AS yes_won, position
        FROM _tb_inplay_with_price
        GROUP BY condition_id, position
    )
    """).fetchone()
    overall_yes_base = base[1]
    overall_no_base = 1.0 - overall_yes_base
    log(f"Markets: {base[0]:,}, YES base rate: {overall_yes_base:.4f}, NO base rate: {overall_no_base:.4f}")

    tag_base_rates = con.execute("""
    WITH mkt_level AS (
        SELECT condition_id, primary_tag, first(yes_won) AS yes_won
        FROM _tb_inplay_with_price
        WHERE position = 'YES'
        GROUP BY condition_id, primary_tag
    )
    SELECT primary_tag, count() AS n_markets, avg(CAST(yes_won AS DOUBLE)) AS yes_base_rate
    FROM mkt_level
    GROUP BY primary_tag
    HAVING n_markets >= 30
    ORDER BY n_markets DESC
    LIMIT 30
    """).df()
    log(f"Per-tag base rates:\n{tag_base_rates.to_string(index=False)}")
    tag_base_rates.to_csv(OUT_DIR / 'tag_base_rates.csv', index=False)

    # -------------------------------------------------------------------------
    # Step 4: Parameter sweep
    # -------------------------------------------------------------------------
    log("\n--- Step 4: Full parameter sweep ---")

    N_VALS = [2, 3, 5, 8]
    HOLD_WINDOWS = [1.0, 2.0, 4.0]  # hours
    PRICE_GATES = [0.70, 0.80, 0.85, 999.0]  # 999 = no gate
    TRAIN_END = '2025-07-01'
    TEST_START = '2025-07-01'

    results = []
    t0 = time.time()

    total_combos = len(N_VALS) * len(HOLD_WINDOWS) * len(PRICE_GATES) * 2 * 2  # dirs x splits
    log(f"Running {total_combos} combinations...")

    for hold_w in HOLD_WINDOWS:
        for n_thresh in N_VALS:
            for price_gate in PRICE_GATES:
                price_gate_str = f"{price_gate:.2f}" if price_gate < 10 else "none"

                for direction in ['YES', 'NO']:
                    for split in ['train', 'test']:
                        if split == 'train':
                            resolve_start = '2020-01-01'
                            resolve_end = TRAIN_END
                            trade_start = '2020-01-01'
                        else:
                            resolve_start = TEST_START
                            resolve_end = '2030-01-01'
                            trade_start = TEST_START

                        # Price gate only applied to YES
                        if direction == 'YES' and price_gate < 10:
                            price_gate_clause = f"AND avg_entry_price <= {price_gate}"
                        else:
                            price_gate_clause = ""

                        wins_condition = "yes_won = 1" if direction == 'YES' else "yes_won = 0"

                        row = con.execute(f"""
                        WITH inplay_filtered AS (
                            SELECT *
                            FROM _tb_inplay_with_price
                            WHERE hold_hours <= {hold_w}
                              AND hold_hours >= 0
                              AND position = '{direction}'
                              AND CAST(resolved_at AS DATE) >= '{resolve_start}'
                              AND CAST(resolved_at AS DATE) < '{resolve_end}'
                              AND CAST(first_trade AS DATE) >= '{trade_start}'
                        ),
                        market_consensus AS (
                            -- MARKET-LEVEL aggregation (counting unit rule)
                            SELECT
                                condition_id,
                                first(yes_won) AS yes_won,
                                count(DISTINCT trader) AS n_inplay_traders,
                                max(first_trade) AS signal_entry,
                                first(resolved_at) AS resolved_at,
                                SUM(COALESCE(yes_entry_price, 0.5) * ABS(volume)) /
                                    NULLIF(SUM(ABS(volume)), 0) AS avg_entry_price,
                                SUM(ABS(net_usd)) AS total_vol_usd
                            FROM inplay_filtered
                            GROUP BY condition_id
                            HAVING n_inplay_traders >= {n_thresh}
                            {price_gate_clause}
                        )
                        SELECT
                            count() AS n_signals,
                            countIf({wins_condition}) AS n_wins,
                            countIf({wins_condition}) * 1.0 / NULLIF(count(), 0) AS hit_rate,
                            median(date_diff('hour', signal_entry, resolved_at)) AS median_hold_hours,
                            median(avg_entry_price) AS median_entry_price,
                            median(total_vol_usd) AS median_signal_vol_usd
                        FROM market_consensus
                        """).fetchone()

                        n_sigs = row[0] if row[0] else 0
                        if n_sigs >= 5:
                            results.append({
                                'split': split,
                                'direction': direction,
                                'hold_window_h': hold_w,
                                'n_thresh': n_thresh,
                                'price_gate': price_gate_str,
                                'n_signals': n_sigs,
                                'n_wins': row[1] or 0,
                                'hit_rate': round(row[2], 4) if row[2] else None,
                                'median_hold_hours': row[3],
                                'median_entry_price': round(row[4], 3) if row[4] else None,
                                'median_signal_vol_usd': round(row[5], 2) if row[5] else None,
                            })

    elapsed = time.time() - t0
    log(f"Sweep complete in {elapsed:.1f}s — {len(results)} result rows")

    df_results = pd.DataFrame(results)
    df_results.to_csv(OUT_DIR / 'sweep_raw.csv', index=False)

    # Add excess HR
    results_with_excess = []
    for r in results:
        base = overall_yes_base if r['direction'] == 'YES' else overall_no_base
        excess = (r['hit_rate'] - base) if r['hit_rate'] is not None else None
        results_with_excess.append({
            **r,
            'base_rate': round(base, 4),
            'excess_hr': round(excess, 4) if excess is not None else None,
        })

    df_full = pd.DataFrame(results_with_excess)
    df_full.to_csv(OUT_DIR / 'sweep_with_excess.csv', index=False)
    log("Saved sweep_with_excess.csv")

    # -------------------------------------------------------------------------
    # Step 5: Top combos
    # -------------------------------------------------------------------------
    log("\n--- Step 5: Best combos (train, YES, n_signals>=20) ---")
    train_yes = (df_full[
        (df_full['split'] == 'train') &
        (df_full['direction'] == 'YES') &
        (df_full['n_signals'] >= 20)
    ].sort_values('excess_hr', ascending=False).head(20))
    log(train_yes.to_string(index=False))

    log("\n--- Best combos (train, NO, n_signals>=20) ---")
    train_no = (df_full[
        (df_full['split'] == 'train') &
        (df_full['direction'] == 'NO') &
        (df_full['n_signals'] >= 20)
    ].sort_values('excess_hr', ascending=False).head(20))
    log(train_no.to_string(index=False))

    # -------------------------------------------------------------------------
    # Step 6: Tag breakdown
    # -------------------------------------------------------------------------
    log("\n--- Step 6: Tag breakdown ---")

    for hold_w, n_thresh, price_gate, direction in [
        (2.0, 3, 0.80, 'YES'),
        (4.0, 3, 0.80, 'YES'),
        (2.0, 5, 0.80, 'YES'),
        (2.0, 3, 0.80, 'NO'),
    ]:
        wins_cond = "yes_won = 1" if direction == 'YES' else "yes_won = 0"
        pg_clause = f"AND avg_entry_price <= {price_gate}" if direction == 'YES' and price_gate < 10 else ""
        log(f"\nTag breakdown: hold_w={hold_w}h n={n_thresh} gate={price_gate} dir={direction}")
        tb = con.execute(f"""
        WITH inplay_filtered AS (
            SELECT *
            FROM _tb_inplay_with_price
            WHERE hold_hours <= {hold_w}
              AND hold_hours >= 0
              AND position = '{direction}'
        ),
        market_consensus AS (
            SELECT
                condition_id,
                first(primary_tag) AS primary_tag,
                first(yes_won) AS yes_won,
                count(DISTINCT trader) AS n_inplay_traders,
                max(first_trade) AS signal_entry,
                first(resolved_at) AS resolved_at,
                SUM(COALESCE(yes_entry_price, 0.5) * ABS(volume)) /
                    NULLIF(SUM(ABS(volume)), 0) AS avg_entry_price,
                SUM(ABS(net_usd)) AS total_vol_usd
            FROM inplay_filtered
            GROUP BY condition_id
            HAVING n_inplay_traders >= {n_thresh}
            {pg_clause}
        )
        SELECT
            primary_tag,
            count() AS n_signals,
            countIf({wins_cond}) * 1.0 / count() AS hit_rate,
            median(date_diff('hour', signal_entry, resolved_at)) AS med_hold_hours,
            median(avg_entry_price) AS med_entry_price,
            median(total_vol_usd) AS med_vol_usd
        FROM market_consensus
        GROUP BY primary_tag
        HAVING n_signals >= 10
        ORDER BY n_signals DESC
        LIMIT 25
        """).df()
        log(tb.to_string(index=False))

    # -------------------------------------------------------------------------
    # Step 7: Train/test persistence
    # -------------------------------------------------------------------------
    log("\n--- Step 7: Train/test persistence (YES direction) ---")

    top_train = (df_full[
        (df_full['split'] == 'train') &
        (df_full['direction'] == 'YES') &
        (df_full['n_signals'] >= 30)
    ].sort_values('excess_hr', ascending=False).head(15))

    log("hold_w | n | gate | TRAIN_HR | TRAIN_n | TEST_HR | TEST_n | excess_train | excess_test")
    for _, row in top_train.iterrows():
        test_mask = (
            (df_full['split'] == 'test') &
            (df_full['direction'] == 'YES') &
            (df_full['hold_window_h'] == row['hold_window_h']) &
            (df_full['n_thresh'] == row['n_thresh']) &
            (df_full['price_gate'] == row['price_gate'])
        )
        test_rows = df_full[test_mask]
        if len(test_rows) > 0:
            tr = test_rows.iloc[0]
            log(f"  {row['hold_window_h']}h | n={row['n_thresh']} | gate={row['price_gate']} | "
                f"train HR={row['hit_rate']:.3f} (n={row['n_signals']}) | "
                f"test HR={tr['hit_rate']:.3f} (n={tr['n_signals']}) | "
                f"excess: {row['excess_hr']:+.3f} -> {tr['excess_hr']:+.3f}")
        else:
            log(f"  {row['hold_window_h']}h | n={row['n_thresh']} | gate={row['price_gate']} | "
                f"train HR={row['hit_rate']:.3f} (n={row['n_signals']}) | test: NO DATA")

    # -------------------------------------------------------------------------
    # Step 8: Contamination analysis
    # -------------------------------------------------------------------------
    log("\n--- Step 8: Contamination analysis (YES, hold<=4h, n>=3) ---")

    contamination = con.execute("""
    WITH inplay_mkt AS (
        SELECT
            condition_id,
            first(yes_won) AS yes_won,
            count(DISTINCT trader) AS n_traders,
            max(first_trade) AS signal_entry,
            first(resolved_at) AS resolved_at,
            SUM(COALESCE(yes_entry_price, 0.5) * ABS(volume)) /
                NULLIF(SUM(ABS(volume)), 0) AS avg_entry_price
        FROM _tb_inplay_with_price
        WHERE hold_hours <= 4
          AND position = 'YES'
        GROUP BY condition_id
        HAVING n_traders >= 3
    )
    SELECT
        CASE
            WHEN avg_entry_price IS NULL THEN 'NULL price'
            WHEN avg_entry_price < 0.30 THEN '<30% (long-shot)'
            WHEN avg_entry_price < 0.50 THEN '30-50% (uncertain)'
            WHEN avg_entry_price < 0.70 THEN '50-70% (mild lean)'
            WHEN avg_entry_price < 0.80 THEN '70-80% (strong lean)'
            WHEN avg_entry_price < 0.85 THEN '80-85% (near-certain)'
            WHEN avg_entry_price < 0.90 THEN '85-90% (contaminated?)'
            ELSE '>90% (likely contaminated)'
        END AS price_bucket,
        count() AS n_markets,
        countIf(yes_won = 1) * 1.0 / count() AS hit_rate,
        median(date_diff('hour', signal_entry, resolved_at)) AS med_hold_hours
    FROM inplay_mkt
    GROUP BY price_bucket
    ORDER BY min(COALESCE(avg_entry_price, -1))
    """).df()
    log(contamination.to_string(index=False))

    # -------------------------------------------------------------------------
    # Step 9: Signal entry hold-window distribution
    # -------------------------------------------------------------------------
    log("\n--- Step 9: Signal entry time distribution (n>=3, YES, hold<=4h) ---")

    hold_dist_mkt = con.execute("""
    WITH market_agg AS (
        SELECT
            condition_id,
            count(DISTINCT trader) AS n_traders,
            max(first_trade) AS signal_entry,
            first(resolved_at) AS resolved_at,
            date_diff('minute', max(first_trade), first(resolved_at)) / 60.0 AS hold_hours_signal
        FROM _tb_inplay_with_price
        WHERE hold_hours <= 4
          AND position = 'YES'
        GROUP BY condition_id
        HAVING n_traders >= 3
    )
    SELECT
        CASE
            WHEN hold_hours_signal < 0.083 THEN '<5min'
            WHEN hold_hours_signal < 0.25 THEN '5-15min'
            WHEN hold_hours_signal < 0.5 THEN '15-30min'
            WHEN hold_hours_signal < 1.0 THEN '30-60min'
            WHEN hold_hours_signal < 2.0 THEN '1-2h'
            WHEN hold_hours_signal < 4.0 THEN '2-4h'
            ELSE '>=4h'
        END AS signal_hold_bucket,
        count() AS n_markets,
        avg(CAST(n_traders AS DOUBLE)) AS avg_n_traders
    FROM market_agg
    GROUP BY signal_hold_bucket
    ORDER BY min(hold_hours_signal)
    """).df()
    log(f"Signal entry time:\n{hold_dist_mkt.to_string(index=False)}")

    # -------------------------------------------------------------------------
    # Step 10: Compounding score estimates
    # -------------------------------------------------------------------------
    log("\n--- Step 10: Compounding scores (train, YES, n>=20, excess>0) ---")

    top_cs = (df_full[
        (df_full['split'] == 'train') &
        (df_full['direction'] == 'YES') &
        (df_full['n_signals'] >= 20) &
        (df_full['excess_hr'].notna()) &
        (df_full['excess_hr'] > 0)
    ].sort_values('excess_hr', ascending=False).head(15))

    log("combo | HR | EP | excess | hold_days | n_sigs | CS")
    for _, row in top_cs.iterrows():
        hr = row['hit_rate'] or 0
        ep = row['median_entry_price'] or 0.5
        hold_h = row['median_hold_hours'] or 24
        hold_days = max(hold_h / 24.0, 0.04)
        excess = row['excess_hr'] or 0
        edge_usd = (hr - ep) * 100.0  # per $100 notional
        cs = excess * edge_usd / hold_days if edge_usd > 0 else 0
        log(f"  {row['hold_window_h']}h n={row['n_thresh']} gate={row['price_gate']}: "
            f"HR={hr:.3f} EP={ep:.3f} excess={excess:+.3f} "
            f"hold_days={hold_days:.2f} n={row['n_signals']} edge=${edge_usd:.1f} CS={cs:.2f}")

    # -------------------------------------------------------------------------
    # Step 11: Additional — NO direction best combos (tag breakdown)
    # -------------------------------------------------------------------------
    log("\n--- Step 11: NO direction train/test persistence ---")
    top_train_no = (df_full[
        (df_full['split'] == 'train') &
        (df_full['direction'] == 'NO') &
        (df_full['n_signals'] >= 30)
    ].sort_values('excess_hr', ascending=False).head(10))

    for _, row in top_train_no.iterrows():
        test_mask = (
            (df_full['split'] == 'test') &
            (df_full['direction'] == 'NO') &
            (df_full['hold_window_h'] == row['hold_window_h']) &
            (df_full['n_thresh'] == row['n_thresh']) &
            (df_full['price_gate'] == row['price_gate'])
        )
        test_rows = df_full[test_mask]
        if len(test_rows) > 0:
            tr = test_rows.iloc[0]
            log(f"  {row['hold_window_h']}h | n={row['n_thresh']} | gate={row['price_gate']} | "
                f"train HR={row['hit_rate']:.3f} (n={row['n_signals']}) | "
                f"test HR={tr['hit_rate']:.3f} (n={tr['n_signals']}) | "
                f"excess: {row['excess_hr']:+.3f} -> {tr['excess_hr']:+.3f}")

    # -------------------------------------------------------------------------
    # Step 12: Breakdown by hold_window to understand in-play quality gradient
    # -------------------------------------------------------------------------
    log("\n--- Step 12: Hold window quality gradient (n=3, gate=0.80, YES) ---")
    for hold_w in [1.0, 2.0, 4.0]:
        row = con.execute(f"""
        WITH market_consensus AS (
            SELECT
                condition_id,
                first(yes_won) AS yes_won,
                count(DISTINCT trader) AS n_inplay_traders,
                max(first_trade) AS signal_entry,
                first(resolved_at) AS resolved_at,
                SUM(COALESCE(yes_entry_price, 0.5) * ABS(volume)) /
                    NULLIF(SUM(ABS(volume)), 0) AS avg_entry_price
            FROM _tb_inplay_with_price
            WHERE hold_hours <= {hold_w}
              AND hold_hours >= 0
              AND position = 'YES'
            GROUP BY condition_id
            HAVING n_inplay_traders >= 3
              AND avg_entry_price <= 0.80
        )
        SELECT
            count() AS n_signals,
            countIf(yes_won = 1) * 1.0 / count() AS hit_rate,
            median(date_diff('hour', signal_entry, resolved_at)) AS med_hold_h,
            median(avg_entry_price) AS med_ep,
            -- also: what fraction are very last-minute (signal hold < 15min)?
            countIf(date_diff('minute', signal_entry, resolved_at) < 15) * 1.0 / count() AS pct_lt_15min
        FROM market_consensus
        """).fetchone()
        log(f"  hold_w={hold_w}h: n={row[0]:,} HR={row[1]:.3f} "
            f"excess={row[1]-overall_yes_base:+.3f} "
            f"med_hold={row[2]}h ep={row[3]:.3f} "
            f"pct_lt15min={row[4]:.3f}")

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    log("\n=== FINAL SUMMARY ===")
    log(f"Valid non-gambling closed markets: {n_valid:,}")
    log(f"Overall YES base rate: {overall_yes_base:.4f}")
    log(f"Overall NO base rate: {overall_no_base:.4f}")
    log(f"\n[NOTE: These are UPPER BOUNDS. Expect 20-40pp degradation in tick-by-tick.]")
    log(f"\n[BEST COMBOS — TRAIN — YES direction, n>=20, sorted by excess_hr]")
    log(train_yes.head(10).to_string(index=False))
    log(f"\n[BEST COMBOS — TRAIN — NO direction, n>=20, sorted by excess_hr]")
    log(train_no.head(5).to_string(index=False))
    log(f"\nArtifacts: {OUT_DIR}")
    log(f"Completed: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # JSON summary
    summary = {
        'overall_yes_base': overall_yes_base,
        'overall_no_base': overall_no_base,
        'n_valid_markets': n_valid,
        'best_train_yes': train_yes.head(10).to_dict('records'),
        'best_train_no': train_no.head(5).to_dict('records'),
    }
    with open(OUT_DIR / 'summary.json', 'w') as f:
        json.dump(summary, f, indent=2, default=str)
    log("Saved summary.json")


if __name__ == '__main__':
    main()
