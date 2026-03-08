"""
Value Hunter Copy Strategy — Research Script
============================================
Hypothesis: Traders with positive calibration_gap (HR > avg_entry_price) represent
genuinely skilled "value hunters." Copying their consensus signals should yield
excess returns vs the market's implied probability.

Methodology:
1. Identify value hunters in train period (< 2025-07-01)
2. Test consensus-of-value-hunters signals in test period (>= 2025-07-01)
3. Compare to HR-only baseline pool (top-50 by excess_hr)
4. Direction decomposition: YES excess and NO excess separately
5. Tag analysis: which tags do value hunters concentrate in?
6. Compounding score: excess_hr x avg_edge_usd / median_hold_days

Results written to: research/hypotheses/scorecard-v2-strategies/discovery/value_hunter_copy.md

DuckDB tables available:
  maker_positions - (trader, condition_id, position, correct, net_usd, net_yes, net_no, first_trade, resolved_at, ...)
  yes_entry_data  - (trader, condition_id, price_x_vol, volume, first_trade)
  markets         - (condition_id, event_id, slug, ...)
  event_tags      - (event_id, tag_id)
  markets_resolved - resolved markets subset
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

import polars as pl
from research.db import db

TRAIN_END = "2025-07-01"
TEST_START = "2025-07-01"

LOG = Path("/mnt/nvme/git/polymarket/polymarket/tmp/value_hunter.log")
LOG.parent.mkdir(exist_ok=True)

# Clear log
LOG.write_text("")


def log(msg: str) -> None:
    print(msg, flush=True)
    with open(LOG, "a") as f:
        f.write(msg + "\n")


def qdf(d, sql: str) -> pl.DataFrame:
    return d.query(sql)


def main() -> None:
    log("=== Value Hunter Copy Strategy ===")
    log(f"Train: < {TRAIN_END}, Test: >= {TEST_START}")

    d = db()

    # -------------------------------------------------------------------------
    # STEP 1: Identify value hunters from training period
    # -------------------------------------------------------------------------
    log("\n--- STEP 1: Identify Value Hunters (train period) ---")
    log("Entry price proxy: YES = yes_entry_data.price_x_vol/volume (accurate)")
    log("                   NO  = 1 - market avg YES entry (approximate)")

    # YES calibration gap: join maker_positions YES with yes_entry_data
    # CRITICAL: use first_trade < TRAIN_END and resolved_at < TRAIN_END (avoid test contamination)
    sql_train_yes = f"""
    SELECT
        p.trader,
        count(*) AS n_yes,
        avg(CAST(p.correct AS DOUBLE)) AS hr_yes,
        avg(y.price_x_vol / y.volume) AS avg_entry_yes,
        avg(CAST(p.correct AS DOUBLE) - y.price_x_vol / y.volume) AS cal_gap_yes,
        median(date_diff('day', p.first_trade, p.resolved_at)) AS med_hold_yes,
        sum(abs(p.net_usd)) AS total_usd_yes
    FROM maker_positions p
    JOIN yes_entry_data y ON p.trader = y.trader AND p.condition_id = y.condition_id
    WHERE p.position = 'YES'
      AND CAST(p.first_trade AS DATE) < '{TRAIN_END}'
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
      AND p.correct IS NOT NULL
      AND y.volume > 0
      AND y.price_x_vol / y.volume BETWEEN 0.01 AND 0.99
    GROUP BY p.trader
    HAVING n_yes >= 10
    ORDER BY cal_gap_yes DESC
    """

    log("Querying YES train positions...")
    df_yes_train = qdf(d, sql_train_yes)
    log(f"YES train pool: {len(df_yes_train):,} traders with n_yes >= 10")

    # NO calibration gap via market-level avg YES proxy
    sql_train_no = f"""
    WITH mkt_yes_price AS (
        SELECT
            p.condition_id,
            avg(y.price_x_vol / y.volume) AS avg_yes_price
        FROM maker_positions p
        JOIN yes_entry_data y ON p.trader = y.trader AND p.condition_id = y.condition_id
        WHERE p.position = 'YES'
          AND CAST(p.first_trade AS DATE) < '{TRAIN_END}'
          AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
          AND y.volume > 0
          AND y.price_x_vol / y.volume BETWEEN 0.01 AND 0.99
        GROUP BY p.condition_id
    )
    SELECT
        p.trader,
        count(*) AS n_no,
        avg(CAST(p.correct AS DOUBLE)) AS hr_no,
        avg(1.0 - m.avg_yes_price) AS avg_entry_no,
        avg(CAST(p.correct AS DOUBLE) - (1.0 - m.avg_yes_price)) AS cal_gap_no,
        median(date_diff('day', p.first_trade, p.resolved_at)) AS med_hold_no,
        sum(abs(p.net_usd)) AS total_usd_no
    FROM maker_positions p
    JOIN mkt_yes_price m ON p.condition_id = m.condition_id
    WHERE p.position = 'NO'
      AND CAST(p.first_trade AS DATE) < '{TRAIN_END}'
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
      AND p.correct IS NOT NULL
      AND m.avg_yes_price BETWEEN 0.01 AND 0.99
    GROUP BY p.trader
    HAVING n_no >= 10
    ORDER BY cal_gap_no DESC
    """

    log("Querying NO train positions...")
    df_no_train = qdf(d, sql_train_no)
    log(f"NO train pool: {len(df_no_train):,} traders with n_no >= 10")

    # -------------------------------------------------------------------------
    # Combined pool: all positions, calibration gap = HR - avg_entry_price (YES-only proxy)
    # -------------------------------------------------------------------------
    sql_combined = f"""
    WITH yes_entries AS (
        SELECT
            p.trader,
            p.condition_id,
            p.correct,
            p.position,
            p.net_usd,
            p.first_trade,
            p.resolved_at,
            y.price_x_vol / y.volume AS entry_price_yes
        FROM maker_positions p
        JOIN yes_entry_data y ON p.trader = y.trader AND p.condition_id = y.condition_id
        WHERE p.position = 'YES'
          AND CAST(p.first_trade AS DATE) < '{TRAIN_END}'
          AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
          AND p.correct IS NOT NULL
          AND y.volume > 0
          AND y.price_x_vol / y.volume BETWEEN 0.01 AND 0.99
    )
    SELECT
        trader,
        count(*) AS n_yes,
        avg(CAST(correct AS DOUBLE)) AS hr_yes,
        avg(entry_price_yes) AS avg_entry_yes,
        avg(CAST(correct AS DOUBLE) - entry_price_yes) AS cal_gap_yes,
        median(date_diff('day', first_trade, resolved_at)) AS med_hold_yes,
        sum(abs(net_usd)) AS total_usd_yes
    FROM yes_entries
    GROUP BY trader
    HAVING n_yes >= 30
    ORDER BY cal_gap_yes DESC
    """

    log("Querying combined YES pool (n_yes >= 30)...")
    df_combined = qdf(d, sql_combined)
    log(f"Traders with n_yes >= 30: {len(df_combined):,}")
    log(f"  cal_gap_yes > +5pp: {(df_combined['cal_gap_yes'] > 0.05).sum():,}")
    log(f"  cal_gap_yes > 0pp:  {(df_combined['cal_gap_yes'] > 0.00).sum():,}")
    log(f"  cal_gap_yes < -5pp: {(df_combined['cal_gap_yes'] < -0.05).sum():,}")

    # Value hunters: calibration gap > +5pp
    vh = df_combined.filter(pl.col("cal_gap_yes") > 0.05)
    log(f"\nValue hunters (cal_gap_yes > +5pp, n_yes >= 30): {len(vh):,} traders")
    if len(vh) > 0:
        log(f"  avg HR (YES): {vh['hr_yes'].mean():.1%}")
        log(f"  avg entry:    {vh['avg_entry_yes'].mean():.3f}")
        log(f"  avg cal_gap:  {vh['cal_gap_yes'].mean():.3f}")
        log(f"  median n_yes: {vh['n_yes'].median():.0f}")
        log(f"  median total_usd: ${vh['total_usd_yes'].median():.0f}")

    # Top-10, Top-25, Top-50 by calibration gap
    vh_traders = vh["trader"].to_list()
    log(f"\nTop-10 value hunters:")
    for row in vh.head(10).iter_rows(named=True):
        log(f"  {row['trader'][:12]}...: n={row['n_yes']}, HR={row['hr_yes']:.1%}, entry={row['avg_entry_yes']:.3f}, cal_gap={row['cal_gap_yes']:+.3f}, hold={row['med_hold_yes']:.0f}d")

    # -------------------------------------------------------------------------
    # STEP 2: Population base rates (YES direction, train and test)
    # -------------------------------------------------------------------------
    log("\n--- STEP 2: Population Base Rates ---")

    sql_base_train = f"""
    SELECT
        avg(CAST(correct AS DOUBLE)) AS pop_hr_all,
        count(*) AS n_total
    FROM maker_positions
    WHERE CAST(first_trade AS DATE) < '{TRAIN_END}'
      AND CAST(resolved_at AS DATE) < '{TRAIN_END}'
      AND correct IS NOT NULL
      AND position IN ('YES', 'NO')
    """
    pop_train = qdf(d, sql_base_train)
    pop_hr_train = float(pop_train["pop_hr_all"][0])
    log(f"Train population HR (all): {pop_hr_train:.1%}")

    sql_base_test = f"""
    SELECT
        position,
        count(*) AS n,
        avg(CAST(correct AS DOUBLE)) AS pop_hr
    FROM maker_positions
    WHERE CAST(first_trade AS DATE) >= '{TEST_START}'
      AND CAST(resolved_at AS DATE) >= '{TEST_START}'
      AND correct IS NOT NULL
      AND position IN ('YES', 'NO')
    GROUP BY position
    """
    pop_test = qdf(d, sql_base_test)
    test_yes_hr = float(pop_test.filter(pl.col("position") == "YES")["pop_hr"][0])
    test_no_hr = float(pop_test.filter(pl.col("position") == "NO")["pop_hr"][0])
    log(f"Test population HR:  YES={test_yes_hr:.1%}, NO={test_no_hr:.1%}")

    # -------------------------------------------------------------------------
    # STEP 3: HR-only baseline pool (top-50 by excess HR in train)
    # -------------------------------------------------------------------------
    log("\n--- STEP 3: HR-Only Baseline Pool (top-50 by excess HR in train) ---")

    sql_top50 = f"""
    SELECT
        trader,
        count(*) AS n_pos,
        avg(CAST(correct AS DOUBLE)) AS hr_all,
        avg(CAST(correct AS DOUBLE)) - {pop_hr_train:.6f} AS excess_hr
    FROM maker_positions
    WHERE CAST(first_trade AS DATE) < '{TRAIN_END}'
      AND CAST(resolved_at AS DATE) < '{TRAIN_END}'
      AND correct IS NOT NULL
      AND position IN ('YES', 'NO')
    GROUP BY trader
    HAVING n_pos >= 30
    ORDER BY excess_hr DESC
    LIMIT 50
    """

    df_top50 = qdf(d, sql_top50)
    log(f"Top-50 HR pool:")
    log(f"  avg HR:     {df_top50['hr_all'].mean():.1%}")
    log(f"  avg excess: {df_top50['excess_hr'].mean():+.1%}")

    top50_traders = set(df_top50["trader"].to_list())
    vh_set = set(vh_traders)
    overlap = vh_set & top50_traders
    log(f"\nOverlap (value hunters ∩ top-50 HR pool): {len(overlap)} traders ({len(overlap)/50:.0%} of top-50)")
    log(f"  => Value hunters are {'DIFFERENT' if len(overlap) < 20 else 'SIMILAR'} traders from HR pool")

    # -------------------------------------------------------------------------
    # STEP 4: Tag analysis — where do value hunters operate?
    # -------------------------------------------------------------------------
    log("\n--- STEP 4: Tag Analysis (value hunter YES positions, train period) ---")

    # Use only first 500 value hunters to avoid SQL IN clause overload
    vh_sql_list = vh_traders[:500]
    vh_sql_str = ", ".join(f"'{t}'" for t in vh_sql_list)

    sql_tag_profile = f"""
    WITH vh_tagged AS (
        SELECT
            p.trader,
            p.condition_id,
            p.correct,
            p.position,
            p.first_trade,
            m.event_id,
            et.tag_id
        FROM maker_positions p
        JOIN markets m ON p.condition_id = m.condition_id
        JOIN event_tags et ON m.event_id = et.event_id
        WHERE p.trader IN ({vh_sql_str})
          AND p.position = 'YES'
          AND CAST(p.first_trade AS DATE) < '{TRAIN_END}'
          AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
          AND p.correct IS NOT NULL
    )
    SELECT
        tag_id,
        count(DISTINCT trader) AS n_traders,
        count(DISTINCT condition_id) AS n_markets,
        count(*) AS n_positions,
        avg(CAST(correct AS DOUBLE)) AS hr
    FROM vh_tagged
    GROUP BY tag_id
    HAVING n_positions >= 20
    ORDER BY n_traders DESC
    LIMIT 20
    """

    df_tags = qdf(d, sql_tag_profile)
    log("Top 20 tags by trader count (value hunters):")
    for row in df_tags.iter_rows(named=True):
        log(f"  tag_id={row['tag_id']:5d}: {row['n_traders']:4d} traders, {row['n_markets']:5d} markets, {row['n_positions']:7d} positions, HR={row['hr']:.1%}")

    # -------------------------------------------------------------------------
    # STEP 5: Value hunter consensus — test period
    # -------------------------------------------------------------------------
    log("\n--- STEP 5: Value Hunter Consensus Signals (test period) ---")
    log(f"CRITICAL: filter first_trade >= {TEST_START} (only copyable test-period entries)")

    results = {}

    for consensus_n in [2, 3, 5]:
        log(f"\n  N={consensus_n} consensus:")

        # YES consensus
        DAY = "'day'"
        sql_yes_cons = f"""
        WITH vh_yes_test AS (
            SELECT
                p.trader,
                p.condition_id,
                p.correct,
                p.first_trade,
                p.resolved_at,
                abs(p.net_usd) AS pos_usd
            FROM maker_positions p
            WHERE p.trader IN ({vh_sql_str})
              AND p.position = 'YES'
              AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
              AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
              AND p.correct IS NOT NULL
        ),
        mkt_consensus AS (
            SELECT
                condition_id,
                count(DISTINCT trader) AS n_vh,
                max(first_trade) AS signal_entry,
                first(resolved_at) AS resolved_at,
                first(correct) AS correct,
                sum(pos_usd) AS signal_vol
            FROM vh_yes_test
            GROUP BY condition_id
            HAVING n_vh >= {consensus_n}
        )
        SELECT
            count(*) AS n_signals,
            avg(CAST(correct AS DOUBLE)) AS hr,
            median(date_diff({DAY}, signal_entry, resolved_at)) AS med_hold_days,
            avg(signal_vol) AS avg_vol_per_signal,
            avg(n_vh) AS avg_n_vh
        FROM mkt_consensus
        WHERE date_diff({DAY}, signal_entry, resolved_at) BETWEEN 0 AND 365
        """

        try:
            yes_res = qdf(d, sql_yes_cons)
            n_sig = int(yes_res["n_signals"][0]) if yes_res["n_signals"][0] else 0
            if n_sig > 0:
                hr = float(yes_res["hr"][0])
                hold = float(yes_res["med_hold_days"][0])
                avg_vol = float(yes_res["avg_vol_per_signal"][0])
                excess = hr - test_yes_hr
                log(f"    YES: n_signals={n_sig}, HR={hr:.1%}, excess={excess:+.1%} vs base {test_yes_hr:.1%}, hold={hold:.1f}d, avg_vol=${avg_vol:.0f}")
            else:
                log(f"    YES: 0 signals")
                yes_res = None
        except Exception as e:
            log(f"    YES error: {e}")
            yes_res = None
            n_sig = 0

        # NO consensus — need to identify which traders enter NO
        # Build NO value hunters: traders with high cal_gap on NO side
        # Join with NO calibration from step 1
        # For simplicity: use df_no_train to get NO value hunters
        # Merge: use traders that appear in BOTH yes and no cal_gap data if available
        # Primary: use all vh_traders (YES-qualified) for NO consensus too
        # This tests whether YES-skilled traders also make good NO calls

        sql_no_cons = f"""
        WITH vh_no_test AS (
            SELECT
                p.trader,
                p.condition_id,
                p.correct,
                p.first_trade,
                p.resolved_at,
                abs(p.net_usd) AS pos_usd
            FROM maker_positions p
            WHERE p.trader IN ({vh_sql_str})
              AND p.position = 'NO'
              AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
              AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
              AND p.correct IS NOT NULL
        ),
        mkt_consensus AS (
            SELECT
                condition_id,
                count(DISTINCT trader) AS n_vh,
                max(first_trade) AS signal_entry,
                first(resolved_at) AS resolved_at,
                first(correct) AS correct,
                sum(pos_usd) AS signal_vol
            FROM vh_no_test
            GROUP BY condition_id
            HAVING n_vh >= {consensus_n}
        )
        SELECT
            count(*) AS n_signals,
            avg(CAST(correct AS DOUBLE)) AS hr,
            median(date_diff({DAY}, signal_entry, resolved_at)) AS med_hold_days,
            avg(signal_vol) AS avg_vol_per_signal
        FROM mkt_consensus
        WHERE date_diff({DAY}, signal_entry, resolved_at) BETWEEN 0 AND 365
        """

        try:
            no_res = qdf(d, sql_no_cons)
            n_sig_no = int(no_res["n_signals"][0]) if no_res["n_signals"][0] else 0
            if n_sig_no > 0:
                hr_no = float(no_res["hr"][0])
                hold_no = float(no_res["med_hold_days"][0])
                avg_vol_no = float(no_res["avg_vol_per_signal"][0])
                excess_no = hr_no - test_no_hr
                log(f"    NO:  n_signals={n_sig_no}, HR={hr_no:.1%}, excess={excess_no:+.1%} vs base {test_no_hr:.1%}, hold={hold_no:.1f}d, avg_vol=${avg_vol_no:.0f}")
            else:
                log(f"    NO: 0 signals")
                no_res = None
        except Exception as e:
            log(f"    NO error: {e}")
            no_res = None

        results[consensus_n] = {"yes": yes_res, "no": no_res}

    # -------------------------------------------------------------------------
    # STEP 6: Individual copy — top-K by cal_gap (test period)
    # -------------------------------------------------------------------------
    log("\n--- STEP 6: Individual Copy Top-K (test period) ---")

    for k in [10, 25, 50]:
        top_k = vh_traders[:k]
        top_k_str = ", ".join(f"'{t}'" for t in top_k)

        sql_topk = f"""
        SELECT
            position,
            count(DISTINCT condition_id) AS n_markets,
            count(*) AS n_positions,
            avg(CAST(correct AS DOUBLE)) AS hr,
            median(date_diff('day', first_trade, resolved_at)) AS med_hold_days,
            avg(abs(net_usd)) AS avg_pos_usd
        FROM maker_positions
        WHERE trader IN ({top_k_str})
          AND CAST(first_trade AS DATE) >= '{TEST_START}'
          AND CAST(resolved_at AS DATE) >= '{TEST_START}'
          AND correct IS NOT NULL
          AND position IN ('YES', 'NO')
          AND date_diff('day', first_trade, resolved_at) BETWEEN 0 AND 365
        GROUP BY position
        ORDER BY position
        """

        df_topk = qdf(d, sql_topk)
        log(f"\n  Top-{k} value hunters (test period):")
        for row in df_topk.iter_rows(named=True):
            pos = row["position"]
            base = test_yes_hr if pos == "YES" else test_no_hr
            excess = row["hr"] - base
            log(f"    {pos}: n_markets={row['n_markets']}, HR={row['hr']:.1%}, excess={excess:+.1%}, hold={row['med_hold_days']:.1f}d, avg_pos=${row['avg_pos_usd']:.1f}")

    # -------------------------------------------------------------------------
    # STEP 7: HR baseline pool test period performance
    # -------------------------------------------------------------------------
    log("\n--- STEP 7: HR Baseline Pool (top-50) Test Period ---")

    top50_sql_str = ", ".join(f"'{t}'" for t in list(top50_traders))

    sql_hr_test = f"""
    SELECT
        position,
        count(DISTINCT condition_id) AS n_markets,
        count(*) AS n_positions,
        avg(CAST(correct AS DOUBLE)) AS hr,
        median(date_diff('day', first_trade, resolved_at)) AS med_hold_days,
        avg(abs(net_usd)) AS avg_pos_usd
    FROM maker_positions
    WHERE trader IN ({top50_sql_str})
      AND CAST(first_trade AS DATE) >= '{TEST_START}'
      AND CAST(resolved_at AS DATE) >= '{TEST_START}'
      AND correct IS NOT NULL
      AND position IN ('YES', 'NO')
      AND date_diff('day', first_trade, resolved_at) BETWEEN 0 AND 365
    GROUP BY position
    ORDER BY position
    """

    df_hr_test = qdf(d, sql_hr_test)
    log("  Top-50 HR pool (test period):")
    for row in df_hr_test.iter_rows(named=True):
        pos = row["position"]
        base = test_yes_hr if pos == "YES" else test_no_hr
        excess = row["hr"] - base
        log(f"    {pos}: n_markets={row['n_markets']}, HR={row['hr']:.1%}, excess={excess:+.1%}, hold={row['med_hold_days']:.1f}d, avg_pos=${row['avg_pos_usd']:.1f}")

    # -------------------------------------------------------------------------
    # STEP 8: Tag-level consensus, N=2, test period
    # -------------------------------------------------------------------------
    log("\n--- STEP 8: Tag-Level YES Consensus (N=2, test period) ---")

    DAY = "'day'"
    sql_tag_consensus = f"""
    WITH vh_yes_tagged AS (
        SELECT
            p.trader,
            p.condition_id,
            p.correct,
            p.first_trade,
            p.resolved_at,
            abs(p.net_usd) AS pos_usd,
            et.tag_id
        FROM maker_positions p
        JOIN markets m ON p.condition_id = m.condition_id
        JOIN event_tags et ON m.event_id = et.event_id
        WHERE p.trader IN ({vh_sql_str})
          AND p.position = 'YES'
          AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
          AND p.correct IS NOT NULL
    ),
    tag_mkt_consensus AS (
        SELECT
            tag_id,
            condition_id,
            count(DISTINCT trader) AS n_vh,
            max(first_trade) AS signal_entry,
            first(resolved_at) AS resolved_at,
            first(correct) AS correct,
            sum(pos_usd) AS signal_vol
        FROM vh_yes_tagged
        GROUP BY tag_id, condition_id
        HAVING n_vh >= 2
    )
    SELECT
        tag_id,
        count(*) AS n_signals,
        avg(CAST(correct AS DOUBLE)) AS hr,
        median(date_diff({DAY}, signal_entry, resolved_at)) AS med_hold_days,
        avg(signal_vol) AS avg_vol
    FROM tag_mkt_consensus
    WHERE date_diff({DAY}, signal_entry, resolved_at) BETWEEN 0 AND 365
    GROUP BY tag_id
    HAVING n_signals >= 3
    ORDER BY n_signals DESC
    LIMIT 20
    """

    df_tag_cons = qdf(d, sql_tag_consensus)
    log("YES consensus N=2 by tag (test period, >= 3 signals):")
    for row in df_tag_cons.iter_rows(named=True):
        excess = row["hr"] - test_yes_hr
        cs = excess * float(row["avg_vol"]) * excess / max(float(row["med_hold_days"]), 0.5)
        log(f"  tag_id={row['tag_id']:5d}: {row['n_signals']:4d} signals, HR={row['hr']:.1%}, excess={excess:+.1%}, hold={row['med_hold_days']:.1f}d, avg_vol=${row['avg_vol']:.0f}")

    # -------------------------------------------------------------------------
    # STEP 9: NO value hunter consensus by tag
    # -------------------------------------------------------------------------
    log("\n--- STEP 9: Tag-Level NO Consensus (N=2, test period) ---")

    sql_tag_no_consensus = f"""
    WITH vh_no_tagged AS (
        SELECT
            p.trader,
            p.condition_id,
            p.correct,
            p.first_trade,
            p.resolved_at,
            abs(p.net_usd) AS pos_usd,
            et.tag_id
        FROM maker_positions p
        JOIN markets m ON p.condition_id = m.condition_id
        JOIN event_tags et ON m.event_id = et.event_id
        WHERE p.trader IN ({vh_sql_str})
          AND p.position = 'NO'
          AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
          AND p.correct IS NOT NULL
    ),
    tag_mkt_consensus AS (
        SELECT
            tag_id,
            condition_id,
            count(DISTINCT trader) AS n_vh,
            max(first_trade) AS signal_entry,
            first(resolved_at) AS resolved_at,
            first(correct) AS correct,
            sum(pos_usd) AS signal_vol
        FROM vh_no_tagged
        GROUP BY tag_id, condition_id
        HAVING n_vh >= 2
    )
    SELECT
        tag_id,
        count(*) AS n_signals,
        avg(CAST(correct AS DOUBLE)) AS hr,
        median(date_diff({DAY}, signal_entry, resolved_at)) AS med_hold_days,
        avg(signal_vol) AS avg_vol
    FROM tag_mkt_consensus
    WHERE date_diff({DAY}, signal_entry, resolved_at) BETWEEN 0 AND 365
    GROUP BY tag_id
    HAVING n_signals >= 3
    ORDER BY n_signals DESC
    LIMIT 20
    """

    df_tag_no_cons = qdf(d, sql_tag_no_consensus)
    log("NO consensus N=2 by tag (test period, >= 3 signals):")
    for row in df_tag_no_cons.iter_rows(named=True):
        excess = row["hr"] - test_no_hr
        log(f"  tag_id={row['tag_id']:5d}: {row['n_signals']:4d} signals, HR={row['hr']:.1%}, excess={excess:+.1%}, hold={row['med_hold_days']:.1f}d, avg_vol=${row['avg_vol']:.0f}")

    # -------------------------------------------------------------------------
    # STEP 10: Compounding scores
    # -------------------------------------------------------------------------
    log("\n--- STEP 10: Compounding Scores ---")
    log("Formula: CS = excess_hr × avg_edge_usd / median_hold_days")
    log("  avg_edge_usd = avg_vol_per_signal × excess_hr (simplified)")

    for consensus_n in [2, 3, 5]:
        res = results[consensus_n]
        for direction, base_rate in [("yes", test_yes_hr), ("no", test_no_hr)]:
            r = res.get(direction)
            if r is not None and r["n_signals"][0] and int(r["n_signals"][0]) > 0:
                n_sig = int(r["n_signals"][0])
                hr = float(r["hr"][0])
                hold = float(r["med_hold_days"][0])
                avg_vol = float(r["avg_vol_per_signal"][0])
                excess = hr - base_rate
                edge_usd = avg_vol * abs(excess)
                cs = excess * edge_usd / max(hold, 0.5)
                log(f"  N={consensus_n} {direction.upper()}: n={n_sig}, HR={hr:.1%}, excess={excess:+.1%}, avg_vol=${avg_vol:.0f}, hold={hold:.1f}d => CS={cs:.4f}")

    # -------------------------------------------------------------------------
    # STEP 11: NO value hunters (separate from YES pool)
    # -------------------------------------------------------------------------
    log("\n--- STEP 11: NO-Specific Value Hunters (separate pool) ---")

    # Find traders with high NO calibration gap
    # Merge df_no_train with test period
    vh_no = df_no_train.filter(pl.col("cal_gap_no") > 0.05)
    log(f"NO value hunters (cal_gap_no > +5pp, n_no >= 10): {len(vh_no):,} traders")
    if len(vh_no) > 0:
        log(f"  avg HR (NO): {vh_no['hr_no'].mean():.1%}")
        log(f"  avg entry:   {vh_no['avg_entry_no'].mean():.3f}")
        log(f"  avg cal_gap: {vh_no['cal_gap_no'].mean():.3f}")

    vh_no_traders = vh_no["trader"].to_list()[:500]
    vh_no_sql_str = ", ".join(f"'{t}'" for t in vh_no_traders)

    if len(vh_no_traders) >= 2:
        sql_no_vh_cons = f"""
        WITH vh_no_test AS (
            SELECT
                p.trader,
                p.condition_id,
                p.correct,
                p.first_trade,
                p.resolved_at,
                abs(p.net_usd) AS pos_usd
            FROM maker_positions p
            WHERE p.trader IN ({vh_no_sql_str})
              AND p.position = 'NO'
              AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
              AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
              AND p.correct IS NOT NULL
        ),
        mkt_consensus AS (
            SELECT
                condition_id,
                count(DISTINCT trader) AS n_vh,
                max(first_trade) AS signal_entry,
                first(resolved_at) AS resolved_at,
                first(correct) AS correct,
                sum(pos_usd) AS signal_vol
            FROM vh_no_test
            GROUP BY condition_id
            HAVING n_vh >= 2
        )
        SELECT
            count(*) AS n_signals,
            avg(CAST(correct AS DOUBLE)) AS hr,
            median(date_diff('day', signal_entry, resolved_at)) AS med_hold_days,
            avg(signal_vol) AS avg_vol_per_signal
        FROM mkt_consensus
        WHERE date_diff('day', signal_entry, resolved_at) BETWEEN 0 AND 365
        """

        try:
            no_vh_cons = qdf(d, sql_no_vh_cons)
            n_sig = int(no_vh_cons["n_signals"][0]) if no_vh_cons["n_signals"][0] else 0
            if n_sig > 0:
                hr_no = float(no_vh_cons["hr"][0])
                hold_no = float(no_vh_cons["med_hold_days"][0])
                avg_vol_no = float(no_vh_cons["avg_vol_per_signal"][0])
                excess_no = hr_no - test_no_hr
                cs = excess_no * avg_vol_no * abs(excess_no) / max(hold_no, 0.5)
                log(f"  NO-VH pool N=2: n_signals={n_sig}, HR={hr_no:.1%}, excess={excess_no:+.1%}, hold={hold_no:.1f}d, avg_vol=${avg_vol_no:.0f}, CS={cs:.4f}")
            else:
                log("  NO-VH pool N=2: 0 signals")
        except Exception as e:
            log(f"  NO-VH pool error: {e}")

    # -------------------------------------------------------------------------
    # Collect raw numbers for report
    # -------------------------------------------------------------------------
    log("\n=== RAW NUMBERS SUMMARY ===")
    log(f"Value hunters (YES, cal_gap > +5pp, n>=30): {len(vh):,}")
    log(f"NO value hunters (cal_gap > +5pp, n>=10): {len(vh_no):,}")
    log(f"Overlap with top-50 HR pool: {len(overlap)}/50")
    log(f"Test base YES HR: {test_yes_hr:.1%}")
    log(f"Test base NO HR: {test_no_hr:.1%}")

    # Save key dataframes as JSON for the report
    results_data = {
        "n_value_hunters_yes": len(vh),
        "n_value_hunters_no": len(vh_no),
        "train_pop_hr": pop_hr_train,
        "test_yes_hr": test_yes_hr,
        "test_no_hr": test_no_hr,
        "overlap_top50_hr": len(overlap),
        "top_tag_consensus_yes": df_tag_cons.to_dicts()[:10],
        "top_tag_consensus_no": df_tag_no_cons.to_dicts()[:10],
    }

    out_json = Path("/mnt/nvme/git/polymarket/polymarket/research/hypotheses/scorecard-v2-strategies/discovery/value_hunter_raw.json")
    out_json.write_text(json.dumps(results_data, indent=2, default=str))
    log(f"\nRaw results JSON: {out_json}")
    log(f"Full log: {LOG}")
    log("\n=== DONE ===")


if __name__ == "__main__":
    main()
