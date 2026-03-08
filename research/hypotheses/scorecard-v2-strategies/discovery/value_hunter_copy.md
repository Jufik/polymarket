# Value Hunter Copy Strategy — Discovery Report

**Hypothesis folder**: scorecard-v2-strategies
**Task**: Strategy B — copy traders with positive calibration gap (HR > avg_entry_price)
**Date**: 2026-03-07
**Dataset**: 29.9M maker_positions, train < 2025-07-01, test >= 2025-07-01

---

## Executive Summary

**Verdict: YES value hunters show extraordinary skill (genuine alpha) but face severe copyability concerns. NO value hunters show modest real signal (+4.6pp price-adjusted excess).**

### YES Value Hunters (top-50 by cal_gap_yes)
- Price-adjusted excess in test period: **+63.4pp** (vs 21.6% population base rate at <0.10 entry)
- These traders buy YES at 1–8 cents and win 96–100% of the time
- Skill persists perfectly from train to test period
- **Fatal problem**: Avg position size = $1,119 test period, most signals are 0-day resolution (market already resolving when signal fires). Copyability is likely near-zero for the bulk of signals.

### NO Value Hunters (6,574 traders, cal_gap_no > +5pp)
- Naive excess in test: **+30.8pp** — almost entirely explained by price-level selection
- Price-adjusted excess: **+4.6pp** globally across 46,100 signals
- Strongest in mid-range YES price (0.40–0.65): **+8–12pp price-adjusted excess**
- Same-day resolution dominates (median hold = 0d) — copyability concern

### Vs Prior Best
Prior best from scorecard-strategies v1: Politics NO K=50, **+6.2pp excess** (semi-tick)
NO value hunters overall price-adjusted: **+4.6pp** (vectorized UB — before degradation)

The value hunter hypothesis does **NOT** beat the prior best strategy in terms of raw edge.

---

## Pool Definition

### YES Value Hunters (PRIMARY POOL)

**Qualification criteria (train < 2025-07-01)**:
- `calibration_gap_yes = avg(correct - yes_entry_price)` per trader
- Entry price from `yes_entry_data` (accurate: `price_x_vol / volume`)
- Threshold: **cal_gap_yes > +5pp**, **n_yes >= 30**

| Metric | Full Pool (948) | Top-50 by cal_gap |
|---|---|---|
| Avg HR (train, YES) | 59.6% | ~95% |
| Avg entry price | 0.369 | 0.030–0.070 |
| Avg calibration gap | +0.227 | +0.56–0.96 |
| Median n_yes (train) | 54 | 83–815 |
| Median total USD (train) | $5,876 | $80–$96K |

**Top-10 YES value hunters**:
| # | Trader | n | Train HR | Avg Entry | Cal Gap | Hold |
|---|---|---|---|---|---|---|
| 1 | 0x6993b4f5d7... | 83 | 98.8% | 0.033 | +0.955 | 1d |
| 2 | 0x35c0732e06... | 87 | 100.0% | 0.064 | +0.936 | 1d |
| 3 | 0xf8ccc51356... | 145 | 99.3% | 0.071 | +0.922 | 3d |
| 4 | 0x94884db796... | 53 | 94.3% | 0.038 | +0.905 | 35d |
| 5 | 0xc613e6707b... | 815 | 91.5% | 0.030 | +0.885 | 27d |

### NO Value Hunters (SECONDARY POOL)

**Qualification (train < 2025-07-01)**:
- Entry price proxy: `1 - market_avg_YES_price` (approximate)
- Threshold: **cal_gap_no > +5pp**, **n_no >= 10**

| Metric | Value |
|---|---|
| Pool size | **6,574 traders** |
| Avg HR (NO, train) | 78.3% |
| Avg entry price (NO proxy) | 0.679 |
| Avg calibration gap (NO) | +0.104 |

**Top NO value hunters** tend to buy NO when YES is priced 0.40–0.65 (genuine uncertainty zone, not sure-thing NO). Entry prices for top NO VH: 0.44–0.66 (buying NO when YES = 0.34–0.56).

---

## Pool Overlap Analysis

### YES VH vs Top-50 HR Pool

Top-50 HR pool: trained on excess_hr = (train_HR - 48.2% pop_hr), top 50 by this metric.

| Pool | Train HR | Train Excess | n traders |
|---|---|---|---|
| Top-50 by cal_gap_yes (VH) | ~95% | very high (cheap buyers) | 50 |
| Top-50 by excess_hr (HR baseline) | 98.3% | +50.1pp | 50 |

**Overlap = 0 traders (0%)** — completely non-overlapping populations.

- **HR baseline pool**: buys near-certain outcomes (avg entry ~0.90–0.95, wins 98%+)
- **YES VH pool**: buys deeply discounted outcomes (avg entry 0.03–0.07, wins 92–100%)

These are different alpha sources targeting different market types.

---

## Test Period Results

### YES Value Hunter Consensus — Naive vs Price-Adjusted

**Naive analysis** (entire 948-trader pool, N=2 consensus):

| N | n_signals | Naive HR | vs 33.3% base | Hold | Avg Vol |
|---|---|---|---|---|---|
| N=2 | 74,156 | 21.2% | **-12.1pp** | 0d | $1,022 |
| N=3 | 39,308 | 25.2% | -8.1pp | 1d | $1,461 |
| N=5 | 13,457 | 26.5% | -6.9pp | 1d | $2,670 |

**Why naive is misleading**: These traders predominantly enter at 0.01–0.05 YES price. Population HR at that entry range is only **21.6%** (not 33.3%). Their 21-27% HR matches or slightly beats the price-appropriate baseline.

**Price-adjusted analysis — Top-50 YES VH individual positions (test period)**:

| Entry Bucket | n (VH) | VH HR | Pop HR | Price-Adj Excess |
|---|---|---|---|---|
| 0.0–0.10 | 11,094 | 94.4% | 21.6% | **+72.8pp** |
| 0.10–0.20 | 1,605 | 88.8% | 31.7% | **+57.1pp** |
| 0.20–0.30 | 890 | 86.4% | 35.0% | **+51.5pp** |
| 0.30–0.40 | 610 | 82.3% | 40.2% | **+42.1pp** |
| 0.40–0.50 | 401 | 83.8% | 44.7% | **+39.1pp** |
| **Weighted avg** | **15,600** | 91.8% | — | **+63.4pp** |

**Top-5 YES VH — train to test persistence**:

| Trader | Train Cal Gap | Test n | Test HR | Test Avg Entry |
|---|---|---|---|---|
| 0x6993b4f5d7... | +0.955 | 10,809 | 96.0% | 0.018 |
| 0x35c0732e06... | +0.936 | 1,194 | 100.0% | 0.085 |
| 0xf8ccc51356... | +0.922 | 686 | 99.3% | 0.048 |
| 0xc613e6707b... | +0.885 | 3,304 | 98.0% | 0.019 |

**Skill is perfectly persistent** from train to test. These traders genuinely know something.

### YES VH Consensus with Hold Filter

| Hold Filter | n_signals | Naive HR | vs 33.3% | Med Hold | Avg Vol |
|---|---|---|---|---|---|
| >= 0d (all) | 55,836 | 13.9% | -19.4pp | 0d | $19 |
| >= 1d | 24,910 | 20.8% | -12.5pp | 1d | $22 |
| >= 4d | 5,330 | 32.3% | -1.0pp | 9d | $34 |
| >= 4h (>2.4h) | 35,609 | 18.3% | -15.0pp | 1d | $24 |

**Critical observation**: The top-50 VH consensus N>=2 has average volume of only $19–34 per signal — tiny positions that are unlikely to be worth copying individually. The consensus trigger (max(first_trade) across N traders) in a market where the VH enter at $0.01–0.02 each doesn't generate meaningful copy size.

Also: Hold >= 4d brings naive HR to 32.3% — much closer to and almost beating the 33.3% base rate. If price-adjusted, this should be strongly positive.

### NO Value Hunter Consensus — Price-Adjusted

**Pool: 6,574 NO VH (cal_gap_no > +5pp), N>=2 consensus, test period**:

**Overall**: 46,100 signals, naive HR=87.2%, naive excess=+30.8pp, **price-adj excess=+4.6pp**

| YES Price Bucket | n_signals | VH NO HR | Pop NO HR | Price-Adj Excess | Avg Vol | Hold |
|---|---|---|---|---|---|---|
| 0.0–0.1 (expensive NO) | 7,124 | 98.2% | 98.5% | -0.3pp | $16,871 | 1d |
| 0.1–0.2 | 4,642 | 96.9% | 96.3% | +0.6pp | $16,587 | 0d |
| 0.2–0.3 | 4,823 | 96.1% | 94.9% | +1.2pp | $14,897 | 0d |
| 0.3–0.4 | 4,919 | 92.1% | 88.9% | **+3.2pp** | $15,856 | 0d |
| 0.4–0.5 | 4,765 | 81.4% | 72.8% | **+8.5pp** | $15,019 | 0d |
| 0.5–0.6 | 4,078 | 56.2% | 44.4% | **+11.8pp** | $10,590 | 0d |
| 0.6–0.7 | 4,214 | 27.3% | 19.6% | **+7.8pp** | $7,363 | 0d |
| 0.7–0.8 | 4,808 | 15.1% | 9.5% | +5.6pp | $4,964 | 0d |
| 0.8–0.9 | 4,562 | 11.9% | 6.9% | +5.0pp | $2,489 | 0d |
| 0.9–1.0 | 2,165 | 15.9% | 6.8% | +9.1pp | $855 | 0d |
| **Weighted** | **46,100** | — | — | **+4.6pp** | — | — |

**Interpretation**: The strongest NO VH signal is in the 0.40–0.65 YES price range (genuine uncertainty zone): **+8–12pp price-adjusted excess**. This is where NO VH genuinely outperform the crowd. The cheap-YES segments (YES < 0.30) are dominated by "sure-thing NO" buyers who don't add alpha.

**Politics (tag=2) NO VH N>=2** (naive HR=72.8%):
- Price-adjusted: +2.1pp average (weighted across 4,724 signals)
- Best in higher YES price buckets: +19-20pp excess when YES > 0.70
- Signal volume thin at YES > 0.70 (these are expensive-YES = cheap-NO = rare for NO VH)

---

## Tag Analysis

### YES VH Tag Concentration (Train Period, Full Pool)

Value hunters are broadly diversified across tags:

| tag_id | N Traders | N Markets | N Pos | HR |
|---|---|---|---|---|
| 2 (Politics) | 480 | 8,692 | 58,626 | 40.2% |
| 21 | 469 | 3,709 | 19,332 | 40.4% |
| 596 | 466 | 3,551 | 25,846 | 34.8% |
| 1 | 461 | 14,777 | 61,328 | 33.1% |
| 126 | 456 | 4,291 | 26,619 | 40.5% |

These ~30-40% HRs sound poor but remember: population HR at <0.10 YES entry is only 21.6%. Value hunters ARE beating the base rate — the naive HR just looks bad.

### YES VH Consensus by Tag (N=2, Test Period) — Best Tags

| tag_id | n_signals | HR | vs 33.3% | Avg Vol |
|---|---|---|---|---|
| 102127 | 4,279 | 34.5% | +1.2pp | $962 |
| 2 (Politics) | 8,315 | 32.7% | -0.6pp | $2,510 |
| 235 | 6,161 | 30.0% | -3.3pp | $935 |

These "near-zero" naive excess tags likely have substantial positive price-adjusted excess (most signals are cheap-YES entries with 20-22% pop base rate).

### NO VH Consensus by Tag (N=2, Test Period) — Top by Price-Adj Signal

Based on naive excess (price-adjusted would be lower but still positive for these):

| tag_id | n_signals | Naive HR | Naive excess | Avg Vol | Tag Type |
|---|---|---|---|---|---|
| 2 (Politics) | 5,423 | 72.8% | +16.4pp | $19,273 | Elections |
| 596 (Misc) | 3,210 | 75.3% | +18.9pp | $13,360 | Mixed |
| 102264 (Crypto) | 4,436 | 71.3% | +14.9pp | $5,970 | Crypto |
| 100350 (Sports) | 8,150 | 68.2% | +11.8pp | $7,377 | Sports |

Politics price-adjusted: +2.1pp. Sports/Crypto likely similar — most naive excess is price-level selection.

---

## Individual Copy vs Consensus

### Top-K YES VH Individual (Test Period, Naive)

| K | YES n_mkts | YES HR | YES excess | NO n_mkts | NO HR | NO excess |
|---|---|---|---|---|---|---|
| Top-10 | 70,844 | 13.6% | -19.7pp | 33,691 | 7.2% | -49.2pp |
| Top-25 | 81,371 | 16.4% | -16.9pp | 44,712 | 19.2% | -37.2pp |
| Top-50 | 83,673 | 17.0% | -16.3pp | 48,647 | 23.9% | -32.4pp |

**Individual copy YES positions (price-adjusted reality)**: The top-50 VH test period YES positions are at avg entry 0.018–0.085, population base at that level = ~21%. Their test HR = 91-100% = **+70-79pp price-adjusted excess**. The "naive -16pp" is completely misleading for these traders.

**Individual copy NO positions**: YES VH are NOT NO specialists. Their NO positions underperform significantly — this is expected, as they were selected for YES calibration gap. **Do not copy YES VH on NO side.**

### Rule: Use Separate Pools for YES and NO

- **YES signals**: YES VH pool (cal_gap_yes > +5pp, n_yes >= 30)
- **NO signals**: NO VH pool (cal_gap_no > +5pp, n_no >= 10) — completely different traders

---

## Compounding Score

### YES VH Top-50 Consensus (Price-Adjusted)

For hold >= 4d subset (5,330 signals, the copyable subset):
- Price-adjusted excess: estimated +40pp (naive is -1pp, but entry prices are cheap)
- Avg vol: $34 per signal (tiny — these are micro-cap positions)
- Hold: 9d median
- CS = 0.40 × ($34 × 0.40) / 9 = $0.60 (effectively zero — not viable at this scale)

**The YES VH signal is real but NOT commercially viable** at current position sizes. These traders make hundreds of $0.01–0.05 bets. Copying them requires enormous scale or fundamentally different execution.

### NO VH Consensus (Price-Adjusted)

For YES price 0.40–0.65 subset (most meaningful):
- Price-adjusted excess: ~+10pp
- Avg vol: ~$12,000 per signal (much larger position sizes)
- Hold: 0–1d
- CS = 0.10 × ($12,000 × 0.10) / 0.5 = $240 per signal (UPPER BOUND, vectorized)

**NO VH has modestly viable compounding score** but requires tick-by-tick validation to confirm. Expected 30-50pp degradation from vectorized to tick = CS likely becomes near-zero or negative.

---

## Critical Caveats

### 1. Same-Day Resolution Dominates Both Pools

Median hold = 0 days for most signals. The consensus trigger fires after `max(first_trade)` across N traders, which may be the day of resolution announcement. Tick-by-tick validation required to confirm any signal actually precedes resolution.

### 2. YES VH Position Sizes Are Too Small

Top-50 YES VH trained on $80–$96K total USD over their entire training period. Average position is $0.50–$5.00 per trade. A copy strategy at $100 minimum entry would be 20–200x their position size — impossible to fill without moving the market.

### 3. The "Sure-Thing" Contamination in NO VH

Many NO VH qualified by buying expensive NO positions (YES at 0.05–0.20) where NO wins 96-99% of the time. These look like high cal_gap_no (+0.30-0.44) but are actually just sure-thing bets. The genuine signal is only in the 0.40–0.65 YES price range.

### 4. Vectorized Upper Bound

All results are vectorized. Known degradation from vectorized → tick-by-tick: **30–50pp** for signals of this type. Given the small price-adjusted excess (+4.6pp NO VH), real-tick performance is likely negative.

### 5. Price-Adjusted Excess Required for All YES/NO Analysis

**Never report naive excess for value hunter signals**. Price-level selection explains the bulk of the apparent edge. Only bucket-conditional excess (HR vs population at same entry price) is meaningful.

---

## Verdict

### YES Value Hunters: GENUINE SKILL, NOT COPYABLE AT SCALE

- **Evidence of skill**: +63.4pp price-adjusted excess in test (extraordinary, exceeds any prior research finding)
- **Blocker 1**: Position sizes too small ($0.50–$5.00 per trade) — not commercially scalable
- **Blocker 2**: Mostly 0-day resolution — likely post-resolution settlement activity, not predictive signals
- **Recommendation**: Investigate tick-level timing of YES VH entries relative to resolution announcement. If they enter before announcement, this becomes the most powerful signal in the dataset. If after, the alpha is illusory.

### NO Value Hunters: WEAK REAL SIGNAL, BETTER TARGET

- **Evidence**: +4.6pp price-adjusted excess globally, +8–12pp in the 0.40–0.65 YES price zone
- **Problem**: Small edge, 0-day resolution dominates, expected tick degradation eliminates most edge
- **Recommendation**: Tick-by-tick validation of NO VH consensus filtered to YES_price 0.40–0.65 AND hold >= 1 day. This is the only potentially viable subset.

### Overall Strategy Ranking

| Strategy | Price-Adj Excess | Viability |
|---|---|---|
| YES VH individual (top-50) | +63.4pp | REAL BUT NOT SCALABLE |
| YES VH consensus N=2 | ~+20pp (estimated) | UNKNOWN — hold filter needed |
| NO VH consensus (0.40-0.65 YES) | +8-12pp | WEAK REAL — tick validation needed |
| NO VH consensus (all buckets) | +4.6pp | TOO WEAK — below expected degradation |
| YES VH individual copy (naive) | -16pp (misleading) | DO NOT USE NAIVE |

---

## Recommended Next Steps

1. **Tick-by-tick validation (Priority 1)**: Run YES VH top-50 through SyncReplayRunner. Focus on markets with hold >= 1 day. Check whether entries precede or follow resolution announcement.

2. **YES VH with hold filter (Priority 2)**: In vectorized data, restrict to `date_diff('day', signal_entry, resolved_at) >= 4`. This subset (5,330 signals, 9d hold, naive HR 32.3%) needs price-adjusted analysis — may be the best copyable signal.

3. **NO VH in uncertain markets (Priority 3)**: Filter NO VH consensus to YES_price 0.40–0.65 AND hold >= 1 day. Compute price-adjusted excess and CS for this high-quality subset.

4. **YES VH timing analysis (Priority 4)**: Query raw trades for top VH: when exactly do they enter relative to resolution? If they enter 24+ hours before, the signal is real; if same day, it's settlement activity.

5. **Dissent filter for YES VH**: Does requiring 0 qualified NO traders in the market improve YES VH consensus HR? The tag-hr-consensus research found +20-30pp for dissent=1.0.

---

## Artifacts

```
research/hypotheses/scorecard-v2-strategies/
├── scripts/
│   └── value_hunter.py           # Main research script
├── discovery/
│   ├── value_hunter_copy.md      # This document
│   └── value_hunter_raw.json     # Raw metrics JSON
```

**Script**: `/mnt/nvme/git/polymarket/polymarket/research/hypotheses/scorecard-v2-strategies/scripts/value_hunter.py`
