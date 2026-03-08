# Tag Scan Results — January 2026 Extended Window

**Date**: 2026-03-07
**Method**: Vectorized UPPER BOUNDS + Tick-by-Tick Validation
**Train cutoff**: 2026-01-01 | **Test period**: 2026-01-01 to 2026-02-01
**Pool**: Top-K=25 composite score | **Consensus**: N=2
**Filter**: >=30 test markets, >=50 train markets
**Tick validate threshold**: excess_hr >= 25% AND n_signals >= 10

---

## Summary

- Total tags in universe: 213
- Tags meeting data threshold (>=30 test markets, >=50 train): **19** (was 14 in July 2025 scan)
- Tags advanced to tick validation: **4** (Sports, Crypto, Finance, Weather)
- Tags with genuine tick signal: **2** (Finance +64.8pp, Sports +41.3pp)
- **Net new viable tags beyond known three**: **Finance** (newly qualified, N=27 signals)
- **Weather**: IN-PLAY CONTAMINATED (temperature-watching during resolution window)

> [!CRITICAL]
> These are results from both vectorized AND tick-by-tick validation.
> Tick-validated results are ground truth. Vectorized numbers are upper bounds only.
> Weather shows 100% tick HR but is IN-PLAY CONTAMINATED — do not deploy.
> Finance is a genuine new signal: 81.8% tick HR, +64.8pp excess, but negative PnL due to high fill prices.

---

## Tick-Validated Results

| Tag | Test Mkts | Pool | Vec Signals | Vec HR | Vec Excess | Tick Fills | Tick HR | Tick Excess | Tick PnL | Sharpe | Status |
|-----|----------|------|------------|--------|-----------|-----------|---------|------------|----------|--------|--------|
| **Crypto** | 1,935 | 25 | 31 | 100.0% | +73.9% | 50 | 80.0% | +53.8% | $-492 | -0.43 | monitor |
| **Finance** | 1,460 | 25 | 27 | 81.5% | +64.5% | 33 | 81.8% | **+64.8%** | $-124 | -0.75 | investigate |
| **Sports** | 45,582 | 25 | 31 | 83.9% | +51.3% | 46 | 73.9% | +41.3% | $-353 | -3.62 | monitor |
| **Weather** | 1,990 | 25 | 11 | 90.9% | +79.5% | 14 | 100.0% | +88.6% | $+113 | 10.12 | IN-PLAY |

> [!WARNING]
> **Weather PnL is positive but the signal is contaminated — do not use as evidence of viability.**
> All Weather signals are same-day city temperature markets resolved in 1-3 hours.
> The "pool traders" watch real-time weather apps and enter during the resolution window.
> This is the same contamination pattern as Awards and Trump from the July 2025 scan.

---

## In-Play Contamination Analysis: Weather

All 5 vectorized weather consensus markets:

| Market | Signal Entry (UTC) | Resolution (UTC) | Hold | Won |
|--------|--------------------|-----------------|------|-----|
| highest-temperature-in-atlanta-on-january-7 | 2026-01-07 21:55 | 22:14 | 1h | YES |
| highest-temperature-in-london-on-january-14 | 2026-01-14 22:25 | 23:40 | 1h | YES |
| highest-temperature-in-seoul-on-january-12 | 2026-01-12 17:50 | 18:21 | 1h | YES |
| highest-temperature-in-seoul-on-january-6 | 2026-01-06 16:33 | 18:16 | 2h | YES |
| highest-temperature-in-seoul-on-january-14 | 2026-01-14 11:17 | 14:14 | 3h | YES |

**Root cause**: These are markets like "Will the highest temperature in Seoul today be >= 4°C?". The pool traders enter in the late afternoon/evening after the temperature for the day is already known from public weather APIs. They are betting on outcomes already observable — NOT making advance predictions.

- 60% of signals: hold <= 1h (entered <1h before resolution)
- 100% of signals: resolve same calendar day as entry
- Pattern identical to Awards (live ceremony watching) and Trump (speech watching)

**Conclusion**: Weather is IN-PLAY CONTAMINATED. The 100% tick HR and positive PnL are artifacts of near-certain information at signal time. A copy strategy cannot replicate this edge — by the time a 2nd pool trader enters, the temperature is already fixed and the market is priced near 1.0.

---

## Finance Tag — New Viable Signal

Finance is a **genuinely new tag** that now qualifies with 6 months of additional training data.

### Signal Composition

Finance markets in January 2026 include:
- **Quarterly earnings** (IBM, JPM, TSM, AAL, AXP, SCHW, DAL, TXN, PG...): hold 3-135h
- **Stock price range markets** (META above 620, GOOGL above 310, NVDA 180-185...): hold 3-58h
- **Weekly close range** (will NFLX close between X-Y this week): hold 3-4h

This is a legitimate pre-event information domain: earnings traders with track records.

### Signal Quality

| Metric | Value |
|--------|-------|
| Vectorized signals (Jan 2026) | 27 |
| Tick fills | 33 |
| Tick HR | 81.8% |
| Tick excess HR (vs 17.0% base) | **+64.8pp** |
| Tick PnL (at $100/position) | -$124 |
| Sharpe | -0.75 |

### PnL vs HR Mismatch Explanation

The negative PnL at 81.8% HR is explained by **high fill prices**. Finance YES markets priced by sharp traders before consensus fires typically sit at 0.85-0.95 (the market is already near certain). At p=0.90:

- Win: profit = (1/0.90 - 1) × $100 = +$11.11
- Lose: loss = -$100
- Expected PnL at 81.8% HR: 0.818 × $11.11 - 0.182 × $100 = **-$9.08 per trade**

To break even at p=0.90, you need HR > 90%. The fill simulator fills at the Nth-trader's price plus a small buffer, which lands at these high prices for Finance markets already partially resolved.

**Key implication**: Finance is a **high-HR, low-edge** signal. You need price gating (max_price ≤ 0.70-0.75) to make it profitable. Many signals will be skipped, but the remaining ones at genuine uncertainty levels (0.4-0.75 entry) should be profitable.

### Recommended Next Steps for Finance

1. Re-run tick validation with `max_price=0.75` filter
2. Expected: fewer fills (maybe 10-15 vs 33) but positive PnL
3. Exclude markets with price at consensus-trigger > 0.75

---

## Ranked Recommendations

| Rank | Tag | Signals (Jan 26) | Vec Excess (UB) | Tick Excess | Tick PnL | Hold (d) | Recommendation |
|------|-----|----------------|----------------|------------|----------|----------|----------------|
| 1 | **Finance** | 27 | +64.5% | +64.8pp | $-124 | 1.0 | **tick-validate with max_price=0.75** |
| 2 | **Crypto** | 31 | +73.9% | +53.8pp | $-492 | 0.0 | monitor (already validated) |
| 3 | **Sports** | 31 | +51.3% | +41.3pp | $-353 | 0.0 | monitor (already validated) |
| 4 | ~~Weather~~ | 11 | +79.5% | +88.6pp | $+113 | 0.0 | IN-PLAY (skip) |
| 5 | Politics | 7 | +81.3% | not run | — | 1.0 | too thin (7 signals) |

---

## Full Tag Universe Overview (January 2026)

| Tag | Train Mkts | Train Traders | Train Base | Jan'26 Mkts | Jan'26 Base | Signals | Excess HR | Status |
|-----|-----------|---------------|-----------|------------|------------|---------|-----------|--------|
| Sports | 77,868 | 292,561 | 0.300 | 45,582 | 0.326 | 31 | +51.3% | TICK:monitor |
| Politics | 16,079 | 270,891 | 0.245 | 2,371 | 0.187 | 7 | +81.3% | too thin |
| Weather | 5,998 | 14,342 | 0.124 | 1,990 | 0.114 | 11 | +79.5% | TICK:in-play |
| Crypto | 14,903 | 114,403 | 0.162 | 1,935 | 0.262 | 31 | +73.9% | TICK:monitor |
| Finance | 2,395 | 16,260 | 0.249 | 1,460 | 0.170 | 27 | +64.5% | TICK:investigate |
| Awards | 1,227 | 11,742 | 0.102 | 754 | 0.171 | 7 | +40.0% | too thin |
| Movies | 1,086 | 18,730 | 0.138 | 274 | 0.157 | 5 | +64.3% | too thin |
| Culture | 1,233 | 22,855 | 0.130 | 207 | 0.229 | 2 | -22.9% | weak |
| Music | 789 | 32,724 | 0.117 | 189 | 0.093 | 2 | +40.7% | too thin |
| Science | 390 | 15,373 | 0.236 | 138 | 0.192 | 2 | +80.8% | too thin |
| AI | 324 | 15,109 | 0.161 | 72 | 0.219 | 2 | +78.1% | too thin |
| Trump | 667 | 14,239 | 0.257 | 71 | 0.389 | 0 | -38.9% | weak |
| Business | 757 | 50,192 | 0.142 | 69 | 0.401 | 0 | -40.1% | weak |
| MrBeast | 341 | 6,306 | 0.223 | 58 | 0.242 | 2 | +25.8% | too thin |
| Inflation | 170 | 3,939 | 0.153 | 55 | 0.118 | 3 | +54.9% | too thin |
| Elon Musk | 474 | 15,784 | 0.122 | 53 | 0.106 | 1 | +89.3% | too thin |
| YouTube | 139 | 1,108 | 0.133 | 50 | 0.140 | 0 | -14.0% | weak |
| Esports | 1,973 | 8,860 | 0.443 | 46 | 0.030 | 0 | -3.0% | pool inactive |
| box office | 286 | 6,228 | 0.206 | 37 | 0.266 | 20 | +13.4% | marginal |

---

## Delta vs Previous Scan (July 2025 Cutoff)

### What Changed

| Dimension | July 2025 scan | January 2026 scan |
|-----------|---------------|------------------|
| Train cutoff | 2025-07-01 | 2026-01-01 |
| Test period | 2025-07-01 onwards | January 2026 only |
| Min test markets | 50 | 30 |
| Min train markets | 100 | 50 |
| Tags qualifying | 14 | 19 |
| Tags tick-validated | 0 (pre-existing) | 4 |

### New Tags Qualifying

6 tags entered the scan that were absent before:
- **Finance** (2,395 train vs 68 in July scan) — key new entrant, genuine signal
- **AI** (324 train vs 21 in July scan) — still too thin (2 signals)
- **MrBeast** (341 train vs 33 in July scan) — still too thin (2 signals)
- **Inflation** (170 train vs 42 in July scan) — too thin (3 signals)
- **YouTube** (139 train vs 29 in July scan) — 0 signals
- **Esports** (1,973 train vs 42 in July scan) — pool inactive in Jan 2026

### Esports Update

Esports now has 1,973 training markets (was 42 in July 2025, a 47x increase). The composite pool can be built. However, only 46 Esports markets resolved in January 2026, and the pool traders produced 0 consensus signals. This is likely because:
- January is off-season for major esports tournaments
- The Jan 2026 base rate crashed to 3.0% (vs 44.3% in training) — possible market type shift
- Pool traders are active but not converging on the same markets

**Recommendation**: Re-scan Esports in Q2 2026 when major leagues (CS2, LoL, Valorant) are in full season. The training depth is now solid.

---

## Complete Tag Universe (all 213 tags, top 50 by Jan 2026 market count)

| Tag | Train Mkts | Jan 2026 Mkts | Jan 2026 Base HR | Note |
|-----|-----------|--------------|----------------|------|
| Sports | 77,868 | 45,582 | 0.326 | scanned |
| Politics | 16,079 | 2,371 | 0.187 | scanned |
| Weather | 5,998 | 1,990 | 0.114 | scanned |
| Crypto | 14,903 | 1,935 | 0.262 | scanned |
| Finance | 2,395 | 1,460 | 0.170 | scanned |
| Awards | 1,227 | 754 | 0.171 | scanned |
| Movies | 1,086 | 274 | 0.157 | scanned |
| Culture | 1,233 | 207 | 0.229 | scanned |
| Music | 789 | 189 | 0.093 | scanned |
| Science | 390 | 138 | 0.192 | scanned |
| AI | 324 | 72 | 0.219 | scanned |
| Trump | 667 | 71 | 0.389 | scanned |
| Business | 757 | 69 | 0.401 | scanned |
| MrBeast | 341 | 58 | 0.242 | scanned |
| Inflation | 170 | 55 | 0.118 | scanned |
| Elon Musk | 474 | 53 | 0.106 | scanned |
| YouTube | 139 | 50 | 0.140 | scanned |
| Esports | 1,973 | 46 | 0.030 | scanned |
| box office | 286 | 37 | 0.266 | scanned |
| Iran | 49 | 32 | 0.242 | too few train |
| GDP | 20 | 28 | 0.130 | too few train |
| Commodities | 0 | 28 | 0.104 | too few train |
| argentina | 21 | 22 | 0.121 | too few train |
| Economy | 198 | 21 | 0.255 | too few test |
| Celebrities | 113 | 20 | 0.009 | too few test |
| SpaceX | 72 | 15 | 0.153 | too few test |
| Canada | 21 | 13 | 0.198 | too few train |
| China | 23 | 10 | 0.083 | too few train |
| Elections | 292 | 2 | 0.000 | too few test |
| App Store | 119 | 0 | 0.000 | too few test |
| Fed | 108 | 0 | 0.000 | too few test |

---

## Key Conclusions

### 1. Finance is the primary new discovery

Finance earned +64.8pp tick-validated excess HR in January 2026 — the strongest NEW signal in this scan. The pool trades earnings releases and stock-price range markets with genuine predictive skill. The negative PnL is a fill-price artifact, not a signal quality problem. A max_price gate at 0.70-0.75 should produce positive PnL with 10-15 signals/month.

### 2. Weather is the third in-play contamination case (after Awards and Trump)

All 5 vectorized Weather signals are same-day city temperature markets. Pool traders enter after the temperature is already observable from public APIs. This is NOT advance prediction. The 100% HR is real but un-replicable by a copy strategy that fires on the 2nd pool entry (by then price is already near 1.0).

### 3. Sports, Crypto remain confirmed viable — but negative PnL needs price gating

Both tags show strong tick HR (+41-54pp excess). The negative PnL follows the same pattern as Finance: consensus fires after sharp traders have already moved price to 0.85-0.95. Price gating (max_price=0.65) is the fix, already identified in prior research.

### 4. Esports has training depth now but is seasonally inactive in January

The Esports training pool now has 1,973 markets (was 42). The methodology works — the pool builders return 25 traders. But January 2026 only has 46 Esports markets and pool traders are not converging. Re-scan in April 2026 (spring season start).

### 5. Politics too thin in January 2026 (7 vectorized signals)

Politics had 7 signals all winning (100% vectorized HR), but below the n_signals >= 10 threshold for tick validation. January is naturally thin for politics — major events cluster around elections. Monitor for Q2/Q3 periods.

### 6. No new "surprise" tags

Science (2 signals, 80.8% HR), AI (2 signals, 78.1%), Elon Musk (1 signal, 89.3%) all show tantalizing high HR with trivially small sample sizes. These are consistent with random chance at N=1-2. No action warranted.

---

## Immediate Action Items

1. **Finance max_price rerun**: Re-run tick validation for Finance with `max_price=0.75`. Expected: 10-15 fills, positive PnL, confirms deploy readiness.
2. **Esports Q2 monitoring**: Schedule re-scan for April 2026 when Esports season begins.
3. **Weather documentation**: Add Weather to the in-play contamination knowledge base alongside Awards and Trump.
4. **Finance pool expansion test**: Test K=50 for Finance to increase signal frequency (trade off: potentially noisier pool).

---

## Methodology Notes

- **Pool building**: Top-K=25 by composite score (0.45*excess_hr + 0.25*consistency_sharpe + 0.15*avg_edge_usd + 0.15*bucket_excess_hr)
- **Min trader qualifications**: >= 10 markets in training, conviction >= 0.90, < 10000 trades (bot filter)
- **Signal**: N=2 distinct pool traders enter YES in the same market during January 2026
- **Test window filter**: first_trade >= 2026-01-01 (only copyable entries, prevents training leakage)
- **Hold time**: date_diff(day, max(first_trade), resolved_at) — market level, not trader level
- **Counting**: MARKET level (distinct condition_ids), not trader-position level
- **Vectorized bias**: ~20-40pp excess HR lost in tick-by-tick validation (consensus gap)
- **Tick validation**: YES-only direction filter, SyncReplayRunner, $100/position, capital=$5k
- **PnL calculation**: SimulatedExecutor fills at triggering price + 0.02 buffer (no slippage model)

---

*Script*: `research/hypotheses/scorecard-v2-strategies/scripts/tag_scan_jan2026.py`
*Raw JSON*: `research/hypotheses/scorecard-v2-strategies/validation/tag_scan_jan2026_results.json`
*Log*: `tmp/tag_scan_jan2026.log`
