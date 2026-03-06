"""
Tag-HR-Copy Round 3 Sweep — FIRST_TRADE FIX applied
Fix: AND toDate(t.first_trade) >= '{test_start}' in mkt_buy and mkt_dir
This ensures only traders who ENTERED during the test period generate signals.
Traders qualified in training but who entered markets before test_start are excluded.

Note: event_tags has no created_at column (verified) — tag universe is static, no retroactive tagging risk.

CS formula: excess_hr_pp * median_pnl_usd / median_hold_days (correct, from Round 2)
"""
import clickhouse_connect
import json
from datetime import datetime
from collections import defaultdict

ch = clickhouse_connect.get_client(host="192.168.0.148", port=18123, database="polymarket")

TAGS = ["Esports", "Tennis", "1H"]
MT = [10, 20, 30, 50]
EP = [3, 5, 8, 10, 15]
PRICE_CEIL = [0.75, 0.80, 0.85, 0.90, 1.0]
MAX_H = 48

FOLDS = [
    ("2024-07-01", "2025-01-01", "2025-01-01", "2025-02-01"),
    ("2024-10-01", "2025-04-01", "2025-04-01", "2025-05-01"),
    ("2025-01-01", "2025-07-01", "2025-07-01", "2025-08-01"),
    ("2025-04-01", "2025-10-01", "2025-10-01", "2025-11-01"),
    ("2025-07-01", "2026-01-01", "2026-01-01", "2026-02-01"),
]

def q(sql): return ch.query(sql).result_rows
def c(sql): ch.command(sql)
def sf(row, n=6):
    if not row: return [0]*n
    return [0 if x is None else x for x in row]

results = {
    "hypothesis": "tag-hr-copy",
    "round": 3,
    "timestamp": datetime.now().isoformat(),
    "max_hold_hours": MAX_H,
    "cs_formula": "excess_hr * median_pnl_usd / median_hold_days",
    "fix": "first_trade >= test_start: only test-period entries generate signals",
    "verdict": "pending",
    "tags": {},
}

for tag in TAGS:
    print(f"\n=== {tag} ===", flush=True)

    c("CREATE OR REPLACE TABLE _tmp_thr_tag_mkts ENGINE = Memory AS SELECT DISTINCT m.condition_id FROM markets m JOIN events e ON m.event_id = e.id JOIN event_tags et ON et.event_id = e.id JOIN tags t ON t.id = et.tag_id WHERE t.label = '" + tag + "'")

    all_fold_data = []

    for (ts, te, xs, xe) in FOLDS:
        # Base rate: markets resolved in TRAIN window
        br = q("SELECT countIf(yes_won=1), count() FROM (SELECT condition_id, any(yes_won) AS yes_won FROM maker_positions_resolved_corrected WHERE condition_id IN (SELECT condition_id FROM _tmp_thr_tag_mkts) AND toDate(resolved_at) >= '" + ts + "' AND toDate(resolved_at) < '" + te + "' GROUP BY condition_id)")
        yw, tot = br[0]
        if tot < 20:
            print(f"  {xs}: SKIP {tot}", flush=True)
            continue
        base = round(yw / tot, 4)
        print(f"  fold {xs}: base={base:.4f} n={tot}", flush=True)

        # Entry price: trader avg YES entry price in training window
        c("""CREATE OR REPLACE TABLE _tmp_thr_entry_price ENGINE = Memory AS
        SELECT ta.trader, sum(ta.price_x_vol) / sum(ta.volume) AS avg_ep
        FROM (SELECT * FROM trader_trade_agg FINAL) ta
        JOIN token_market_map tm ON ta.asset_id = tm.asset_id AND tm.outcome = 'YES'
        WHERE ta.condition_id IN (SELECT condition_id FROM _tmp_thr_tag_mkts)
          AND ta.volume > 0 AND ta.net_usd > 0
          AND toDate(ta.first_trade) >= '""" + ts + """' AND toDate(ta.first_trade) < '""" + te + """'
        GROUP BY ta.trader""")

        # Qualified BUY traders (hr, n, avg_ep) from training window
        c("""CREATE OR REPLACE TABLE _tmp_thr_qual_buy ENGINE = Memory AS
        SELECT p.trader, count() AS n, toFloat64(countIf(p.correct=1))/count() AS hr,
               if(any(ep.avg_ep) IS NULL, 1.0, any(ep.avg_ep)) AS avg_ep
        FROM maker_positions_resolved_corrected p
        LEFT JOIN _tmp_thr_entry_price ep ON p.trader = ep.trader
        WHERE p.condition_id IN (SELECT condition_id FROM _tmp_thr_tag_mkts)
          AND p.position = 'YES'
          AND toDate(p.resolved_at) >= '""" + ts + """' AND toDate(p.resolved_at) < '""" + te + """'
        GROUP BY p.trader HAVING n >= 5""")

        # Qualified DIR traders from training window
        c("""CREATE OR REPLACE TABLE _tmp_thr_qual_dir ENGINE = Memory AS
        SELECT p.trader, count() AS n, toFloat64(countIf(p.correct=1))/count() AS hr,
               if(any(ep.avg_ep) IS NULL, 1.0, any(ep.avg_ep)) AS avg_ep
        FROM maker_positions_resolved_corrected p
        LEFT JOIN _tmp_thr_entry_price ep ON p.trader = ep.trader
        WHERE p.condition_id IN (SELECT condition_id FROM _tmp_thr_tag_mkts)
          AND toDate(p.resolved_at) >= '""" + ts + """' AND toDate(p.resolved_at) < '""" + te + """'
        GROUP BY p.trader HAVING n >= 5""")

        # *** FIX APPLIED HERE ***
        # Market BUY: only positions where first_trade >= test_start
        # This ensures the signal (copy-trade entry) was actually actionable in test period
        c("""CREATE OR REPLACE TABLE _tmp_thr_mkt_buy ENGINE = Memory AS
        SELECT t.condition_id, any(t.yes_won) AS yes_won,
               dateDiff('hour', max(t.first_trade), any(t.resolved_at)) AS hold_hours,
               median(t.realized_pnl) AS median_pnl,
               groupArray(q.hr) AS arr_hr,
               groupArray(q.n) AS arr_n,
               groupArray(q.avg_ep) AS arr_ep
        FROM maker_positions_resolved_corrected t
        JOIN _tmp_thr_qual_buy q ON t.trader = q.trader
        WHERE t.condition_id IN (SELECT condition_id FROM _tmp_thr_tag_mkts)
          AND t.position = 'YES'
          AND toDate(t.resolved_at) >= '""" + xs + """' AND toDate(t.resolved_at) < '""" + xe + """'
          AND toDate(t.first_trade) >= '""" + xs + """'
        GROUP BY t.condition_id
        HAVING hold_hours >= 0 AND hold_hours <= """ + str(MAX_H))

        # Market DIR: only test-period entries
        c("""CREATE OR REPLACE TABLE _tmp_thr_mkt_dir ENGINE = Memory AS
        SELECT t.condition_id, any(t.correct) AS correct_dir,
               dateDiff('hour', max(t.first_trade), any(t.resolved_at)) AS hold_hours,
               median(t.realized_pnl) AS median_pnl,
               groupArray(q.hr) AS arr_hr,
               groupArray(q.n) AS arr_n,
               groupArray(q.avg_ep) AS arr_ep
        FROM maker_positions_resolved_corrected t
        JOIN _tmp_thr_qual_dir q ON t.trader = q.trader
        WHERE t.condition_id IN (SELECT condition_id FROM _tmp_thr_tag_mkts)
          AND toDate(t.resolved_at) >= '""" + xs + """' AND toDate(t.resolved_at) < '""" + xe + """'
          AND toDate(t.first_trade) >= '""" + xs + """'
        GROUP BY t.condition_id
        HAVING hold_hours >= 0 AND hold_hours <= """ + str(MAX_H))

        combos = {}
        for mt in MT:
            for ep_t in EP:
                thr = round(base + ep_t / 100.0, 4)
                for pc in PRICE_CEIL:
                    exists_expr = f"arrayExists((h,n,e)->h>={thr} AND n>={mt} AND e<={pc}, arr_hr, arr_n, arr_ep)"
                    rb = q(f"SELECT count(), countIf(yes_won=1), round(toFloat64(countIf(yes_won=1))/greatest(count(),1),4), round(median(median_pnl),4), round(avg(median_pnl),4), round(median(hold_hours),2) FROM _tmp_thr_mkt_buy WHERE {exists_expr}")
                    rd = q(f"SELECT count(), countIf(correct_dir=1), round(toFloat64(countIf(correct_dir=1))/greatest(count(),1),4), round(median(median_pnl),4), round(avg(median_pnl),4), round(median(hold_hours),2) FROM _tmp_thr_mkt_dir WHERE {exists_expr}")
                    key = f"{mt}_{ep_t}_{pc}"
                    combos[key] = {
                        "buy": sf(rb[0] if rb else None, 6),
                        "dir": sf(rd[0] if rd else None, 6),
                        "base": base,
                    }

        all_fold_data.append({"base": base, "n": tot, "fold_test": xs, "combos": combos})
        print(f"    {len(combos)} combos, fold done", flush=True)

    def agg(folds, vk):
        cf = defaultdict(list)
        for fd in folds:
            for ck, dat in fd["combos"].items():
                row = dat[vk]
                ns, nc, hr, mp, ap, mh = [float(x) if x is not None else 0 for x in row]
                if ns < 5: continue
                cf[ck].append({"ns": ns, "hr": hr, "br": float(fd["base"]),
                                "exc": hr - float(fd["base"]),
                                "mp": mp, "ap": ap, "mh": mh, "fold": fd["fold_test"]})
        out = []
        for ck, fs in cf.items():
            if len(fs) < 2: continue
            parts = ck.split("_")
            mt2, ep2, pc2 = int(parts[0]), int(parts[1]), float(parts[2])
            n = len(fs)
            ah = sum(f["hr"] for f in fs)/n
            ae = sum(f["exc"] for f in fs)/n
            amp = sum(f["mp"] for f in fs)/n
            aap = sum(f["ap"] for f in fs)/n
            ah2 = sum(f["mh"] for f in fs)/n
            ts2 = int(sum(f["ns"] for f in fs))
            hd = ah2/24 if ah2 > 0 else 1.0
            cs = round(ae * amp / hd, 4) if amp > 0 else 0
            out.append({
                "params": {"min_trades": mt2, "excess_hr_pp": ep2, "max_avg_entry_price": pc2},
                "n_folds": n, "avg_hr": round(ah,4),
                "avg_excess_hr_pp": round(ae*100,2),
                "median_pnl_usd": round(amp,2),
                "avg_pnl_usd": round(aap,2),
                "avg_hold_hours": round(ah2,2),
                "total_signals": ts2, "signals_per_fold": round(ts2/n,1),
                "compounding_score": cs,
            })
        out.sort(key=lambda x: x["compounding_score"], reverse=True)
        return out[:15]

    tb = agg(all_fold_data, "buy")
    td = agg(all_fold_data, "dir")
    results["tags"][tag] = {
        "buy_only_top15": tb,
        "directional_top15": td,
        "n_folds": len(all_fold_data),
        "fold_base_rates": [{"fold": fd["fold_test"], "base": fd["base"], "n": fd["n"]} for fd in all_fold_data],
    }

    print(f"  BUY best: {tb[0] if tb else 'NONE'}", flush=True)
    print(f"  DIR best: {td[0] if td else 'NONE'}", flush=True)

out = "/Users/kiefferjulien/git/polymarket/research/hypotheses/tag-hr-copy/discovery/results_raw_r3.json"
with open(out, "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"\nDone -> {out}", flush=True)
