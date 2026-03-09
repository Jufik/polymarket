# Challenger Review: edge-weighted-skill (Round 1)

> All discovery results are VECTORIZED UPPER BOUNDS. Expect 20-40pp tick degradation for standard tracks.
> In-play tick degradation is LARGER (50-60pp estimated) due to latency sensitivity.

Date: 2026-03-09

---

## Track Summary

Three viable tracks identified in discovery:

| Track | HR | Excess HR | PnL/mo | Signals/mo | Avg Hold | Compounding Score (raw) |
|-------|----|-----------|--------|------------|----------|------------------------|
| In-Play K=10 N=1 | 90.9% | +52.1pp | $1,553 | 121 | 3.1h (~0.13d) | very high |
| Consensus K=200 N=2 | 63.7% | +24.9pp | $3,582 | 761 | 5.4h (~0.23d) | very high |
| Edge Copy K=100 N=1 | 51.1% | +12.3pp | $665 | 2,121 | 12.6h (~0.53d) | moderate |

---

## Compounding Score Assessment

Formula: `excess_hr x avg_edge_usd / median_hold_days`

Avg edge per trade derived from PnL / signals (per month, $100 stake basis):

### In-Play K=10 N=1
- Excess HR: 52.1pp = 0.521
- Avg edge/trade: $1,553 / 121 = $12.83/signal (but PnL is net — many months may be negative; single-month sample)
- Median hold: ~0.13 days (3.1h)
- **Compounding score: 0.521 x 12.83 / 0.13 = ~51.4**
- Benchmark: far above target of 0.5+ — exceptional capital recycling on paper

### Consensus K=200 N=2
- Excess HR: 24.9pp = 0.249
- Avg edge/trade: $3,582 / 761 = $4.71/signal
- Median hold: ~0.23 days (5.4h)
- **Compounding score: 0.249 x 4.71 / 0.23 = ~5.1**
- Benchmark: well above 0.5+ — fast recycling, meaningful throughput

### Edge Copy K=100 N=1
- Excess HR: 12.3pp = 0.123
- Avg edge/trade: $665 / 2,121 = $0.31/signal
- Median hold: ~0.53 days (12.6h)
- **Compounding score: 0.123 x 0.31 / 0.53 = ~0.072**
- Benchmark: BELOW target of 0.5 — thin edge per trade kills compounding despite high volume

---

## Hold Time Analysis

### In-Play K=10 N=1
- Median: ~3.1h (0.13 days)
- Hold distribution: tightly concentrated (sports/esports during active games)
- Capital turns per month: ~230x theoretical
- **Assessment: exceptional recycling rate — but 99% sure-thing regime means almost no real edge vs base rate**

### Consensus K=200 N=2
- Median: ~5.4h (0.23 days)
- Hold distribution: short, likely concentrated in crypto and sports in-play markets
- Capital turns per month: ~130x theoretical
- 90th percentile unknown — need tick data to confirm no long-tail
- **Assessment: best balance of throughput + hold time in this discovery**

### Edge Copy K=100 N=1
- Median: ~12.6h (0.53 days)
- Hold distribution: wider — 2,121 signals across diverse market types
- Capital turns per month: ~56x theoretical
- **Assessment: hold time is acceptable but per-trade edge is too thin ($0.31) to justify deployment**

---

## Critical Challenges

### 1. In-Play Track — The Sure-Thing Problem
The 90.9% HR for In-Play K=10 is almost entirely a sure-thing artifact:
- 99% of signals in sure-thing regime (0.85+ price)
- Vectorized HR = ~99% in this bucket by base rate alone
- Excess HR vs base rate is what matters — discovery shows only +52pp which implies ~38% base HR baseline
- With 50-60pp expected tick degradation for in-play, realistic excess HR collapses to ~-8pp (negative edge)
- The $1,553/month PnL is from 121 signals at $100 stake — survives only if slippage is near-zero
- **The in-play track is NOT viable until tick validation confirms positive PnL post-degradation**

### 2. Consensus K=200 N=2 — Pool Stability Uncertainty
- K=200 pool has never been walk-forward tested (discovery only tested K=25/50/100)
- Pool overlap between edge-pool and HR-pool is only 17% at K=100; at K=200, likely higher dilution
- 5.4h hold suggests crypto-heavy mix — Fold 2 Crypto edge_primary K=25 showed σ=0.236 (catastrophic instability)
- Need month-over-month pool composition stability before trusting $3,582/mo figure

### 3. Edge Copy K=100 — Volume Without Edge
- 2,121 signals/month is impressive throughput, but $0.31 avg edge/trade is noise-level
- Any realistic execution cost (1-2pp spread) wipes the edge entirely
- This track should not be prioritized for validation resources

---

## Capital Efficiency Suggestions

### For In-Play Track (K=10):
1. **Impose strict sure-thing filter**: Exclude signals where market price > 0.85 at entry. This collapses signal count from 121 to ~19 genuine edge signals but prevents 99% of capital from sitting in near-certain bets with no alpha.
2. **Time-exit within 6h**: If market does not resolve, close position. In-play positions are live-event dependent — stale positions post-event become dead capital.
3. **Do not expand to K=25+**: K=25 inplay drops to 77.2% HR with 3x more signals and likely same vectorized inflation. Signal quality degrades faster than throughput improves.

### For Consensus Track (K=200 N=2):
1. **Run at N=2, not N=3**: N=3 drops to 53.2% HR and -$776/month. Consensus filter is most valuable at exactly N=2; further consensus adds no value and destroys throughput.
2. **Enforce 24h max hold**: 5.4h median means long-tail outliers may lock capital. Any position not resolved in 24h is likely a politics/elections market — close it.
3. **Tag filter**: Exclude Elections tag explicitly. Walk-forward shows σ=0.27-0.33 for Elections — incoherent signal, not signal.
4. **Monitor pool composition monthly**: If top-200 edge-weighted pool shifts > 40% month-over-month, halt and retrain before next deployment.

### For Edge Copy Track (K=100):
1. **Do not validate** — compounding score of 0.072 is structurally insufficient. The thin per-trade edge cannot survive realistic execution costs.
2. If consensus track proves viable, Edge Copy K=100 N=1 is already a degenerate case of it (N=1 is just copy). Consolidate into the consensus framework.

---

## Category Recommendation

| Track | Primary Category | Typical Resolution | Assessment |
|-------|-----------------|-------------------|------------|
| In-Play K=10 | Sports (in-play) | < 4h | Fast enough — but base rate dominates |
| Consensus K=200 N=2 | Mixed (Crypto-heavy) | 5-8h | Acceptable, needs tag audit |
| Edge Copy K=100 | Mixed | 12.6h | Too slow for thin edge |

Recommended: **Consensus K=200 N=2 is the capital-efficient winner if validated**. It clears the 0.5+ compounding threshold even after expected tick degradation, assuming 15pp realistic excess HR at 0.23-day holds = compounding score ~3.0.

Avoid Politics and Elections in all tracks until resolution time is confirmed. Politics markets resolve in 30+ days — any signal there locks capital for a month.

---

## Tick-by-Tick Degradation Projection

Apply standard 20-40pp vectorized-to-tick degradation (conservative: 30pp):

| Track | Vectorized Excess HR | Realistic Excess HR (est.) | Compounding Score (realistic) | Viable? |
|-------|---------------------|---------------------------|-------------------------------|---------|
| In-Play K=10 | +52.1pp | ~-8pp to +2pp | < 0 | Probably not |
| Consensus K=200 N=2 | +24.9pp | ~+5pp to +15pp | 0.5 – 3.1 | Possibly yes |
| Edge Copy K=100 | +12.3pp | ~-18pp to -8pp | < 0 | No |

In-play faces LARGER degradation (50-60pp) per discovery notes. At -8pp realistic, the in-play track is a capital destroyer at scale.

---

## Risk Caveat

If Consensus K=200 N=2 is pushed too aggressively (e.g., to K=300, N=1 for volume), pool dilution destroys the quality filter. The edge-weighted scoring is only as good as the underlying trader quality — expanding K degrades the pool's mean bucket_excess_hr. The 517.64 vectorized CS for K=200 N=2 exists because it sits in the sweet spot of quality x throughput. Do not chase throughput by relaxing K or N.

---

## Summary

**Consensus K=200 N=2** is the only track with a credible capital efficiency argument. Its 5.4h average hold and 761 signals/month gives fast capital recycling even after tick degradation. It should proceed to tick validation immediately. In-Play K=10 has extraordinary paper numbers but almost certainly degrades to zero or negative edge in tick simulation — the vectorized HR is base-rate inflation in the sure-thing regime, not genuine alpha. Edge Copy K=100 is a high-volume dead end with per-trade edge below any realistic execution cost. Prioritize validation resources on Consensus K=200 N=2 with Elections excluded and a 24h max hold rule; reject In-Play and Edge Copy without tick proof of positive excess HR.
