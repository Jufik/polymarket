# Challenger Review: tag-hr-consensus (Round 1)

**Date**: 2026-03-06
**Reviewer**: Challenger (capital efficiency)
**Source**: Vectorized discovery results, upper bounds only.

---

## Compounding Score Assessment

### Esports (recommended combo: N=5, W=inf, vol>=1k, ep>=10pp)

- Excess HR: +33.0pp (vectorized upper bound)
- Avg edge: $15.48/trade (median PnL)
- Median hold: 0.083 days (2.0h)
- **Compounding score: 61.3**
- Benchmark: Target is 0.5+. This score is 122x the minimum bar.

### Tennis (recommended combo: N=5, W=8h, vol>=2k, ep>=10pp)

- Excess HR: +48.3pp (vectorized upper bound; +54.3pp at vol>=2k)
- Avg edge: $12.45/trade (median PnL)
- Median hold: 0.071 days (1.7h)
- **Compounding score: 86.5** (top-1 combo); ~97 at vol>=2k extrapolation
- Benchmark: Target is 0.5+. This score is 173x the minimum bar.

### Honest post-discount estimates

The vectorized-to-tick discount is 20-40pp HR. Applying that floor:

| Metric | Esports (pessimistic) | Tennis (pessimistic) |
|--------|----------------------|----------------------|
| Excess HR after discount | ~0pp to +13pp | ~8pp to +28pp |
| Edge retained (proportional) | $0-6/trade | $2-7/trade |
| Hold days | 0.083 (unchanged) | 0.071 (unchanged) |
| **Adjusted CS floor** | 0 to 9.4 | 2.0 to 28.0 |

Even at the pessimistic floor, Tennis still clears the 0.5 bar by 4x. Esports is marginal at the floor but recovers fast with any edge above zero — the hold time is so short that any excess HR produces outsized CS. The hold time is the dominant term in this formula, and it is genuinely structural for sports markets.

---

## Hold Time Analysis

- Median: **0.083 days (2.0h)** for Esports, **0.071 days (1.7h)** for Tennis
- P75: ~4h (from notebook distribution analysis)
- 90th percentile: not directly reported, but MAX_HOLD_HOURS cap is 48h — the sweep only includes markets that resolve within 48h. This means the reported distribution excludes drawn-out markets by construction. That is appropriate for this signal class (sports resolve at match end), but the cap should be validated in tick-by-tick to confirm there are no slow-resolving outliers hiding below the filter.
- Distribution shape: sharply concentrated at 1-4h. Esports/Tennis markets resolve at match completion, not on open-ended schedules.
- Capital turns per month (at 2h median hold, 24h/day, 30d/month): **360 turns/month theoretical maximum**. Even with cooldown and position overlap, this is a qualitatively different regime from any existing strategy.

This is not a sports strategy in the ordinary sense. At 2h median hold, it behaves more like a crypto 5/15-min strategy than a conventional sports bet. The capital efficiency case is overwhelming — the question is entirely whether the edge survives tick-by-tick.

---

## Comparison vs Existing Strategies

### s2_insider_copy (sports pool)

From the config and validated results:
- Sports: 74.3% HR, +13.5pp NO excess, $391/pos, hold ~8 days (typical sports resolution)
- Compounding score (sports pool): 13.5pp x $391 / 8 days = **660** — but this is per-position PnL, not median PnL per signal. On a $10 position size the comparable CS would be: 0.135 x 10 / 8 = **0.169**.

Recomputing tag-hr-consensus on comparable $10 position basis is not directly possible from the sweep (the sweep uses realized_pnl from maker_positions, not fixed sizing). The $12-17 median PnL likely reflects variable position sizes from the qualified traders, not a standardized $10 position. This means the raw CS numbers are not directly comparable across strategies without normalizing to a common position size.

**What is directly comparable**: the hold time and throughput. s2_insider_copy sports holds ~8 days. tag-hr-consensus holds ~2h. That is a 96x capital velocity difference. Even if tag-hr-consensus produces only 2pp excess HR post-tick-discount (vs s2's 13.5pp), the velocity advantage closes the gap entirely.

### s3_no_sniper (Economy/Tech)

From the config: 77.0% HR, 5-minute entry window, Economy/Tech tags.
- s3 is event-driven at market creation (5-min window). Hold time unknown from config alone, but Economy/Tech markets are not sports — likely days to weeks.
- tag-hr-consensus is structurally faster. Not directly competing for the same capital pool.

---

## Capital Efficiency Suggestions

### 1. Do not wait for resolution — add a time-based exit

Current design holds to `resolved_at`. For a 2h median, this is fine. But the P75 is 4h and the cap is 48h. Any position that hasn't resolved in 6h is an outlier. Validate whether there is a tail of slow-resolving Esports/Tennis markets (e.g., suspended matches, disputed outcomes) where the market stays open for 24-48h while the position is locked.

Recommendation: set a hard exit at 8h in deployment (4x median). If the market hasn't resolved, close the position. The 6h+ tail likely has lower HR anyway — if consensus traders were correct, the outcome was usually clear within a few hours of match end.

### 2. Use vol>=2k for Tennis, not vol>=1k

The data is explicit: vol>=2k delivers +6pp HR over vol>=1k for Tennis (84.8% to 90.9%). The throughput penalty is real — 27 vs 91 signals/fold — but the CS improvement at higher vol is worth it given Tennis's fragility on the W parameter. Higher-quality signals in a fragile regime is the right tradeoff. At 27 signals/fold (~9/month), position sizing can be larger per signal while staying within capital limits.

### 3. Separate Esports and Tennis into distinct deployed instances

These two signals have materially different parameter regimes. Tennis requires W=8h (critical — dropping to 4h costs 9.1pp HR) and vol>=2k. Esports is robust to window (W=inf is optimal) and works at vol>=1k. Mixing them into one instance means either suboptimal parameters for one or both, or additional complexity in the signal logic. Keep them separate from day one.

### 4. Push N=4 as a fallback tier for Esports throughput

The top robust Esports combo is N=5, W=inf, vol>=1k: 209 signals/fold (~70/month). N=4 drops HR by 6.6pp but was not swept with a full sensitivity analysis. At N=4 the throughput likely grows significantly (more markets will have 4 vs 5 qualified traders). If tick-by-tick shows N=4 still clears the excess HR bar, deploy a second lower-conviction tier at smaller position size. This would push monthly signal count well above 100.

### 5. Validate the volume filter's availability at signal time

This is a potential showstopper. The sweep uses `total_vol >= 1000` where total_vol is the sum of all qualified-trader net_usd positions in the market. At signal time (when the Nth qualified trader enters), total volume is only the sum of N traders' entries — not the full final market volume. If the $1k filter cannot be computed at signal time, it cannot be used as a deployment filter.

The 2h hold time makes this urgent: there is no time to wait for additional volume to accumulate. The volume signal must be contemporaneous or use a proxy (e.g., order book depth at signal time, or market age + category as a proxy for size).

---

## Category Recommendation

- Current categories: Esports, Tennis
- Typical resolution: 1-4h (genuine sports resolution, not political/cultural ambiguity)
- These are the best possible categories for this strategy. The 2h hold time is a direct consequence of sports markets resolving at match end. Do not expand to slower categories — the compounding score advantage disappears entirely.

For comparison: if this signal were applied to Politics (30+ day resolution), the CS at 33pp excess HR and $15 edge would be: 0.33 x 15 / 30 = **0.165** — below most thresholds of interest. The entire CS advantage is structural to sports resolution speed.

---

## Risk Caveat

The suggestions above are contingent on tick-by-tick validation showing any positive excess HR. The vectorized discount warning is not cosmetic: tag-hr-copy was rejected precisely because the vectorized signal (67% HR) collapsed to 46% tick-by-tick — zero excess HR. tag-hr-consensus fixes the root cause of that collapse (counting unit mismatch), but the fix has not been validated under real execution conditions.

Specific risks that could eliminate the edge even with the right counting unit:

1. **Execution latency**: at 2h median hold, entering 30-60 minutes after the Nth qualified trader means you are buying into a market that may have already moved. Slippage against a fast-moving consensus signal is qualitatively different from copy-trading a slow insider over days.

2. **Bot contamination at pool creation**: the esports_bot classification is proposed but not implemented. Current sweep uses the BOT_GUARD (10,000 positions) as a proxy. If bots with <10,000 positions but HR=0% persist in the pool, the qualified pool is still contaminated.

3. **Non-stationary Esports base rate**: the 2025-10 fold had a 65.4% base rate vs 45.6% in 2026-01. The excess HR calculation is highly sensitive to the per-fold base rate estimate. If deployment uses a stale base rate, the qualified pool threshold shifts and the wrong traders qualify.

4. **Volume filter as look-ahead**: as noted above, if $1k total volume at signal time is not achievable with only N trader positions, the volume filter is a look-ahead bias. The sweep's HR numbers are inflated if the filter is not deployable.

Aggressive capital deployment recommendations above should be held back until tick-by-tick confirms at least one scenario where excess HR > 10pp survives, and until the volume filter availability question is answered.

---

## Summary

tag-hr-consensus has the highest raw compounding scores of any hypothesis reviewed: 61.3 for Esports and 86.5 for Tennis at vectorized upper bounds, driven almost entirely by the 2h median hold time rather than by exceptional edge per trade. The hold time advantage is structural and real — Esports/Tennis markets resolve at match end. Even under the most pessimistic 40pp tick discount, Tennis clears the viability bar, and Esports is viable if any positive excess HR survives.

The capital efficiency case for immediate tick-by-tick validation is unambiguous: if this signal has any real edge, it compounds faster than any existing or proposed strategy by a factor of 30-100x versus sports hold times in s2_insider_copy. The priority question is not whether to validate, but whether to validate Esports or Tennis first. Tennis should go first: higher CS, higher vol filter (less look-ahead risk), and the fragility on W=8h is a parameter that tick-by-tick will validate or kill quickly. Esports follows with N=5, W=inf as the robust combo.

The volume filter deployability question must be answered before any paper deployment, regardless of what tick-by-tick shows. That is the single largest unresolved structural risk.
