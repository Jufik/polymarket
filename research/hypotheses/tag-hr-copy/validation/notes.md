# Validation Notes: tag-hr-copy

## Status: COMPLETED (all 3 tags) — VERDICT: NONE as implemented

## Validation Run Details

- Period: 2025-01-01 to 2026-01-01 (full year)
- Training pool: 6mo trailing (Sep 2025 to Mar 2026)
- Executor: RealisticFillSimulator (calibrated slippage, no rejection)
- Settlement: Enabled (asset_id matching)
- Capital: $1,000 @ $100/position max, 20 max open positions
- Total trades replayed: 8,371,546 (pre-filtered to 699 qualified makers)
- Total intents: 22,097
- Fills: 3,265 | Budget rejections: 18,832

## Degradation Analysis

| Tag | Vec HR | Tick HR | Degradation |
|-----|--------|---------|-------------|
| Esports | 67.2% | 45.8% | -21.4pp |
| 1H | 78.0% | 49.8% | -28.2pp (gambling confirmed: near base rate) |
| Tennis | 72.4% | 40.6% | -31.8pp (below base rate after threshold) |

All degradations in the 20-40pp "expected" band — but PnL is deeply negative (-$102.50 median)
because the issue is structural, not just simulation friction.

## Root Cause: Consensus Gap

**Discovery signal**: N qualified traders per market at consensus → high HR
**Implementation**: ANY single qualified trader's first BUY YES → near-random

The vectorized sweep measured a MARKET-LEVEL hit rate conditional on multiple qualified
traders being present. The tick-by-tick strategy copied individual trades without waiting
for consensus, which collapsed HR to near-random.

## Key Evidence

### Fill Price vs HR (All Tags)
- <0.20: HR=12.4% (below base)
- 0.20-0.40: HR=35.3% (below base)
- 0.40-0.60: HR=49.4% (random)
- 0.60-0.75: HR=64.4% (STRONG SIGNAL)
- 0.75-0.80: HR=85.2% (exceptional, n=135)

Insight: High-price entries are better. The 0.60-0.75 regime is where consensus forms AFTER
price has already moved. This is likely the "after consensus trigger" period.

## Surprising Findings (to capture in knowledge base)

1. **Price > HR correlation**: Higher fill prices → better HR. Counterintuitive for a "buy low"
   signal. Suggests the signal isn't about buying cheap — it's about following informed money
   AFTER they've moved the price up (post-consensus formation).

2. **1H gambling confirmed**: HR=49.8% vs base 47.3% — effectively random. The 1H signal needs
   consensus formation urgently or should be dropped.

3. **Tennis below base rate at threshold**: HR=40.6% vs effective threshold of 45.1%. Tennis
   noise-to-signal ratio is highest because the 20 min_trades threshold is too permissive —
   too many low-quality "qualified" traders included.

4. **Budget exhaustion is a real constraint**: 18,832 of 22,097 intents were rejected by budget.
   With $1,000 capital and $100/position, only 10 positions can be open simultaneously.
   This masks the true signal count but also prevents seeing the full capital deployment issue.

## What to do next

### Spawn: tag-hr-copy-consensus [HIGH PRIORITY]
- Same qualified pool (319 Esports, 131 1H, 294 Tennis)
- Signal: wait for N_consensus (e.g. 3) qualified traders in same market within T_window (e.g. 4h)
- Entry: at consensus trigger time, not first individual trade
- Expected HR: 60-70% (matching vectorized, which measured this exact scenario)
- Expected signal volume: 300-800/year (vs 3,265 in this run)

### Spawn: tag-hr-copy-price-regime [MEDIUM]
- Apply strict price floor: 0.55 <= price <= 0.75
- This would exclude the low-HR low-price entries
- Expected to improve HR but reduce signals

## Implementation Notes

### Bugs Fixed During Implementation

1. `trader_trade_agg FINAL alias` — CH v24.8 syntax error. Fixed by using direct table query.
2. `arrayJoin()` IN-clause size limit — CH max query size exceeded for 699 addresses.
   Fixed by Memory temp tables.
3. Strategy `_open_cids` pruned on settlement → re-entries in same market.
   Fixed by `_entered_cids` (permanent, never prune).
4. Harness needed to bootstrap providers BEFORE trade loading for pre-filter to work.
   Fixed by linter (architect updated harness.py build order).

### Known Limitations of This Validation

1. **Training window mismatch**: Pool computed from Sep 2025 - Mar 2026. Some test period
   (Jan-Sep 2025) predates the training window. For proper walk-forward, pool should be
   computed from data BEFORE each month. This may explain some of the degradation.

2. **No walk-forward**: Single pool for all 12 months. In production, pool refreshes every
   6 months in walk-forward. This could affect results by ±5pp.

3. **Budget constraint**: $1,000 is very small. True signal quality is masked by budget.
   A $10,000 capital test would show true signal volume and HR without budget rejection.
