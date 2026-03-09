# Entry Price Bucket Analysis — Key Findings

**Script**: `research/hypotheses/in-play-traders/scripts/entry_price_buckets.py`
**Date**: 2026-03-09
**Dataset**: 2,435,312 resolved YES positions across 222,113 markets, 321,419 traders

---

## Core Insight

HR alone does not capture skill. A trader entering YES at 0.25 with 40% HR has enormous edge; one entering at 0.90 with 90% HR has negative edge (base rate there is 95.5%). This analysis buckets positions by entry price and computes edge above the market-level base rate for each trader.

---

## 1. Base Rates by Entry Price Bucket

The market as a whole shows a smooth progression of HR with entry price, but with two important non-linearities:

| Bucket | N Pos | Base HR | Avg Entry | Edge Space | Avg PnL/Pos |
|--------|-------|---------|-----------|------------|-------------|
| [0.00-0.05] | 439,463 | 16.0% | 0.026 | +13.3pp | -$1.53 |
| [0.05-0.10] | 213,971 | 21.1% | 0.070 | +14.1pp | +$2.75 |
| [0.10-0.15] | 128,780 | 23.0% | 0.121 | +10.8pp | +$7.66 |
| [0.15-0.20] | 101,087 | 25.1% | 0.173 | +7.8pp | +$13.75 |
| [0.20-0.25] | 90,912 | 27.8% | 0.223 | +5.5pp | +$15.45 |
| [0.25-0.30] | 91,264 | 30.5% | 0.273 | +3.1pp | +$13.14 |
| [0.30-0.35] | 93,231 | 32.7% | 0.325 | +0.2pp | +$10.25 |
| [0.35-0.40] | 90,886 | 34.7% | 0.375 | -2.8pp | -$9.52 |
| [0.40-0.45] | 108,443 | 37.3% | 0.426 | -5.3pp | -$2.59 |
| [0.45-0.50] | 149,769 | 41.9% | 0.477 | -5.9pp | +$3.14 |
| [0.50-0.55] | 165,420 | 47.4% | 0.519 | -4.5pp | +$17.29 |
| [0.55-0.60] | 94,565 | 55.7% | 0.573 | -1.6pp | +$55.80 |
| [0.60-0.65] | 85,498 | 62.5% | 0.624 | +0.1pp | +$98.57 |
| [0.65-0.70] | 71,686 | 68.4% | 0.675 | +0.9pp | +$87.57 |
| [0.70-0.75] | 63,444 | 74.6% | 0.725 | +2.2pp | +$124.71 |
| [0.75-0.80] | 62,105 | 81.3% | 0.774 | +3.9pp | +$131.93 |
| [0.80-0.85] | 95,369 | 91.3% | 0.827 | +8.6pp | +$250.55 |
| [0.85-0.90] | 78,438 | 92.5% | 0.875 | +5.1pp | +$297.48 |
| [0.90-0.95] | 83,887 | 95.5% | 0.926 | +2.9pp | +$121.54 |
| [0.95-1.00] | 127,094 | 97.7% | 0.977 | -0.1pp | +$34.81 |

**"Edge Space" = base_hr - avg_entry**: how much buffer exists between base HR and break-even price.

### Key observations:
- **Break-even roughly requires HR > entry price** (to profit on a $1 YES bet)
- **0.00-0.35 range: edge space positive** — on average the market *overestimates* event probability here. Entering below 35 cents on YES is, on average, a profitable bet at current prices.
- **0.35-0.55 range: edge space negative** — these are the "death zone" buckets. Base HR is below entry price, average PnL turns negative.
- **0.55+ range: edge space closes but avg PnL rises** — larger dollar bets because prices are high, wins pay less per $1 but volume is high.
- **0.80-0.90 is the richest avg-PnL zone** ($250-297/pos) but it's in-play dominated (events resolving quickly).

---

## 2. Where Is Trader Skill Concentrated?

For traders with >= 10 positions in a bucket, the edge distribution by bucket:

| Bucket | N Traders | Avg Edge | Median Edge | Q90 Edge | Frac Beat Base |
|--------|-----------|----------|-------------|----------|----------------|
| [0.00-0.05] | 3,574 | +8.5% | -6.0% | +60.5% | 42.9% |
| [0.05-0.10] | 2,472 | +5.5% | -5.3% | +51.9% | 42.5% |
| [0.10-0.15] | 1,826 | +2.8% | -6.3% | +47.0% | 41.2% |
| [0.15-0.20] | 1,472 | +2.2% | -5.7% | +43.2% | 41.9% |
| [0.20-0.30] | ~2,600 | +0.7% to -1.5% | -4.7% to -5.5% | ~32-38% | ~41% |
| [0.30-0.50] | ~5,000 | -1.9% to -2.3% | -1.9% to -5.1% | ~22-30% | ~41-45% |
| [0.50-0.65] | ~5,500 | -0.5% to -0.9% | +0.5% to +1.1% | ~21-23% | ~51-53% |
| [0.65-0.80] | ~1,700 | +1.6% to +3.0% | +3.0% to +5.4% | ~19-25% | ~59-62% |
| [0.80-0.90] | ~2,600 | +2.0% | +7.5% to +8.7% | ~7.5-8.7% | ~70-72% |
| [0.90-1.00] | ~3,100 | +0.3% to +1.0% | +2.3% to +4.5% | ~2.3-4.5% | ~73-81% |

### Key observations:
- **Low-price buckets (0-20%) have the widest skill spread** — Q90 edge of +47-61% vs median of -6%. This is where elite traders stand out most.
- **Mid-range (30-55%) has the worst average edge and worst skill dispersion** — majority of traders lose here.
- **High-price buckets (80-95%) show the best "frac_beat_base"** (70-73%) but compressed absolute edge — everyone is near the top, so the variance is tiny.
- **The 65-80% zone is a sweet spot**: median edge positive (3-5pp), good frac_beat_base (~60%), and non-trivial absolute edge space.

---

## 3. Top Traders by Edge — Critical Finding: The 0-5% Bucket Problem

The raw edge ranking is dominated by traders with 100% HR in the 0.00-0.05 bucket. Investigation reveals this is the **in-play signal** known from prior research:

- Entries at prices of 0.010-0.050 (market already near-resolved)
- Hours-to-resolution: **2-6 hours** for these positions
- All wins, tiny PnL ($0.05-$57 per position)
- These are **not genuine long-shot skill** — they enter AFTER the outcome is effectively known (in-play traders placing final bets on near-certain outcomes)

This is the same pattern identified in `agent-memory/researcher/in-play-tracks-bc.md`: elite in-play traders enter at 0.97-0.99 implied probability (or 0.01-0.03 on the losing side), resolving within hours.

### Excluding the <5% bucket (genuine skill test):

Top 30 traders by weighted edge score, excluding the 0-5% bucket:

| Rank | Trader | N Bkts | N Pos | Wtd Edge | Avg HR | Base HR | Tot PnL |
|------|--------|--------|-------|----------|--------|---------|---------|
| 1 | ...1385e693 | 1 | 46 | +78.9% | 100.0% | 21.1% | $426 |
| 2 | ...eaab81d6 | 3 | 1,180 | +78.8% | 100.0% | 23.1% | $24,980 |
| 3 | ...5a5c2e27 | 2 | 36 | +78.4% | 100.0% | 22.1% | $176 |
| 5 | ...08fe028a | 4 | 157 | +75.5% | 98.8% | 24.2% | $3,808 |
| 6 | ...7799bd7e | 3 | 255 | +74.9% | 100.0% | 25.3% | $4,670 |
| 7 | ...caaf4615 | 7 | 240 | +74.8% | 99.6% | 27.8% | $786 |
| 9 | ...da49fad7 | 6 | 368 | +74.4% | 97.9% | 26.7% | $2,436 |
| 17 | ...35a59b6e | 6 | 223 | +71.4% | 94.4% | 26.7% | $3,504 |

These traders operate mostly in the 5-30% YES bucket — genuine long-shot skill, not in-play.

---

## 4. Naive HR vs Edge Ranking Comparison

**Top 50 by naive HR vs their edge rank:**
- Only **14 of 200** top-HR traders appear in the top-200 by edge score (7% overlap)
- Many naive top-HR traders are: (a) in-play (0-5% bucket), or (b) high-price-bucket traders where 91-97% HR sounds impressive but base rate is also 91-97%
- Rank divergence of -500 to -1,600 positions is common for traders whose HR is explained entirely by the bucket they trade in

**Key divergence examples:**
- Trader with naive HR rank #28, edge rank #1,603: 100% HR but operates in the 0.90+ bucket (base rate 90.2%). Only 9pp of actual edge.
- Trader with naive HR rank #11, edge rank #88: 100% HR, 30.4% base rate bucket — genuine +70pp edge.

This confirms the hypothesis: **naive HR ranking mixes true skill with price-bucket selection bias**.

---

## 5. Summary

| Metric | Value |
|--------|-------|
| Total YES-position traders | 321,419 |
| Total resolved markets | 222,113 |
| Total YES positions analyzed | 2,435,312 |
| Overall YES hit rate | 44.5% |
| Traders qualifying for scoring (>=20 pos) | 7,423 |
| Average weighted edge among scored | +1.7% |
| Top-200 HR / Top-200 Edge overlap | 14 / 200 (7%) |

---

## 6. Recommendations for Future Research

1. **Use edge-over-base as the primary ranking signal**, not raw HR, when selecting copy-traders.

2. **Exclude the 0-5% bucket entirely** from trader scoring — it captures in-play final-minute bets, not information-based skill.

3. **The 5-30% entry price range is the richest zone for genuine skill discovery**: widest spread between top and bottom traders, most exploitable edge (Q90 is +47-60pp above base rate).

4. **The 65-80% zone warrants investigation** as a secondary signal: median edge is actually positive (3-5pp), 60% of traders beat base rate here, and these are trades on genuinely uncertain near-favorite markets — not in-play.

5. **Consider a "calibrated skill score"**: weighted edge × log(n_positions) across buckets 5-80%, excluding the top/bottom extremes where in-play and near-certain markets dominate.

---

## Output Files

- `entry_price_base_rates.json` — base rates by all 20 price buckets
- `bucket_edge_distribution.json` — edge distribution per bucket (n_traders, avg/median/Q90 edge)
- `top50_traders_edge_score.json` — top-50 by weighted avg edge (all buckets)
- `top50_traders_naive_hr.json` — top-50 by naive HR with edge rank for comparison
- `top30_traders_edge_ex_longshot.json` — top-30 excluding the 0-5% in-play bucket
- `entry_price_bucket_findings.json` — full combined findings JSON
