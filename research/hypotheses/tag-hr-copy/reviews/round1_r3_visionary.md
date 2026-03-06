# Visionary Review: tag-hr-copy (Round 1, post-R3 corrections)

**Reviewer**: Visionary
**Date**: 2026-03-05
**Based on**: R3 corrected results (first_trade >= test_start fix applied)

---

## Summary of R3 State

Three viable tags after R3 corrections (all UPPER BOUNDS):

| Tag | HR | Excess HR | Med PnL | Hold | CS |
|-----|-----|-----------|---------|------|-----|
| Esports BUY | 67.2% | +35.7pp | $8.13 | 2.0h | 34.9 |
| 1H BUY | 78.0% | +27.3pp | $4.01 | 1.33h | 19.7 |
| Tennis BUY | 72.4% | +33.6pp | $2.40 | 2.0h | 9.7 |
| Tennis DIR | 66.4% | +30.5pp | $2.43 | 2.6h | 6.8 |

Key structural finding: Esports base rate (34.3%) and Tennis base rate (22.4%) are far below global 38.1%, which means excess HR computations are correct relative to tag-aware base rate (`tag_base_rates.md`). The 1H tag base rate (49.7%) is the closest to balanced.

---

## Adjacent Signals

### 1. Trader Recency Weighting (Esports)
The R3 fix revealed that phantom early-mover entries in Esports were HIGHER quality than test-period entries (HR dropped 12.4pp post-fix). This suggests trader skill in Esports is NOT static — early-in-market traders have genuine informational edge that decays as time passes. A recency-weighted consensus (`exp(-lambda * days_since_first_trade)`) would amplify recent entrants' signals while discounting stale positions. The existing `esports-weighted-copy` spawned idea covers 30d trailing HR weighting but misses the within-market decay dimension. Both dimensions should be tested jointly.

### 2. Consensus Confidence Score (Cross-tag)
Currently the qualification threshold is binary: a trader either passes `min_trades + excess_hr_pp` or not. But the data shows Esports has `mt=50` optimal while Tennis uses `mt=20`. Rather than a hard cutoff, a confidence score per market (`sum(qualified_trader_excess_hr_pp) / sqrt(n_qualified)`) would rank signals by consensus strength and enable position sizing by confidence tier. This is especially valuable for Tennis where `mt=20` admits noisier traders.

### 3. Esports Sub-Tag Decomposition
Esports is a broad category covering CS2, LoL, Valorant, Dota2, etc. Each game has its own market structure, resolution timing, and liquidity profile. The base rate variance within Esports could be significant (some games are closer to coin flips than others). A sub-tag sweep (game-level instead of Esports-level) might reveal one game dominating the signal — and allow tighter qualification thresholds. See `data/tag_base_rates.md` for how tag-specific base rates affect excess HR computation.

### 4. Post-Set Esports Markets
Tennis in Polymarket is often structured as "win-the-set" or "win-the-match" markets. The same structure likely applies to Esports — "win map 1", "win map 2", "win the series". Early maps resolve quickly (same hold time profile as 1H) while series markets take longer. Filtering to in-series map markets only could reduce hold time from 2.0h to ~0.5h for Esports, tripling the compounding score by recycling capital faster.

---

## Parameter Variations

### 1. Entry Window Constraint (Esports)
The `esports-entry-timing` spawned idea targets copying within 15 min of consensus formation. But there is a complementary dimension: entry price at copy time. When the Nth qualified trader acts (consensus trigger), the current price may already have moved. Add `max_copy_lag_minutes` parameter sweeping [5, 10, 15, 30, 60] against price staleness to find the entry window that preserves edge without too many missed signals. This is a strict improvement over BUY-only with no timing filter.

### 2. Minimum Unique Market Count per Trader
Currently `min_trades >= 50` for Esports. But a trader who made 50 trades across 2 markets (25 trades each) looks the same as a trader who made 50 trades across 25 markets. The former is a high-conviction specialist; the latter is more diversified and provides a stronger signal (less likely to be a coordinated position). Add `min_markets >= N` to the qualification criteria as an orthogonal filter.

### 3. Tennis at mt=30-40
Tennis optimal at mt=20 while Esports is at mt=50. This is a 2.5x gap in qualification stringency. The period_base_rate_variance knowledge (`data/period_base_rate_variance.md`) shows monthly YES rates vary 20-45%. A Tennis sweep at mt=[20, 25, 30, 35, 40] would reveal how stable the 72.4% HR is at stricter thresholds. If HR holds at mt=30+, the signal becomes more defensible for deployment.

### 4. Price Ceiling Below 0.75 for Esports
Esports pc=0.75 is the sweet spot per R3 results. The jump from pc=0.8 to pc=0.75 (+0.96pp HR, -26 sigs) is meaningful but what about pc=0.70 and pc=0.65? The base rate for Esports is 34.3% YES — contracts below 0.75 that win generate asymmetric payoffs (buy at 0.60, win $1.00 = +67% return). A tighter pc sweep [0.60, 0.65, 0.70, 0.75] might find a smaller, higher-quality signal subset worth running independently alongside the main strategy.

### 5. Ensemble Threshold (Portfolio-level)
The three-tag portfolio currently treats each tag independently. A combined signal (require at least 2 of 3 tags to have qualified consensus on the same outcome/condition_id, if markets overlap) could produce a much higher-confidence subset. True cross-tag overlap may be rare but worth quantifying.

---

## Cross-Hypothesis Connections

### Tennis DIR vs BUY-only Gap Narrows After R3
In R2, Tennis DIR CS=4.6 vs BUY CS=10.9 — a 2.4x gap. In R3, Tennis DIR CS=6.8 vs BUY CS=9.7 — only 1.4x gap. The directional filter was hurting Tennis BUY (phantom pre-test NO traders diluting BUY quality) while the R3 fix reversed this. This is the opposite of the expected pattern from `pitfalls/sell_is_exit.md` (SELL is exit, BUY dominates). For Tennis specifically, the YES/NO directional structure appears more symmetric than other tags. The `tennis-directional` spawned hypothesis should be treated as HIGH priority — it is not a minor variation but a substantially different signal.

### 1H as Portfolio Anchor
The 1H tag has three structural advantages that make it the best portfolio anchor:
1. Lowest vectorized-to-tick degradation risk (minimal phantom contamination, -1.8pp HR)
2. Fastest capital recycling (1.33h hold, shortest of the three)
3. Most stable to parameter perturbation (from R2 sensitivity)

The 1H compounding score of 19.7 is the most reliable number in R3 because the signal is least affected by the `first_trade >= test_start` fix. Esports (CS=34.9) carries more uncertainty about how much further degradation will occur in tick-by-tick replay — specifically because the early-mover informational edge (which was the strongest part of the signal) is now excluded.

### Esports + Tennis Portfolio Complementarity
Esports base rate: 34.3% YES. Tennis base rate: 22.4% YES. Both are NO-biased categories. Yet both strategies run BUY-only on YES. This means both strategies are bucking the NO bias and picking genuine YES outliers. The intersection of these two tag signals on the same market (if any market carries both Esports and Tennis tags, which is unlikely but possible for exhibition matches) would be extremely high confidence.

More importantly, the two strategies are structurally uncorrelated: they track different trader pools, different market types, and resolve on different event schedules. A combined Esports+Tennis+1H portfolio should have lower drawdown correlation than any single-tag strategy.

### Split Position Blind Spot in Trader Qualification
`pitfalls/split_position_blind_spot.md` warns that 12% of maker (trader, asset_id) pairs have negative net_tokens due to CTF splits — and that `maker_positions_resolved_corrected` is the correct table to use. The R3 sweep already uses this table (per notes.md). However, the qualification step (`min_trades >= 50`) counts `trade_count` which is correct (counts OrderFilled events). But `realized_pnl` for split-route traders is still partially corrected (minimum splits inferred, not exact). Any trader qualification based on PnL should be noted as having ~12% noise on the high-volume tail.

### Period Base Rate Variance Risk
`data/period_base_rate_variance.md` shows July 2025 had only 20.4% YES wins (vs 35-37% normally). The R3 fold_detail shows Esports base_rate=0.1333 in 2025-01 and 0.1233 in 2025-04 — extremely low YES rates. If the strategy was copying YES trades in those periods against 12-13% base rates, the excess HR (67.2% - 12-13% = 54-55pp) is massive but the periods also have very small sample sizes (n=225 and n=227). The bulk of signal volume comes from 2025-10 (n=1444) and 2026-01 (n=2894) where base rates are more normal (34-44%). Validate that the aggregate HR is not dominated by the high-n recent folds where base rates happen to align better.

---

## Compounding Improvements

### 1. Early Exit Signal (Esports)
Esports holds average 2.0h. But if the market price moves significantly after entry (e.g., reaches 0.90+ within 30 min), most of the potential PnL is already captured. An early exit trigger at `current_price >= entry_price + 0.15` would recycle capital faster. This converts some winning positions from 2.0h hold to 0.5h hold — tripling throughput on those markets. Risk: missing the full payoff at resolution. Mitigant: sell 70%, hold 30% to resolution.

### 2. 1H Capacity Test is Underpriced as Priority
The `1h-capacity-test` was spawned at MEDIUM priority. But 1H has 5009 signals/fold and the fastest capital recycling (1.33h hold). Even with 50% degradation in tick-by-tick, that is 2500 real signals/quarter. At $50/signal, that is $125,000/quarter in deployed capital just for 1H. The capacity test should understand market depth at this scale — not because capacity is expected to be a problem now, but because it sets the ceiling on 1H strategy value. Upgrade to HIGH.

### 3. Position Sizing by Remaining Hold Time
`execution/hold_time_capital.md` suggests sizing inversely proportional to expected hold time. Within the Esports pool, some markets are near resolution when the signal triggers (30 min remaining), while others have 4+ hours. A smaller position in near-resolution markets (less time to recover from bad luck) and larger in mid-timeline entries would improve capital efficiency without changing signal quality.

---

## New Hypothesis Ideas

For `research/ideas.md` backlog:

1. **esports-sub-tag-sweep**: Decompose Esports tag into game-level sub-categories (CS2, LoL, Valorant, Dota2) and run tag-hr-copy sweep per game. Hypothesis: one or two games drive most of the 67.2% HR signal while others dilute it. Priority: high.

2. **tennis-directional**: Already spawned from researcher. Confirmed here as HIGH priority — Tennis DIR CS=6.84 after R3 fix is qualitatively stronger than any directional signal seen in previous research. The YES/NO symmetry in Tennis suggests both sides have real informational content, unlike Esports where BUY dominates 78x over DIR. Priority: high.

3. **esports-map-markets**: Isolate Esports "win this map" markets from "win the series" markets. Map markets resolve within 1-2h vs 4-6h for series. Shorter hold time with similar HR would increase Esports CS from 34.9 to potentially 100+. Priority: high.

4. **consensus-confidence-rank**: Instead of binary qualified/not qualified, score each consensus market by `sum(trader_excess_hr_pp) / sqrt(n_qualified)`. Top quartile of confidence scores should have significantly higher HR. Test across all three tags. Priority: medium.

5. **esports-entry-window**: Already spawned. Add `max_copy_lag_minutes` parameter sweep [5, 15, 30, 60] to esports-entry-timing — find entry window that preserves HR without sacrificing signal count. Priority: high.

---

## Summary

The R3 corrections materially improved our understanding: the 1H signal is the most reliable (minimal contamination, stable), Tennis is a surprise upgrade (72.4% HR on test-period entries is stronger than expected), and Esports is still strong but now correctly bounded to copyable entries only.

The most promising next direction is **not** adding more tags but deepening the three-tag portfolio in two ways. First, decompose Esports into game-level sub-tags — the CS from a pure CS2 or LoL signal may significantly exceed the aggregate Esports CS=34.9 because different games have different insider structures. Second, validate Tennis separately as a high-priority directional hypothesis (`tennis-directional`) — its R3 behavior (HR going UP after phantom removal) is structurally opposite to Esports and deserves its own validation track, not to be bundled with the tag-hr-copy validation.

The three-tag portfolio (Esports + 1H + Tennis) has genuine complementarity: different event schedules, different trader pools, different hold times, and different informational structures. As a combined portfolio it should have better Sharpe than any single-tag strategy. The blind spot to watch in validation is whether the Esports CS=34.9 survives 20-40pp vectorized-to-tick degradation — it started at 73.7 in R2 and halved to 34.9 in R3. A second halving to CS~17 in tick-by-tick would put it below 1H and make the Esports risk/reward (more volatile, smaller sample in early folds) less compelling relative to the more stable 1H signal.
