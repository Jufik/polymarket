# Contrarian Value Signal — Discovery Report

**Hypothesis folder**: entry-price-quality
**Task**: Track B — buying against the crowd cheaply
**Date**: 2026-03-07
**Dataset**: 6,346,992 total positions (1,272,989 YES + 5,074,003 NO), 37,341 traders with ≥20 positions

---

## Key Finding

> **avg_entry_price alone is the best predictor of excess HR (IC = 0.877). High-entry-price traders have
> high RAW hit rate (e.g. 0.76) but ALSO high EXCESS hit rate — they are not penalized by the population
> base rate. The bargain_score composite (excess_hr × avg_profit_when_correct) achieves IC = 0.824 and
> offers a principled way to weight value, but is essentially redundant with avg_entry_price for pool
> selection. The "sure-thing penalty" hypothesis is WRONG: sure-thing pilers show HIGHER excess HR, not
> lower.**

---

## Data Infrastructure Notes

- YES positions: entry price from `yes_entry_data.price_x_vol / volume` (clean, calibrated)
- NO positions: entry price proxy = `1 - avg(YES price per market)` (approximate — no per-trader NO entry price available)
- `net_usd / net_no` is NOT a valid NO entry price proxy (correlation with yes_entry_data = 0.06)
- Gambling exclusion: markets with `slug LIKE '%updown%'` or `'%up-or-down%'` removed
- Global HR (all positions): 57.4%

---

## Population Base Rates by Entry Price Bucket

| Price Bucket | N Positions | Population HR |
|---|---|---|
| 0.00–0.20 | 1,218,135 | 14.1% |
| 0.20–0.40 | 1,014,310 | 16.5% |
| 0.40–0.60 | 1,172,895 | 47.2% |
| 0.60–0.80 | 964,626 | 85.3% |
| 0.80–1.00 | 1,977,026 | 97.4% |

**YES positions only:**

| Price Bucket | N | Population HR |
|---|---|---|
| 0.00–0.20 | 636,129 | 20.7% |
| 0.20–0.40 | 174,545 | 38.6% |
| 0.40–0.60 | 157,554 | 52.3% |
| 0.60–0.80 | 123,997 | 71.8% |
| 0.80–1.00 | 180,764 | 94.0% |

Population HR is strongly price-determined. Cheap traders face a much lower bar to exceed the base rate.

---

## Approach 1: Sure-Thing Ratio

**Definition**: fraction of CORRECT positions entered at price > 0.80.

| Sure-Thing Ratio | N Traders | Avg HR | Avg Excess HR | Avg Entry Price |
|---|---|---|---|---|
| 0–10% (low) | 3,997 | 48.6% | -8.8pp | 0.46 |
| 10–30% | 7,371 | 50.9% | -6.5pp | 0.47 |
| 30–50% | 8,029 | 55.9% | -1.5pp | 0.52 |
| 50–70% | 6,642 | 59.7% | +2.3pp | 0.58 |
| 70–100% (high) | 11,212 | 70.9% | +13.6pp | 0.69 |

**IC (sure_thing_ratio → excess_hr): 0.445**

**Interpretation**: Traders with HIGH sure-thing ratio have HIGHER excess HR. The hypothesis is reversed — this is because sure-thing traders enter markets where the price correctly reflects near-certainty, so they systematically beat the global base rate of 57.4%. The penalty logic is flawed because it ignores the bucket-specific base rate.

**Conclusion**: sure_thing_ratio is a CONFOUNDED metric. It conflates good calibration (entering cheap correct markets at high prices) with luck. DO NOT use as a negative signal.

---

## Approach 2: Edge-Per-Dollar (avg_profit_when_correct)

**Definition**: for each correct trade, profit = `1 - entry_price`. Average this across all correct trades.

| Decile | Avg Profit/Correct | Avg Entry Price | Avg HR | Avg Excess HR |
|---|---|---|---|---|
| 1 (lowest profit/correct) | 0.052 | 0.751 | 75.8% | +18.4pp |
| 2 | 0.088 | 0.698 | 71.4% | +14.1pp |
| 3 | 0.150 | 0.648 | 66.6% | +9.2pp |
| 4 | 0.213 | 0.608 | 62.4% | +5.1pp |
| 5 | 0.259 | 0.573 | 59.8% | +2.4pp |
| 6 | 0.296 | 0.543 | 56.9% | -0.5pp |
| 7 | 0.329 | 0.519 | 54.3% | -3.0pp |
| 8 | 0.362 | 0.499 | 52.8% | -4.6pp |
| 9 | 0.400 | 0.473 | 50.3% | -7.1pp |
| 10 (highest profit/correct) | 0.524 | 0.366 | 42.9% | -14.5pp |

**IC (avg_profit_when_correct → excess_hr): -0.494**

**Interpretation**: Traders who make the most profit per correct trade (cheap entry = high payout) have LOWER excess HR. The insight: buying cheap is hard — those who enter at 0.15 need only 15% resolution to be correct, but actually only resolve at 20.7% (population). High cheap-entry traders beat a low bar, not a high bar.

**Conclusion**: avg_profit_when_correct is NEGATIVELY correlated with excess HR. Using it as a positive score would penalize high-conviction traders. However, it IS useful as a **value multiplier** when combined with excess HR.

---

## Approach 3: Bargain Hunter Composite

**Definition**: `bargain_score = excess_hr × avg_profit_when_correct`

| Decile | Avg Bargain Score | Avg Profit/Correct | Avg Excess HR | Avg HR | Avg Entry Price |
|---|---|---|---|---|---|
| 1 (lowest) | -0.136 | 0.444 | -30.4pp | 27.0% | 0.347 |
| 2 | -0.052 | 0.353 | -15.8pp | 41.6% | 0.452 |
| 3 | -0.028 | 0.323 | -9.6pp | 47.8% | 0.488 |
| 4 | -0.013 | 0.274 | -5.9pp | 51.5% | 0.511 |
| 5 | -0.003 | 0.199 | -2.1pp | 55.2% | 0.546 |
| 6 | +0.005 | 0.180 | +4.3pp | 61.6% | 0.598 |
| 7 | +0.013 | 0.209 | +10.9pp | 68.3% | 0.645 |
| 8 | +0.022 | 0.176 | +21.2pp | 78.5% | 0.737 |
| 9 | +0.033 | 0.216 | +20.7pp | 78.0% | 0.704 |
| 10 (highest) | +0.073 | 0.299 | +26.2pp | 83.6% | 0.651 |

**IC (bargain_score → excess_hr): 0.824**

**Interpretation**: bargain_score has strong monotonic relationship with excess HR across deciles 5–10.
The top decile (score 0.073) shows avg HR = 83.6% with +26.2pp excess. Critically, this composite
succeeds because it weights by profit-per-correct-trade — top bargain hunters balance cheap entry
(avg 0.299 profit/correct = avg entry ~0.70) AND high excess HR (+26.2pp).

However, the avg entry price of 0.65 for top-decile bargain hunters shows they are NOT pure contrarians
buying at 0.10–0.20 — they're mid-range value buyers (0.65 entry = expensive YES, or cheap NO).

---

## Approach 4: Price-Conditional Excess HR

**Population HR and trader-level HR by bucket:**

| Bucket | Pop HR | Trader Avg HR (>=5 in bucket) | Trader Count |
|---|---|---|---|
| 0.00–0.20 | 14.1% | 9.4% | 43,194 trader-bucket pairs |
| 0.20–0.40 | 16.5% | 19.0% | 24,884 |
| 0.40–0.60 | 47.2% | 45.8% | 31,558 |
| 0.60–0.80 | 85.3% | 85.3% | 27,109 |
| 0.80–1.00 | 97.4% | 97.9% | 87,597 |

**Insight**: In the cheapest bucket (0.00–0.20), traders average 9.4% HR vs 14.1% population. Most cheap buyers are WORSE than chance. The 0.20–0.40 bucket is the only cheap bucket where traders slightly beat population (19.0% vs 16.5%).

**Quintile breakdown of excess HR in cheap bucket (<0.20):**

| Quintile (cheap excess HR) | N Traders | Avg Cheap Excess | Avg Overall Excess HR | Avg Overall HR |
|---|---|---|---|---|
| Q1 (worst) | 4,595 | -14.1pp | -6.5pp | 50.9% |
| Q2 | 4,595 | -14.1pp | -6.3pp | 51.1% |
| Q3 | 4,594 | -7.6pp | -10.8pp | 46.6% |
| Q4 | 4,594 | +4.0pp | -2.7pp | 54.7% |
| Q5 (best cheap) | 4,594 | +28.9pp | +5.7pp | 63.1% |

**Interpretation**: Traders who genuinely beat the population in cheap markets (Q5) show modest positive overall excess HR (+5.7pp). But only ~9% of traders (Q5) actually beat the cheap-bucket population. This is weak signal.

---

## Approach 5: Direction-Aware Analysis

### YES Positions by Entry Price Decile

| Price Decile | N Traders | Avg YES Entry | Avg YES HR | Avg Profit/Correct |
|---|---|---|---|---|
| 1 (cheapest) | 1,808 | 0.044 | 8.8% | 0.905 |
| 2 | 1,808 | 0.111 | 21.0% | 0.561 |
| 3 | 1,808 | 0.194 | 34.3% | 0.575 |
| 4 | 1,808 | 0.282 | 43.7% | 0.557 |
| 5 | 1,808 | 0.360 | 48.1% | 0.499 |
| 6 | 1,808 | 0.424 | 50.4% | 0.446 |
| 7 | 1,807 | 0.479 | 52.9% | 0.403 |
| 8 | 1,807 | 0.536 | 56.9% | 0.350 |
| 9 | 1,807 | 0.614 | 64.8% | 0.286 |
| 10 (most expensive) | 1,807 | 0.765 | 83.6% | 0.177 |

**Critical YES insight**: The cheapest YES traders (avg entry 0.044) achieve only 8.8% HR. Population
base rate at this price range (0–20%) is 20.7%. These contrarians are CONSISTENTLY WRONG — buying YES
at 0.04 when the market prices it at 0.04 means the crowd is right. Cheapest = worst predictors.

### NO Positions by Entry Price Bucket

| Bucket (NO price proxy) | N | Pop HR |
|---|---|---|
| 0.00–0.20 (cheap NO = YES was expensive) | 582,006 | 6.9% |
| 0.20–0.40 | 839,765 | 11.9% |
| 0.40–0.60 | 1,015,341 | 46.4% |
| 0.60–0.80 | 840,629 | 87.2% |
| 0.80–1.00 | 1,796,262 | 97.8% |

**NO insight**: Cheap NO positions (NO price 0.00–0.20, meaning YES was priced at 0.80–1.00) have only 6.9% HR. This makes sense — buying cheap NO when YES is at 90 cents rarely pays off (YES wins 97% of the time in that range). Cheap NO = expensive YES = rare wins.

---

## IC Summary Table

| Metric | IC (→ excess_hr) | Direction |
|---|---|---|
| **avg_entry_price** | **0.877** | Higher entry price → higher excess HR |
| **bargain_score** (excess_hr × profit/correct) | **0.824** | Higher composite → higher excess HR |
| sure_thing_ratio | 0.445 | More sure-thing correct → higher excess HR (reversed from hypothesis) |
| hr_in_cheap_bucket (<0.20) | 0.299 | Weak positive |
| hr_in_expensive_bucket (>0.80) | 0.251 | Weak positive |
| avg_profit_when_correct | -0.494 | Cheaper entries → LOWER excess HR |

---

## Concrete Trader Examples

### Top Bargain Hunters (highest bargain_score)

1. `0xa05c4259...` — 60 positions, HR=100%, excess=+42.6pp, avg_entry=0.027, 59/60 cheap positions
2. `0x65cf7a7c...` — 34 positions, HR=94.1%, excess=+36.8pp, avg_entry=0.120
3. `0x5849c946...` — 123 positions, HR=92.7%, excess=+35.3pp, avg_entry=0.132
4. `0x1c02dd7d...` — 390 positions, HR=90.5%, excess=+33.2pp, avg_entry=0.089
5. `0xf8ccc513...` — 939 positions, HR=90.7%, excess=+33.4pp, avg_entry=0.132

These are genuine cheap-market experts: they enter at 2–13 cent prices and win >90%. Their excess HR
comes from BEATING A LOW BASE RATE (20.7% pop) at a very high absolute rate (90%+).

### Top Sure-Thing Pilers

1. `0x711da0ef...` — 7,931 positions, HR=0.04%, excess=-57.3pp, avg_entry=0.396, sure_thing_ratio=1.0
   (All correct positions entered at >0.80, but almost no wins — this is a chronic loser)
2. `0xa4c601e7...` — 3,593 positions, HR=0.06%, excess=-57.3pp, sure_thing_ratio=1.0

These "sure-thing pilers" (sure_thing_ratio=1.0) are actually near-zero HR traders whose FEW correct
positions happened to be in expensive markets. They are not "piling on certainties" — they are just bad.

---

## Verdict

### Which metric best separates value hunters from sure-thing pilers?

**Winner: avg_entry_price** (IC = 0.877)

But the interpretation is INVERTED from the original hypothesis:
- **HIGH entry price → higher excess HR** (not penalized)
- **LOW entry price → lower excess HR** (contrarians are mostly wrong in cheap markets)

The population base rate fully explains the cheap-entry paradox: buying at 5 cents only works if you can
win >20% of the time. Most traders who buy cheap win less than 20% — they're worse than random.

**Runner-up: bargain_score** (IC = 0.824)

Useful as a multi-dimensional composite: it finds the rare traders who BOTH buy cheap AND beat the cheap
base rate. Top-decile bargain hunters (avg entry 0.65) are not extreme contrarians but mid-range value
finders who buy at 0.60–0.70 with 83.6% hit rate.

**Do NOT use**: avg_profit_when_correct alone (IC = -0.494, wrong direction), sure_thing_ratio as penalty
(IC = +0.445, opposite of intended effect).

---

## Implications for Trader Scorecard

1. **Do not penalize sure-thing buyers** — their excess HR is positive. They know what's certain.
2. **Cheap entry is NOT a virtue** — most cheap buyers are wrong more than the market.
3. **True contrarian value hunters** (buying cheap AND winning at >population rate) are extremely rare.
   Only the top quintile of cheap-bucket traders shows positive overall excess HR (+5.7pp).
4. **avg_entry_price is effectively redundant** with existing hit_rate signal (r=0.877) — entry price
   is downstream of trader calibration.
5. **Best use**: bargain_score as a tie-breaker between traders with similar excess HR — prefer the
   trader who achieves excess HR at cheaper prices (higher expected return per $ allocated).

---

## Recommended Composite Formula

```
entry_value_score = excess_hr * avg_profit_when_correct
```

- Range: typically -0.15 to +0.10
- Positive means: trader is right more than population AND profits well when right
- Use as secondary signal after excess_hr filtering (e.g. among traders with excess_hr > +5pp)
- Avoid using as primary ranker — it still correlates more with excess_hr than entry value

**Script**: `research/hypotheses/entry-price-quality/scripts/contrarian_value.py`
**Results JSON**: `research/hypotheses/entry-price-quality/discovery/contrarian_value_results.json`
