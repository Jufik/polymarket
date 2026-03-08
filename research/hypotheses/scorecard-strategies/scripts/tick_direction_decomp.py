"""
Direction decomposition for Crypto tick signals.
Key question: is the 74.5% HR just structural NO bias (85.8% NO wins)?
Break signals into YES vs NO direction and check HR separately.
Also check Politics K=50 breakdown.
"""
import sys
import json
sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")
from research.db import db as get_db
from collections import defaultdict

con = get_db().con

TRAIN_END = "2025-12-05"
TEST_START = "2025-12-05"

# Build infrastructure
con.execute("""
CREATE OR REPLACE MACRO is_gambling_market(slug) AS (
    lower(slug) LIKE '%updown%' OR lower(slug) LIKE '%up-or-down%'
    OR ((lower(slug) LIKE '%-above-%' OR lower(slug) LIKE '%-below-%')
        AND (lower(slug) LIKE '%btc%' OR lower(slug) LIKE '%bitcoin%'
             OR lower(slug) LIKE '%eth%' OR lower(slug) LIKE '%ethereum%'
             OR lower(slug) LIKE '%xrp%' OR lower(slug) LIKE '%sol%'
             OR lower(slug) LIKE '%-close-%'))
);
""")
con.execute("""
CREATE OR REPLACE TABLE _tv_market_tags AS
SELECT m.condition_id, first(et.label ORDER BY et.tag_id) AS primary_tag
FROM markets m
JOIN event_tags et ON m.event_id = et.event_id
WHERE NOT is_gambling_market(m.slug)
GROUP BY m.condition_id;
""")

# Build Crypto pool
print("Building Crypto pool...")
con.execute(f"""
CREATE OR REPLACE TABLE _tv_crypto_pool AS
WITH train_pos AS (
    SELECT p.trader,
           count(DISTINCT p.condition_id) AS n_positions,
           avg(CASE
               WHEN p.position = 'YES' THEN CAST(p.yes_won AS DOUBLE)
               WHEN p.position = 'NO' THEN 1.0 - CAST(p.yes_won AS DOUBLE)
               ELSE 0.5
           END) AS hit_rate,
           avg(CASE WHEN p.volume > 0 THEN abs(p.net_usd) / p.volume ELSE 0 END) AS avg_conviction
    FROM maker_positions p
    JOIN _tv_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Crypto'
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
      AND p.volume > 0 AND p.net_usd IS NOT NULL
    GROUP BY p.trader
    HAVING count(DISTINCT p.condition_id) >= 10
       AND avg(CASE WHEN p.volume > 0 THEN abs(p.net_usd) / p.volume ELSE 0 END) >= 0.90
       AND count(*) < 10000
),
base_rate AS (
    SELECT avg(CAST(p.yes_won AS DOUBLE)) AS br
    FROM maker_positions p
    JOIN _tv_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Crypto' AND p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
)
SELECT t.trader, t.n_positions, t.hit_rate,
       (t.hit_rate - b.br) AS excess_hr
FROM train_pos t CROSS JOIN base_rate b
WHERE (t.hit_rate - b.br) > 0;
""")
n_pool = con.execute("SELECT count(*) FROM _tv_crypto_pool").fetchone()[0]
print(f"  Crypto pool: {n_pool} traders")

# Load pool entries
con.execute("CREATE OR REPLACE TABLE _tv_tick_pool (trader VARCHAR);")
pool_traders = [r[0] for r in con.execute("SELECT trader FROM _tv_crypto_pool").fetchall()]
con.executemany("INSERT INTO _tv_tick_pool VALUES (?)", [(t,) for t in pool_traders])

entries = con.execute(f"""
SELECT p.condition_id, p.trader, p.position, abs(p.net_usd) AS abs_usd,
       epoch(CAST(p.first_trade AS TIMESTAMP)) AS ft_ts,
       CAST(p.yes_won AS DOUBLE) AS yes_won,
       epoch(CAST(p.resolved_at AS TIMESTAMP)) AS res_ts
FROM maker_positions p
JOIN _tv_market_tags mt ON p.condition_id = mt.condition_id
JOIN _tv_tick_pool tp ON p.trader = tp.trader
WHERE mt.primary_tag = 'Crypto'
  AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
  AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
  AND p.volume > 0
ORDER BY p.condition_id, epoch(CAST(p.first_trade AS TIMESTAMP))
""").fetchall()
print(f"  Pool entries: {len(entries):,}")

# Simulate tick-by-tick consensus (N=5, conf=0.90)
by_market = defaultdict(list)
meta = {}
for row in entries:
    cid, trader, pos, abs_usd, ft_ts, yes_won, res_ts = row
    by_market[cid].append({"trader": trader, "position": pos, "abs_usd": abs_usd or 0.0, "ft": ft_ts})
    if cid not in meta:
        meta[cid] = {"yes_won": yes_won, "res_ts": res_ts}

MIN_N = 5
MIN_CONF = 0.90

signals = {"YES": [], "NO": []}
for cid, ents in by_market.items():
    m = meta.get(cid, {})
    yes_won = m.get("yes_won", None)
    res_ts = m.get("res_ts", None)
    if yes_won is None or res_ts is None:
        continue

    sorted_e = sorted(ents, key=lambda x: x["ft"])
    seen = set()
    n_yes = set()
    n_no = set()
    yes_vol = no_vol = 0.0

    for e in sorted_e:
        t = e["trader"]
        if t in seen:
            continue
        seen.add(t)
        if e["position"] == "YES":
            n_yes.add(t)
            yes_vol += e["abs_usd"]
        else:
            n_no.add(t)
            no_vol += e["abs_usd"]

        if len(seen) < MIN_N:
            continue

        dom = max(len(n_yes), len(n_no))
        conf = dom / len(seen)
        if conf < MIN_CONF:
            continue

        sig_ts = e["ft"]
        hold_h = (res_ts - sig_ts) / 3600.0
        if hold_h < 0:
            continue

        direction = "YES" if yes_vol >= no_vol else "NO"
        correct = (yes_won == 1.0) if direction == "YES" else (yes_won == 0.0)

        signals[direction].append({
            "correct": correct,
            "hold_h": hold_h,
            "yes_won": yes_won,
        })
        break

print("\n=== Crypto Tick Signal Direction Decomposition ===")
for direction in ["YES", "NO"]:
    sigs = signals[direction]
    n = len(sigs)
    if n == 0:
        print(f"  {direction}: 0 signals")
        continue
    n_correct = sum(1 for s in sigs if s["correct"])
    hr = n_correct / n
    holds = sorted(s["hold_h"] for s in sigs)
    med_hold = holds[n // 2]
    print(f"  {direction}: N={n}, HR={hr:.3f}, med_hold={med_hold:.0f}h")

total_sigs = sum(len(v) for v in signals.values())
total_correct = sum(sum(1 for s in v if s["correct"]) for v in signals.values())
print(f"  TOTAL: N={total_sigs}, HR={total_correct/total_sigs:.3f}")

# Test base rates for context
yes_br = con.execute(f"""
    SELECT avg(CAST(p.yes_won AS DOUBLE))
    FROM maker_positions p
    JOIN _tv_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Crypto' AND p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
""").fetchone()[0]
print(f"\n  Crypto test YES base rate: {yes_br:.4f}")
print(f"  Crypto test NO base rate: {1.0-yes_br:.4f}")
print(f"  If ALL signals were NO bets: expected HR = {1.0-yes_br:.3f}")
print(f"  YES signal HR vs YES base rate: ?")

# Now also check with broader signal range (also test N=3)
print("\n=== Crypto: N=3, conf>=0.80 (broader sweep) ===")
signals_n3 = {"YES": [], "NO": []}
for cid, ents in by_market.items():
    m = meta.get(cid, {})
    yes_won = m.get("yes_won", None)
    res_ts = m.get("res_ts", None)
    if yes_won is None or res_ts is None:
        continue
    sorted_e = sorted(ents, key=lambda x: x["ft"])
    seen = set()
    n_yes = set()
    n_no = set()
    yes_vol = no_vol = 0.0
    for e in sorted_e:
        t = e["trader"]
        if t in seen:
            continue
        seen.add(t)
        if e["position"] == "YES":
            n_yes.add(t)
            yes_vol += e["abs_usd"]
        else:
            n_no.add(t)
            no_vol += e["abs_usd"]
        if len(seen) < 3:
            continue
        dom = max(len(n_yes), len(n_no))
        conf = dom / len(seen)
        if conf < 0.80:
            continue
        sig_ts = e["ft"]
        hold_h = (res_ts - sig_ts) / 3600.0
        if hold_h < 0:
            continue
        direction = "YES" if yes_vol >= no_vol else "NO"
        correct = (yes_won == 1.0) if direction == "YES" else (yes_won == 0.0)
        signals_n3[direction].append({"correct": correct, "hold_h": hold_h})
        break

for direction in ["YES", "NO"]:
    sigs = signals_n3[direction]
    n = len(sigs)
    if n == 0:
        print(f"  {direction}: 0 signals")
        continue
    n_correct = sum(1 for s in sigs if s["correct"])
    hr = n_correct / n
    print(f"  {direction}: N={n}, HR={hr:.3f}")

# Politics direction decomposition
print("\n=== Politics NO K=50 direction check ===")
# Pool is top-50 NO traders by composite score
con.execute(f"""
CREATE OR REPLACE TABLE _tv_pol_pool AS
WITH train_pos AS (
    SELECT p.trader, count(DISTINCT p.condition_id) AS n_positions,
           avg(CASE WHEN p.position='YES' THEN CAST(p.yes_won AS DOUBLE)
                    WHEN p.position='NO' THEN 1.0-CAST(p.yes_won AS DOUBLE)
                    ELSE 0.5 END) AS hit_rate,
           avg(CASE WHEN p.volume>0 THEN abs(p.net_usd)/p.volume ELSE 0 END) AS avg_conviction
    FROM maker_positions p
    JOIN _tv_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Politics'
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
      AND p.volume > 0 AND p.net_usd IS NOT NULL
    GROUP BY p.trader
    HAVING count(*) >= 20
       AND avg(CASE WHEN p.volume>0 THEN abs(p.net_usd)/p.volume ELSE 0 END) >= 0.90
       AND count(*) < 10000
),
base_rate AS (
    SELECT avg(CAST(p.yes_won AS DOUBLE)) AS br
    FROM maker_positions p
    JOIN _tv_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag='Politics' AND p.position='YES'
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
),
scored AS (
    SELECT t.trader, t.n_positions, t.hit_rate,
           (t.hit_rate-b.br)*ln(t.n_positions+1.0) AS score,
           ROW_NUMBER() OVER (ORDER BY (t.hit_rate-b.br)*ln(t.n_positions+1.0) DESC) AS rk
    FROM train_pos t CROSS JOIN base_rate b
    WHERE (t.hit_rate-b.br) > 0
)
SELECT trader FROM scored WHERE rk <= 50;
""")

pol_pool = set(r[0] for r in con.execute("SELECT trader FROM _tv_pol_pool").fetchall())

# Load Politics NO entries
con.execute("CREATE OR REPLACE TABLE _tv_tick_pool (trader VARCHAR);")
con.executemany("INSERT INTO _tv_tick_pool VALUES (?)", [(t,) for t in pol_pool])

pol_entries = con.execute(f"""
SELECT p.condition_id, p.trader, p.position, abs(p.net_usd),
       epoch(CAST(p.first_trade AS TIMESTAMP)) AS ft_ts,
       CAST(p.yes_won AS DOUBLE), epoch(CAST(p.resolved_at AS TIMESTAMP))
FROM maker_positions p
JOIN _tv_market_tags mt ON p.condition_id = mt.condition_id
JOIN _tv_tick_pool tp ON p.trader = tp.trader
WHERE mt.primary_tag = 'Politics' AND p.position = 'NO'
  AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
  AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
  AND p.volume > 0
ORDER BY p.condition_id, epoch(CAST(p.first_trade AS TIMESTAMP))
""").fetchall()

pol_by_mkt = defaultdict(list)
pol_meta = {}
for row in pol_entries:
    cid, trader, pos, abs_usd, ft, yes_won, res_ts = row
    pol_by_mkt[cid].append({"trader": trader, "ft": ft, "abs_usd": abs_usd or 0.0})
    if cid not in pol_meta:
        pol_meta[cid] = {"yes_won": yes_won, "res_ts": res_ts}

pol_signals = []
for cid, ents in pol_by_mkt.items():
    m = pol_meta.get(cid, {})
    yes_won = m.get("yes_won", None)
    res_ts = m.get("res_ts", None)
    if yes_won is None or res_ts is None:
        continue
    sorted_e = sorted(ents, key=lambda x: x["ft"])
    seen = set()
    for e in sorted_e:
        t = e["trader"]
        if t in seen:
            continue
        seen.add(t)
        if len(seen) < 3:
            continue
        sig_ts = e["ft"]
        hold_h = (res_ts - sig_ts) / 3600.0
        if hold_h < 24.0:
            continue
        correct = (yes_won == 0.0)  # NO direction: correct when YES loses
        pol_signals.append({"correct": correct, "hold_h": hold_h, "yes_won": yes_won})
        break

n_pol = len(pol_signals)
if n_pol > 0:
    n_corr_pol = sum(1 for s in pol_signals if s["correct"])
    hr_pol = n_corr_pol / n_pol
    hold_h_pol = sorted(s["hold_h"] for s in pol_signals)
    med_hold_pol = hold_h_pol[n_pol // 2]
    print(f"  Politics NO K50: N={n_pol}, HR={hr_pol:.3f}, med_hold={med_hold_pol:.0f}h")

    # What fraction were correct YES wins vs NO wins?
    yes_wins_in_no_signals = sum(1 for s in pol_signals if s["yes_won"] == 0.0)
    print(f"  NO wins in signal set: {yes_wins_in_no_signals}/{n_pol} = {yes_wins_in_no_signals/n_pol:.3f}")

# Politics NO test base rate
pol_br = con.execute(f"""
    SELECT avg(CAST(p.yes_won AS DOUBLE))
    FROM maker_positions p
    JOIN _tv_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag='Politics' AND p.position='YES'
      AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
""").fetchone()[0]
print(f"  Politics test YES base rate: {pol_br:.4f}")
print(f"  Politics test NO base rate: {1.0-pol_br:.4f}")

print("\nDone!")
