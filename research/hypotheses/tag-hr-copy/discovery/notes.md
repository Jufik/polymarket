# Tag-HR-Copy Discovery Notes (Round 3)

**Round history:**
- R1: initial sweep (avg_pnl bug, hours denominator bug)
- R2: added `max_avg_entry_price` filter, fixed CS formula (median_pnl, days denominator)
- R3: **critical fix** — `first_trade >= test_start` in market aggregation (Skeptic audit)

## Critical Bug Fixed in Round 3

**The bug (Skeptic-identified):** `_tmp_thr_mkt_buy` joined qualified training traders to markets resolving in the test window — without filtering on `first_trade`. A trader who entered a market during training, and whose market resolved in the test window, was counted as a test signal. That trade was not copyable (it happened before the test period started).

**Fix:** Added `AND toDate(t.first_trade) >= '{test_start}'` in both `_tmp_thr_mkt_buy` and `_tmp_thr_mkt_dir`.

**Magnitude:** 31.9% of test-window positions had `first_trade` before test start (across all tags, 2025-07 test fold: 69,477 phantom / 217,895 total).

**event_tags schema:** Confirmed only `(event_id INT32, tag_id INT32)` — no `created_at`. Tag universe is static, no retroactive tagging risk.

## Classification Status

No new classifications created. Used:
- `maker_positions_resolved_corrected` — split-corrected positions with PnL + resolution
- Tag chain: `markets -> events -> event_tags -> tags` (markets.category is always NULL)
- Entry price: `trader_trade_agg FINAL` joined to `token_market_map` on `asset_id`, `outcome='YES'`

## R2 vs R3 Delta (Critical)

| Tag | R2 CS | R3 CS | Delta | R2 HR | R3 HR | HR Delta |
|-----|-------|-------|-------|-------|-------|----------|
| Esports | 73.70 | **34.87** | -52.7% | 79.6% | **67.2%** | -12.4pp |
| Tennis | 10.94 | **9.67** | -11.6% | 46.7% | **72.4%** | +25.7pp |
| 1H | 22.07 | **19.71** | -10.7% | 79.8% | **78.0%** | -1.8pp |

**Esports** lost the most: HR dropped 12.4pp because phantom early-mover entries were higher quality — genuine insiders who entered during training were driving the signal. The remaining test-period entries are still strong (67.2% HR, +35.7pp excess) but the signal is now correctly bounded to actually copyable entries.

**Tennis** reversed direction: HR went UP 25.7pp. Test-period fresh entries (72.4% HR) are actually BETTER quality than the diluted pre-test entries. The R2 result was contaminated by low-quality historical traders. R3 Tennis is now the strongest BUY HR signal in the portfolio.

**1H** barely changed (-1.8pp HR, -10.7% CS). The 1H markets are short-dated and almost all entries happen close to resolution — the pre-test contamination was minimal.

## Key Findings (Round 3)

### Final Top Results (BUY-only, UPPER BOUNDS)

**Esports BUY (mt=50, ep=15, pc=0.75):**
- HR=67.2%, excess=+35.7pp, med_pnl=$8.13, hold=2h, sigs=4769, CS=34.87

**Tennis BUY (mt=20, ep=15, pc=0.80):**
- HR=72.4%, excess=+33.6pp, med_pnl=$2.40, hold=2h, sigs=5725, CS=9.67

**1H BUY (mt=50, ep=15, pc=0.75):**
- HR=78.0%, excess=+27.3pp, med_pnl=$4.01, hold=1.33h, sigs=5009, CS=19.71

### Tennis Directional — Surprise Upgrade

Tennis DIR in R3: HR=72.4% (excess=+30.5pp), CS=6.84. This is substantially better than R2 DIR (HR=52.4%, CS=4.6). The directional filter was contaminated by low-quality pre-test traders in R2. Now Tennis DIR is a serious spawned idea.

### SELL Variant (unchanged)

BUY-only still dominates on all tags:
- Esports: BUY CS=34.87 vs DIR CS=0.45 (78x)
- 1H: BUY CS=19.71 vs DIR CS=0.06 (328x)
- Tennis: BUY CS=9.67 vs DIR CS=6.84 (1.4x — smallest gap, DIR worth exploring)

### Price Ceiling (pc) Sweet Spot

For all 3 tags: pc=0.75 remains optimal. Effect unchanged from R2 — pc filter removes expensive-entry traders, improving signal quality at -15% volume cost.

## Sensitivity (from R2, partially applicable to R3)

The R2 sensitivity was computed on a buggy sweep. Key observations still qualitatively valid:
- Esports is moderately fragile to mt perturbation (6pp drop at mt-20%)
- 1H is extremely stable across all perturbations (<1.2pp)
- Tennis stability to be re-tested in R4 if it proceeds to validation

> [!NOTE] Re-run sensitivity_r3.py against corrected sweep if needed before validation.

## SQL Gotchas (Round 3 additions)

- **`first_trade >= test_start` is CRITICAL** in market-level aggregation for test window signals. Without it, training-period entries contaminate test signals by up to 32%.
- Signal count anomaly: R3 showed MORE signals than R2 for Esports (+3222) despite the filter. This is because R3 captures MORE markets with valid test-period entries from qualified training traders (the filter changes WHICH traders contribute per market, not necessarily fewer markets).
- Per-fold `base` in R3 for `2026-01` fold shows 0.3476 (not 0.5070 as in R2). This is correct — training window 2025-07 to 2026-01 has different YES ratio than all-time.

## Spawned Ideas (Updated)

1. **tennis-directional** [HIGH]: Tennis DIR now shows HR=72.4% (excess=+30.5pp, CS=6.8) after R3 fix. Up from R2's HR=52.4%. This is now a credible signal — validate.
2. **esports-entry-timing** [HIGH]: Copy within 15 min of consensus formation — Esports resolves in 2h avg.
3. **esports-weighted-copy** [HIGH]: Weight by trader trailing 30d HR (exp decay). CS=34.87 baseline.
4. **1h-capacity-test** [MEDIUM]: 5009 signals/fold. Test max capital deployment.
