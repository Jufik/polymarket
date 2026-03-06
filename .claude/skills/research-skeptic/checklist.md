# Skeptic Detailed Checklist

## 1. Look-ahead Bias

**What to check**: Any feature or signal that uses information not available at the time the trading decision would be made.

**How to check**:
- Read SQL in `discovery/sweep.sql` — does it join on resolution data for feature construction?
- Read strategy code — does `on_trade()` access features computed from future trades?
- Check timestamp ordering — are features computed from trades strictly before the current trade?

**Failure examples**:
- Using `resolved_epoch` to compute pre-trade features
- Consensus computed including trades after the entry trade
- VWAP computed using trades that happen later the same day

## 2. Survivorship Bias

**What to check**: Universe restricted to markets that had specific post-hoc characteristics.

**How to check**:
- Check market selection SQL — does it filter on metrics only knowable after resolution?
- Volume filters: "markets with > 100 trades" might exclude illiquid-but-valid markets

**Failure examples**:
- Only including markets that resolved within 30 days (excludes long-lived markets)
- Requiring "active" trading in final week (biases toward markets with clear outcomes)

## 3. Edge Above Base Rate

**What to check**: Reported hit rate compared to the unconditional base rate for the predicted direction.

**How to check**:
- NO direction: base rate = 62%. Excess = reported_no_hr - 0.62
- YES direction: base rate = 38%. Excess = reported_yes_hr - 0.38
- Mixed direction: weight by proportion of each direction in the strategy

**Failure threshold**: excess < 5pp is `> [!WARNING]`, excess < 2pp is `> [!CRITICAL]`

## 4. Sample Size

**What to check**: Total trades and per-parameter-combo trade counts.

**How to check**:
- Total trades across full period
- Per-parameter combo counts from sweep results
- Per-month counts (ensure not concentrated in one period)

**Failure threshold**: < 50 total is `> [!CRITICAL]`, < 100 is `> [!WARNING]`

## 5. Walk-Forward

**What to check**: Whether parameters were selected on the same data used for evaluation.

**How to check**:
- Is there a separate train/test split?
- Are sweep parameters chosen on train, evaluated on test?
- Or are best parameters from full-sample used for reporting?

**Failure**: All-in-sample with no walk-forward is `> [!WARNING]`

## 6. Degradation Band (Round 2 only)

**What to check**: Gap between vectorized and tick-by-tick hit rates.

**How to check**:
- Read `validation/summary.json` for tick-by-tick HR
- Compare with discovery vectorized HR
- Compute: degradation_pp = vec_hr - tick_hr

**Failure**: < 10pp is `> [!CRITICAL]` (too good), > 40pp is `> [!WARNING]` (too bad)
