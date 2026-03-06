"""
Tag-HR-Copy Discovery Sweep v2
More efficient: fewer queries per fold via pre-materialized temp tables.
"""
import clickhouse_connect
import json
from datetime import datetime

ch = clickhouse_connect.get_client(host="192.168.0.148", port=18123, database="polymarket")

TAGS = ["Esports", "Basketball", "Tennis", "NCAA", "1H"]
MIN_TRADES_GRID = [10, 20, 30, 50]
EXCESS_HR_GRID_PP = [3, 5, 8, 10, 15]
MAX_HOLD_HOURS = 48

FOLD_TEST_PAIRS = [
    ("2025-01-01", "2025-02-01"),
    ("2025-04-01", "2025-05-01"),
    ("2025-07-01", "2025-08-01"),
    ("2025-10-01", "2025-11-01"),
    ("2026-01-01", "2026-02-01"),
]


def subtract_months(ds, months):
    from datetime import date
    import calendar
    d = date.fromisoformat(ds)
    m = d.month - months
    y = d.year + (m - 1) // 12
    m = ((m - 1) % 12) + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day).isoformat()


def q(sql):
    return ch.query(sql).result_rows

def c(sql):
    ch.command(sql)


results = {"hypothesis": "tag-hr-copy", "timestamp": datetime.now().isoformat(),
           "max_hold_hours": MAX_HOLD_HOURS, "verdict": "pending", "tags": {}}

for tag in TAGS:
    print(f"
{'='*60}", flush=True)
    print(f"TAG: {tag}", flush=True)

    # Build tag market set
    c(f"""
    CREATE OR REPLACE TABLE _tmp_thr_tag_mkts ENGINE = Memory AS
    SELECT DISTINCT m.condition_id
    FROM markets m JOIN events e ON m.event_id = e.id
    JOIN event_tags et ON et.event_id = e.id
    JOIN tags t ON t.id = et.tag_id
    WHERE t.label = '{tag}'
    """)

    all_fold_data = []

    for (test_start, test_end) in FOLD_TEST_PAIRS:
        train_end = test_start
        train_start = subtract_months(train_end, 6)
        print(f"  Fold {train_start} -> {test_start}", flush=True)

        # Base rate
        br = q(f"""
        SELECT countIf(yes_won=1), count()
        FROM (SELECT condition_id, any(yes_won) AS yes_won
              FROM maker_positions_resolved_corrected
              WHERE condition_id IN (SELECT condition_id FROM _tmp_thr_tag_mkts)
                AND toDate(resolved_at) >= '{train_start}'
                AND toDate(resolved_at) < '{train_end}'
              GROUP BY condition_id)
        """)
        yes_w, total = br[0]
        if total < 20:
            print(f"    SKIP: {total} train markets", flush=True)
            continue
        base_rate = round(yes_w / total, 4)
        print(f"    base={base_rate:.4f} n={total}", flush=True)

        # Trainer BUY-only
        c(f"""
        CREATE OR REPLACE TABLE _tmp_thr_qual_buy ENGINE = Memory AS
        SELECT trader, count() AS n, toFloat64(countIf(correct=1))/count() AS hr
        FROM maker_positions_resolved_corrected
        WHERE condition_id IN (SELECT condition_id FROM _tmp_thr_tag_mkts)
          AND position = 'YES'
          AND toDate(resolved_at) >= '{train_start}' AND toDate(resolved_at) < '{train_end}'
        GROUP BY trader HAVING n >= 5
        """)

        # Trainer directional
        c(f"""
        CREATE OR REPLACE TABLE _tmp_thr_qual_dir ENGINE = Memory AS
        SELECT trader, count() AS n, toFloat64(countIf(correct=1))/count() AS hr
        FROM maker_positions_resolved_corrected
        WHERE condition_id IN (SELECT condition_id FROM _tmp_thr_tag_mkts)
          AND toDate(resolved_at) >= '{train_start}' AND toDate(resolved_at) < '{train_end}'
        GROUP BY trader HAVING n >= 5
        """)

        # Market agg BUY
        c(f"""
        CREATE OR REPLACE TABLE _tmp_thr_mkt_buy ENGINE = Memory AS
        SELECT t.condition_id, any(t.yes_won) AS yes_won,
               dateDiff('hour', max(t.first_trade), any(t.resolved_at)) AS hold_hours,
               median(t.realized_pnl) AS median_pnl,
               groupArray(q.hr) AS trader_hrs, groupArray(q.n) AS trader_n
        FROM maker_positions_resolved_corrected t
        JOIN _tmp_thr_qual_buy q ON t.trader = q.trader
        WHERE t.condition_id IN (SELECT condition_id FROM _tmp_thr_tag_mkts)
          AND t.position = 'YES'
          AND toDate(t.resolved_at) >= '{test_start}' AND toDate(t.resolved_at) < '{test_end}'
        GROUP BY t.condition_id
        HAVING hold_hours >= 0 AND hold_hours <= {MAX_HOLD_HOURS}
        """)

        # Market agg DIR
        c(f"""
        CREATE OR REPLACE TABLE _tmp_thr_mkt_dir ENGINE = Memory AS
        SELECT t.condition_id, any(t.correct) AS correct_dir,
               dateDiff('hour', max(t.first_trade), any(t.resolved_at)) AS hold_hours,
               median(t.realized_pnl) AS median_pnl,
               groupArray(q.hr) AS trader_hrs, groupArray(q.n) AS trader_n
        FROM maker_positions_resolved_corrected t
        JOIN _tmp_thr_qual_dir q ON t.trader = q.trader
        WHERE t.condition_id IN (SELECT condition_id FROM _tmp_thr_tag_mkts)
          AND toDate(t.resolved_at) >= '{test_start}' AND toDate(t.resolved_at) < '{test_end}'
        GROUP BY t.condition_id
        HAVING hold_hours >= 0 AND hold_hours <= {MAX_HOLD_HOURS}
        """)

        combos = {}
        for mt in MIN_TRADES_GRID:
            for ep in EXCESS_HR_GRID_PP:
                thr = round(base_rate + ep / 100.0, 4)
                rb = q(f"""SELECT count(), countIf(yes_won=1),
                    round(toFloat64(countIf(yes_won=1))/count(),4),
                    round(median(median_pnl),4), round(avg(median_pnl),4),
                    round(median(hold_hours),2)
                    FROM _tmp_thr_mkt_buy
                    WHERE arrayExists((h,n)->h>={thr} AND n>={mt}, trader_hrs, trader_n)""")
                rd = q(f"""SELECT count(), countIf(correct_dir=1),
                    round(toFloat64(countIf(correct_dir=1))/count(),4),
                    round(median(median_pnl),4), round(avg(median_pnl),4),
                    round(median(hold_hours),2)
                    FROM _tmp_thr_mkt_dir
                    WHERE arrayExists((h,n)->h>={thr} AND n>={mt}, trader_hrs, trader_n)""")
                combos[f"{mt}_{ep}"] = {
                    "buy": list(rb[0]) if rb else [0]*6,
                    "dir": list(rd[0]) if rd else [0]*6,
                    "thr": thr,
                }

        all_fold_data.append({"base_rate": base_rate, "n_train": total, "combos": combos})

    # Aggregate
    from collections import defaultdict
    def agg(fold_data_list, vk):
        cf = defaultdict(list)
        for fd in fold_data_list:
            br = fd["base_rate"]
            for ck, dat in fd["combos"].items():
                row = dat[vk]
                ns, nc, hr, mp, ap, mh = row
                if ns < 5: continue
                cf[ck].append({"ns": ns, "hr": float(hr), "br": float(br),
                                "exc": float(hr)-float(br), "ap": float(ap),
                                "mp": float(mp), "mh": float(mh)})
        out = []
        for ck, fs in cf.items():
            if len(fs) < 2: continue
            mt, ep = [int(x) for x in ck.split("_")]
            ah = sum(f["hr"] for f in fs)/len(fs)
            ae = sum(f["exc"] for f in fs)/len(fs)
            aap = sum(f["ap"] for f in fs)/len(fs)
            amp = sum(f["mp"] for f in fs)/len(fs)
            amh = sum(f["mh"] for f in fs)/len(fs)
            ts = sum(f["ns"] for f in fs)
            hd = amh/24 if amh > 0 else 1.0
            cs = round(ae*aap/hd, 4)
            out.append({"params": {"min_trades": mt, "excess_hr_pp": ep},
                        "n_folds": len(fs), "avg_hr": round(ah,4),
                        "avg_excess_hr_pp": round(ae*100,2),
                        "avg_pnl_usd": round(aap,2), "median_pnl_usd": round(amp,2),
                        "avg_hold_hours": round(amh,2),
                        "total_signals": ts, "signals_per_fold": round(ts/len(fs),1),
                        "compounding_score": cs})
        out.sort(key=lambda x: x["compounding_score"], reverse=True)
        return out[:10]

    tb = agg(all_fold_data, "buy")
    td = agg(all_fold_data, "dir")
    results["tags"][tag] = {"buy_only_top10": tb, "directional_top10": td}
    print(f"  buy best: {tb[0] if tb else None}", flush=True)
    print(f"  dir best: {td[0] if td else None}", flush=True)

out = "/Users/kiefferjulien/git/polymarket/research/hypotheses/tag-hr-copy/discovery/results_raw.json"
with open(out, "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"Written to {out}", flush=True)
