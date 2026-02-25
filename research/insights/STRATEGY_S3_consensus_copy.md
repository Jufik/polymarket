# S3: Consensus Copy NO (Fixed Bets, Direction Signal)

**Status**: Active, MEDIUM confidence (only 2 holdout windows)
**Capital allocation**: $200 (2 concurrent $100 bets) — building validation
**Direction**: NO-only (YES consensus is anti-predictive)
**Source insights**: copy/06-07, copy/15, 20_informed_mm_estimate.md

---

## Edge Summary

When >=5 pure_taker consistent traders agree on NO direction in a market, bet NO at fixed $100. Sharpe 3.5-5.0, but validated on only 2 holdout windows (Dec 2025 + Jan 2026). The signal works through **price selection** — finding favorable entry prices when skilled traders take contrarian positions — rather than pure directional prediction.

---

## Non-Tautological Backtest Results (copy/06)

### Direction-Level Summary

| Direction | Count | Avg HR | Avg Sharpe |
|-----------|-------|--------|------------|
| **NO-only** | **2,077** | **45.3%** | **-3.3** |
| YES-only | 1,422 | **18.5%** | -10.6 |
| both | 2,184 | 39.0% | -7.1 |

**YES-only is strongly anti-predictive** — 18.5% HR vs 38.1% base rate. When skilled traders consensus-point YES, the market is LESS likely to resolve YES.

### Top Configurations (All NO-only)

| # | Sharpe | HR | Dir | MVF | Min T | Agree | Band |
|---|--------|-----|-----|-----|-------|-------|------|
| 1 | 5.0 | 57.9% | NO | informed | 10 | 60% | wide |
| 2 | 4.8 | 46.5% | NO | informed | 5 | 100% | wide |
| 4 | 4.5 | 59.9% | NO | pure | 7 | 70% | wide |
| 8 | 4.1 | 62.1% | NO | pure | 7 | 70% | wide |

**Pattern**: NO-only, pure_taker or informed_taker, wide price band [0.05, 0.95], min 5-10 traders, 60-100% agreement.

### Market Base Rates

38.1% of resolved markets have yes_won=True, 61.9% NO-won. The NO side wins 2:1 overall. This context is critical for interpreting HR.

---

## Execution Delay: Signal IMPROVES with Latency (copy/07)

**The top 10 configs overall are ALL at delay >= 30s.** No delay=0s in top 10.

| # | Sharpe | HR | Delay | Dir | MVF | Agree |
|---|--------|-----|-------|-----|-----|-------|
| 1 | 6.04 | 64.5% | **300s** | NO | pure | 70% |
| 2 | 5.93 | 63.4% | **60s** | NO | pure | 70% |
| 3 | 5.91 | 57.0% | **300s** | NO | pure | 80% |
| 8 | 5.13 | 62.9% | **30s** | NO | pure | 70% |

### Mechanism

- **Less liquid markets**: At delay=0s, first trade may be stale. At 30-60s, market processes skilled flow, NO entry price improves. PnL/bet goes from $10.61 to $98.43 (9.3x improvement).
- **Highly liquid markets**: Already efficient at delay=0s; delay doesn't help.
- **300s**: Improvement plateaus, coverage drops to ~80%.

**Optimal delay: 60s.** No latency race needed. The live system can:
1. Detect consensus signal
2. Wait 60s for price stabilization
3. Place order at a BETTER entry price

---

## Edge Decomposition (copy/06)

Positive Sharpe with below-base-rate HR (46-50% vs 62% NO base) explained by entry price:
- At NO entry price p=0.70: win pays $42.86, loss costs $100 → breakeven at 70%
- At p=0.30: win pays $233, loss costs $100 → breakeven at 30.4%

The signal selects markets where skilled traders disagree with YES AND the NO entry price offers positive EV even with <50% hit rate.

---

## Informed Market-Making: DOES NOT WORK (insight #20)

The hypothesis "place NO limit orders inside spread on consensus-signal markets" was thoroughly tested across 12 dev windows.

| Best Config | Taker $/bet | Maker $/bet (2c spread) | Avg HR |
|-------------|:---:|:---:|:---:|
| t5, 70% agree | **-$4.22** | **+$1.83** | 49.1% |

The spread converts a losing taker strategy into a marginal ~$50/month result. **The signal is fundamentally unreliable for market-making** — HR swings 21% to 78% across windows. High agreement (90%+) is anti-predictive.

**Verdict**: Do NOT pursue informed MM on consensus signals. The spread cannot rescue a bad signal.

---

## How S3 Differs from S1

| | S1 Proportional Copy | S3 Consensus Copy |
|---|:---:|:---:|
| Signal source | Individual trader ROI | Crowd NO direction |
| Bet type | Proportional to trader sizing | Fixed $100 |
| Direction | Follows trader | Always NO |
| Markets | All pool trades | Only consensus-NO markets |
| Overlap | LOW (rarely same markets) | LOW |
| Confidence | HIGH (9/9 months) | MEDIUM (2 windows) |

---

## Recommended Configuration

| Parameter | Value |
|-----------|-------|
| Pool | 6-9m consistent, pure_taker |
| Min traders | 5-7, agreement >= 60% |
| Direction | NO-only |
| Delay | 60s |
| Price band | Wide [0.05, 0.95] |
| Capital | $200 (building validation data) |

---

## Validation Needed

- Extend holdout to 6+ windows (currently only Dec 2025 + Jan 2026)
- Small bet counts (20-65 per window) — susceptible to noise
- Jan 2026 performance (Sharpe 5-7) may not generalize
- Base rate dominance (61.9% NO) inflates apparent signal quality
