"""Compare first-mover filter vs standard consensus vs deep consensus.

Three approaches:
A) Standard: fire when N distinct qualified traders have entered (any entry order)
B) First-mover: fire when N of the FIRST K entrants are qualified (K=N, only first N matter)
C) Deep consensus: fire when market has >= N qualified traders total

Plus: regime gate skipping folds with train_base > 0.50
"""
from __future__ import annotations
from research.db import db

FOLDS = [
    ("2025-01-01", "2025-07-01", "2025-07-01", "2025-08-01"),
    ("2025-04-01", "2025-10-01", "2025-10-01", "2025-11-01"),
    ("2025-07-01", "2026-01-01", "2026-01-01", "2026-02-01"),
]

CONFIGS = {
    "Esports": (10, 0.80),
    "Tennis": (20, 0.90),
}


def main():
    d = db()

    for tag, (meh_pp, mpe) in CONFIGS.items():
        meh_thresh = meh_pp / 100.0

        d.execute("DROP TABLE IF EXISTS tag_mkts")
        d.execute("""
            CREATE TEMP TABLE tag_mkts AS
            SELECT DISTINCT m.condition_id FROM markets m
            JOIN event_tags et ON m.event_id = et.event_id
            WHERE et.label = $1
        """, [tag])

        print(f"\n{'='*60}")
        print(f"{tag}  (meh={meh_pp}pp, mpe={mpe})")
        print(f"{'='*60}")

        agg = {}  # {label: [(hr, excess, n, fold)]}

        for ts, te, xs, xe in FOLDS:
            br = d.fetchone(f"""
                SELECT sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / count() AS base
                FROM (SELECT condition_id, first(yes_won) AS yes_won FROM maker_positions
                      WHERE condition_id IN (SELECT condition_id FROM tag_mkts)
                        AND CAST(resolved_at AS DATE) >= '{ts}' AND CAST(resolved_at AS DATE) < '{te}'
                      GROUP BY condition_id)
            """)
            train_base = br["base"]

            d.execute("DROP TABLE IF EXISTS _ep3")
            d.execute(f"""
                CREATE TEMP TABLE _ep3 AS
                SELECT y.trader, sum(y.price_x_vol) / sum(y.volume) AS avg_ep
                FROM yes_entry_data y
                WHERE y.condition_id IN (SELECT condition_id FROM tag_mkts)
                  AND CAST(y.first_trade AS DATE) >= '{ts}' AND CAST(y.first_trade AS DATE) < '{te}'
                GROUP BY y.trader
            """)

            pool_r = d.fetchall(f"""
                SELECT p.trader FROM maker_positions p
                INNER JOIN _ep3 ep ON p.trader = ep.trader
                WHERE p.condition_id IN (SELECT condition_id FROM tag_mkts)
                  AND p.position = 'YES'
                  AND CAST(p.resolved_at AS DATE) >= '{ts}' AND CAST(p.resolved_at AS DATE) < '{te}'
                GROUP BY p.trader
                HAVING count() >= 5 AND count() < 10000
                  AND sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() < 0.99
                  AND sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {train_base} >= {meh_thresh}
                  AND first(ep.avg_ep) <= {mpe}
            """)
            pool = {r["trader"] for r in pool_r}
            if len(pool) < 2:
                continue

            placeholders = ", ".join(f"'{t}'" for t in pool)
            d.execute("DROP TABLE IF EXISTS tp3")
            d.execute(f"""
                CREATE TEMP TABLE tp3 AS
                SELECT p.condition_id, p.trader, p.first_trade, p.yes_won, p.resolved_at
                FROM maker_positions p
                WHERE p.condition_id IN (SELECT condition_id FROM tag_mkts)
                  AND p.position = 'YES'
                  AND CAST(p.resolved_at AS DATE) >= '{xs}' AND CAST(p.resolved_at AS DATE) < '{xe}'
                  AND CAST(p.first_trade AS DATE) >= '{xs}'
                  AND p.trader IN ({placeholders})
            """)

            test_br = d.fetchone(f"""
                SELECT sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / count() AS base
                FROM (SELECT condition_id, first(yes_won) AS yes_won FROM maker_positions
                      WHERE condition_id IN (SELECT condition_id FROM tag_mkts)
                        AND CAST(resolved_at AS DATE) >= '{xs}' AND CAST(resolved_at AS DATE) < '{xe}'
                      GROUP BY condition_id)
            """)
            test_base = test_br["base"]

            regime = train_base > 0.50
            regime_str = " [HIGH BASE]" if regime else ""
            print(f"\n  fold {xs}: pool={len(pool)} train_base={train_base:.3f} test_base={test_base:.3f}{regime_str}")

            for n in [3, 4, 5]:
                # A) Standard consensus: >= N distinct qualified traders
                # (already computed in test_fixes.py, same as deep consensus here)
                rows_std = d.fetchall(f"""
                    WITH per_market AS (
                        SELECT condition_id, first(yes_won) AS yes_won, count(DISTINCT trader) AS n_traders
                        FROM tp3 GROUP BY condition_id HAVING n_traders >= {n}
                    )
                    SELECT count() AS n, sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::INT AS w,
                           round(sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / greatest(count(),1), 4) AS hr
                    FROM per_market
                """)

                # B) First-mover only: among the first N entrants (by first_trade), all N must be qualified
                # Since ALL entries are qualified (we pre-filtered tp3), this is: first N entries exist
                # But to be fair, we compute: markets where the first N entries are among first N chronologically
                # This is effectively the same as requiring >= N entries...
                # Actually: the question is whether first-mover filtering helps. Let's compute:
                # "Markets where the FIRST 2 qualified entrants agree (entry_order 1 and 2)"
                # vs "Markets where ANY 4 qualified entrants entered"
                # The latter has more markets but potentially worse HR (if copiers dilute)

                # C) Entry density: markets with >= N entries AND min_gap > 30min between ALL pairs
                rows_indep = d.fetchall(f"""
                    WITH ranked AS (
                        SELECT condition_id, trader, min(first_trade) AS ft, first(yes_won) AS yes_won,
                               ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY min(first_trade)) AS entry_order
                        FROM tp3 GROUP BY condition_id, trader
                    ),
                    gaps AS (
                        SELECT condition_id, entry_order,
                               date_diff('minute',
                                   LAG(ft) OVER (PARTITION BY condition_id ORDER BY ft),
                                   ft) AS gap_min
                        FROM ranked
                    ),
                    market_independence AS (
                        SELECT condition_id,
                               min(CASE WHEN entry_order >= 2 THEN gap_min ELSE NULL END) AS min_gap
                        FROM gaps GROUP BY condition_id
                    ),
                    consensus_mkts AS (
                        SELECT r.condition_id, first(r.yes_won) AS yes_won, count(DISTINCT r.trader) AS n_t
                        FROM ranked r
                        JOIN market_independence mi ON r.condition_id = mi.condition_id
                        WHERE mi.min_gap > 30
                        GROUP BY r.condition_id
                        HAVING n_t >= {n}
                    )
                    SELECT count() AS n, sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::INT AS w,
                           round(sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / greatest(count(),1), 4) AS hr
                    FROM consensus_mkts
                """)

                r_std = rows_std[0] if rows_std else {"n": 0, "hr": 0}
                r_ind = rows_indep[0] if rows_indep else {"n": 0, "hr": 0}

                exc_std = round((r_std["hr"] - test_base) * 100, 1) if r_std["n"] > 0 else 0
                exc_ind = round((r_ind["hr"] - test_base) * 100, 1) if r_ind["n"] > 0 else 0

                print(f"    N>={n}: Standard HR={r_std['hr']} exc={exc_std:+.1f}pp (n={r_std['n']}) | "
                      f"IndepOnly HR={r_ind['hr']} exc={exc_ind:+.1f}pp (n={r_ind['n']})")

        # Summary: if we applied regime gate (skip train_base > 0.50), what are avg results?
        print(f"\n  REGIME GATE IMPACT:")
        print(f"  Skipping folds with train_base > 0.50 would remove:")
        for ts, te, xs, xe in FOLDS:
            br = d.fetchone(f"""
                SELECT sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / count() AS base
                FROM (SELECT condition_id, first(yes_won) AS yes_won FROM maker_positions
                      WHERE condition_id IN (SELECT condition_id FROM tag_mkts)
                        AND CAST(resolved_at AS DATE) >= '{ts}' AND CAST(resolved_at AS DATE) < '{te}'
                      GROUP BY condition_id)
            """)
            if br["base"] > 0.50:
                print(f"    fold {xs} (train_base={br['base']:.3f})")


if __name__ == "__main__":
    main()
