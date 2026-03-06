"""
Tag-HR-Copy Discovery Sweep
Vectorized upper-bound sweep across 5 tags × parameter grid.
SELL dual-test: BUY-only AND directional variants.
"""
import clickhouse_connect
import json
import itertools
from datetime import datetime, date
import sys

ch = clickhouse_connect.get_client(host="192.168.0.148", port=18123, database="polymarket")

# ── Config ────────────────────────────────────────────────────────────────────
TAGS = ["Esports", "Basketball", "Tennis", "NCAA", "1H"]
MIN_TRADES_GRID = [10, 20, 30, 50]
EXCESS_HR_GRID = [3, 5, 8, 10, 15]   # pp above tag-specific base rate
LOOKBACK_MONTHS_GRID = [3, 6, 12]
MAX_HOLD_HOURS = 48

# Walk-forward: monthly folds, test each month using prior N months for training
# Use 2025 data (best coverage for all tags)
FOLDS = [
    # (train_start, train_end, test_start, test_end)
    ("2024-07-01", "2025-01-01", "2025-01-01", "2025-02-01"),
    ("2024-10-01", "2025-04-01", "2025-04-01", "2025-05-01"),
    ("2025-01-01", "2025-07-01", "2025-07-01", "2025-08-01"),
    ("2025-04-01", "2025-10-01", "2025-10-01", "2025-11-01"),
    ("2025-07-01", "2026-01-01", "2026-01-01", "2026-02-01"),
]

def run_query(sql):
    result = ch.query(sql)
    return result.result_rows

def setup_tag_markets(tag):
    """Materialize Esports market set (re-used across folds)."""
    ch.command(f"""
    CREATE OR REPLACE TABLE _tmp_sweep_tag_markets ENGINE = Memory AS
    SELECT DISTINCT m.condition_id
    FROM markets m
    JOIN events e ON m.event_id = e.id
    JOIN event_tags et ON et.event_id = e.id
    JOIN tags t ON t.id = et.tag_id
    WHERE t.label = '{tag}'
    """)

def get_base_rate(tag, train_start, train_end):
    """Tag-specific YES base rate in training window."""
    rows = run_query(f"""
    SELECT
        countIf(yes_won = 1) AS yes,
        count() AS total
    FROM (
        SELECT condition_id, any(yes_won) AS yes_won
        FROM maker_positions_resolved_corrected
        WHERE condition_id IN (SELECT condition_id FROM _tmp_sweep_tag_markets)
          AND toDate(resolved_at) >= '{train_start}' AND toDate(resolved_at) < '{train_end}'
        GROUP BY condition_id
    )
    """)
    yes, total = rows[0]
    if total < 10:
        return None, total
    return round(yes / total, 4), total

def qualify_traders_buy_only(train_start, train_end, min_trades, excess_threshold, base_rate):
    """BUY-only: position='YES' makers only."""
    ch.command(f"""
    CREATE OR REPLACE TABLE _tmp_sweep_qual ENGINE = Memory AS
    SELECT trader, count() AS n_resolved, countIf(correct=1) AS n_correct,
           countIf(correct=1) / count() AS hr
    FROM maker_positions_resolved_corrected
    WHERE condition_id IN (SELECT condition_id FROM _tmp_sweep_tag_markets)
      AND position = 'YES'
      AND toDate(resolved_at) >= '{train_start}' AND toDate(resolved_at) < '{train_end}'
    GROUP BY trader
    HAVING n_resolved >= {min_trades} AND hr >= {base_rate + excess_threshold / 100.0}
    """)
    rows = run_query("SELECT count() FROM _tmp_sweep_qual")
    return rows[0][0]

def qualify_traders_directional(train_start, train_end, min_trades, excess_threshold, base_rate):
    """Directional: YES position=bullish, NO position=bearish. 
    HR = fraction of directional bets that resolved in their favor (correct=1)."""
    ch.command(f"""
    CREATE OR REPLACE TABLE _tmp_sweep_qual ENGINE = Memory AS
    SELECT trader, count() AS n_resolved, countIf(correct=1) AS n_correct,
           countIf(correct=1) / count() AS hr
    FROM maker_positions_resolved_corrected
    WHERE condition_id IN (SELECT condition_id FROM _tmp_sweep_tag_markets)
      AND toDate(resolved_at) >= '{train_start}' AND toDate(resolved_at) < '{train_end}'
    GROUP BY trader
    HAVING n_resolved >= {min_trades} AND hr >= {base_rate + excess_threshold / 100.0}
    """)
    rows = run_query("SELECT count() FROM _tmp_sweep_qual")
    return rows[0][0]

def compute_signals(test_start, test_end, position_filter=None):
    """Compute market-level signals in test window."""
    pos_where = f"AND position = '{position_filter}'" if position_filter else ""
    ch.command(f"""
    CREATE OR REPLACE TABLE _tmp_sweep_test ENGINE = Memory AS
    SELECT trader, condition_id, yes_won, correct, realized_pnl, first_trade, resolved_at, position
    FROM maker_positions_resolved_corrected
    WHERE condition_id IN (SELECT condition_id FROM _tmp_sweep_tag_markets)
      AND trader IN (SELECT trader FROM _tmp_sweep_qual)
      AND toDate(resolved_at) >= '{test_start}' AND toDate(resolved_at) < '{test_end}'
      {pos_where}
    """)
    
    rows = run_query(f"""
    SELECT
        count() AS n_signals,
        countIf(yes_won = 1) AS n_correct,
        round(countIf(yes_won = 1) / count(), 4) AS hit_rate,
        round(median(med_pnl), 4) AS median_pnl,
        round(avg(med_pnl), 4) AS avg_pnl,
        round(median(hold_hours), 2) AS median_hold_hours
    FROM (
        SELECT
            condition_id,
            any(yes_won) AS yes_won,
            dateDiff('hour', max(first_trade), any(resolved_at)) AS hold_hours,
            median(realized_pnl) AS med_pnl
        FROM _tmp_sweep_test
        GROUP BY condition_id
        HAVING hold_hours >= 0 AND hold_hours <= {MAX_HOLD_HOURS}
    )
    """)
    return rows[0] if rows else (0, 0, 0, 0, 0, 0)

def compute_signals_directional(test_start, test_end):
    """Directional variant: each trader fires their directional bet."""
    ch.command(f"""
    CREATE OR REPLACE TABLE _tmp_sweep_test ENGINE = Memory AS
    SELECT trader, condition_id, yes_won, correct, realized_pnl, first_trade, resolved_at, position
    FROM maker_positions_resolved_corrected
    WHERE condition_id IN (SELECT condition_id FROM _tmp_sweep_tag_markets)
      AND trader IN (SELECT trader FROM _tmp_sweep_qual)
      AND toDate(resolved_at) >= '{test_start}' AND toDate(resolved_at) < '{test_end}'
    """)
    
    # For directional: market correct if position=YES and yes_won=1, OR position=NO and yes_won=0
    rows = run_query(f"""
    SELECT
        count() AS n_signals,
        countIf(correct_dir = 1) AS n_correct,
        round(countIf(correct_dir = 1) / count(), 4) AS hit_rate,
        round(median(med_pnl), 4) AS median_pnl,
        round(avg(med_pnl), 4) AS avg_pnl,
        round(median(hold_hours), 2) AS median_hold_hours
    FROM (
        SELECT
            condition_id,
            multiIf(
                any(position) = 'YES', any(yes_won),
                any(position) = 'NO', 1 - any(yes_won),
                0
            ) AS correct_dir,
            dateDiff('hour', max(first_trade), any(resolved_at)) AS hold_hours,
            median(realized_pnl) AS med_pnl
        FROM _tmp_sweep_test
        GROUP BY condition_id
        HAVING hold_hours >= 0 AND hold_hours <= {MAX_HOLD_HOURS}
    )
    """)
    return rows[0] if rows else (0, 0, 0, 0, 0, 0)

def compounding_score(excess_hr, avg_edge_usd, median_hold_hours):
    hold_days = median_hold_hours / 24.0
    if hold_days <= 0:
        return 0
    return round(excess_hr * avg_edge_usd / hold_days, 4)

# ── Main Sweep ────────────────────────────────────────────────────────────────
results = {
    "hypothesis": "tag-hr-copy",
    "timestamp": datetime.utcnow().isoformat(),
    "verdict": "pending",
    "tags": {},
}

for tag in TAGS:
    print(f"\n{'='*60}")
    print(f"TAG: {tag}")
    print('='*60)
    
    setup_tag_markets(tag)
    
    tag_results = {
        "buy_only": [],
        "directional": [],
        "base_rates_by_fold": [],
    }
    
    # Walk-forward over folds
    all_buy_combos = {}
    all_dir_combos = {}
    
    for fold in FOLDS:
        train_start, train_end, test_start, test_end = fold
        
        base_rate, n_train_markets = get_base_rate(tag, train_start, train_end)
        if base_rate is None:
            print(f"  SKIP fold {train_start}-{train_end}: too few train markets ({n_train_markets})")
            continue
        
        tag_results["base_rates_by_fold"].append({
            "train_start": train_start, "train_end": train_end,
            "yes_base_rate": base_rate, "n_train_markets": n_train_markets
        })
        print(f"  Fold {train_start}→{test_start}: base_rate={base_rate:.4f}, n_train={n_train_markets}")
        
        for min_trades, excess_pp, lookback_months in itertools.product(
            MIN_TRADES_GRID, EXCESS_HR_GRID, LOOKBACK_MONTHS_GRID
        ):
            # Adjust train window based on lookback
            # (FOLDS already define 6-month windows; just use excess_pp threshold)
            
            combo_key = (min_trades, excess_pp, lookback_months)
            
            # BUY-only
            n_qual = qualify_traders_buy_only(train_start, train_end, min_trades, excess_pp, base_rate)
            if n_qual > 0:
                sig = compute_signals(test_start, test_end, position_filter="YES")
                n_sig, n_corr, hr, med_pnl, avg_pnl, med_hold = sig
                if n_sig > 0:
                    exc_hr = round(hr - base_rate, 4)
                    cs = compounding_score(exc_hr, avg_pnl, med_hold)
                    if combo_key not in all_buy_combos:
                        all_buy_combos[combo_key] = []
                    all_buy_combos[combo_key].append({
                        "fold": f"{test_start}",
                        "n_qual": n_qual, "n_signals": n_sig, "hr": hr,
                        "base_rate": base_rate, "excess_hr": exc_hr,
                        "median_pnl": med_pnl, "avg_pnl": avg_pnl,
                        "median_hold_hours": med_hold,
                        "compounding_score": cs,
                    })
            
            # Directional
            n_qual_dir = qualify_traders_directional(train_start, train_end, min_trades, excess_pp, base_rate)
            if n_qual_dir > 0:
                sig_dir = compute_signals_directional(test_start, test_end)
                n_sig, n_corr, hr, med_pnl, avg_pnl, med_hold = sig_dir
                if n_sig > 0:
                    exc_hr = round(hr - base_rate, 4)
                    cs = compounding_score(exc_hr, avg_pnl, med_hold)
                    if combo_key not in all_dir_combos:
                        all_dir_combos[combo_key] = []
                    all_dir_combos[combo_key].append({
                        "fold": f"{test_start}",
                        "n_qual": n_qual_dir, "n_signals": n_sig, "hr": hr,
                        "base_rate": base_rate, "excess_hr": exc_hr,
                        "median_pnl": med_pnl, "avg_pnl": avg_pnl,
                        "median_hold_hours": med_hold,
                        "compounding_score": cs,
                    })
    
    # Aggregate across folds: avg metrics per combo
    def agg_combos(combo_dict, variant_label):
        agg = []
        for (min_trades, excess_pp, lookback_months), fold_results in combo_dict.items():
            if len(fold_results) < 2:
                continue  # Need at least 2 folds for stability
            avg_hr = sum(r["hr"] for r in fold_results) / len(fold_results)
            avg_excess = sum(r["excess_hr"] for r in fold_results) / len(fold_results)
            avg_pnl = sum(r["avg_pnl"] for r in fold_results) / len(fold_results)
            med_pnl = sum(r["median_pnl"] for r in fold_results) / len(fold_results)
            avg_hold = sum(r["median_hold_hours"] for r in fold_results) / len(fold_results)
            total_sigs = sum(r["n_signals"] for r in fold_results)
            avg_sigs = total_sigs / len(fold_results)
            cs = compounding_score(avg_excess, avg_pnl, avg_hold)
            agg.append({
                "variant": variant_label,
                "params": {"min_trades": min_trades, "excess_hr_pp": excess_pp, "lookback_months": lookback_months},
                "n_folds": len(fold_results),
                "avg_hr": round(avg_hr, 4),
                "avg_excess_hr_pp": round(avg_excess * 100, 2),
                "avg_pnl_usd": round(avg_pnl, 2),
                "median_pnl_usd": round(med_pnl, 2),
                "avg_hold_hours": round(avg_hold, 2),
                "total_signals": total_sigs,
                "signals_per_fold": round(avg_sigs, 1),
                "compounding_score": round(cs, 4),
            })
        agg.sort(key=lambda x: x["compounding_score"], reverse=True)
        return agg[:10]
    
    top_buy = agg_combos(all_buy_combos, "buy_only")
    top_dir = agg_combos(all_dir_combos, "directional")
    
    tag_results["buy_only"] = top_buy
    tag_results["directional"] = top_dir
    
    results["tags"][tag] = tag_results
    
    print(f"  BUY-only top combo: {top_buy[0] if top_buy else 'NONE'}")
    print(f"  Directional top combo: {top_dir[0] if top_dir else 'NONE'}")

# Write results
output_path = "/Users/kiefferjulien/git/polymarket/research/hypotheses/tag-hr-copy/discovery/results_raw.json"
with open(output_path, "w") as f:
    json.dump(results, f, indent=2, default=str)

print(f"\nResults written to {output_path}")
