# Visionary Review: tag-hr-consensus — Signal Quality After 6 Rounds

**Reviewer**: Visionary
**Date**: 2026-03-06
**Basis**: R1–R6b results, exploration artifacts (sharp_pool, signal_time_vol, dissent, copy_trader),
plans/graduated_sizing.md, full knowledge base, tag-hr-copy README

---

## State of Play

Six rounds have established the following hard facts:

1. Tennis K=50, N=3, vol>=200, no tier gate (R6b): +30.94pp excess, +$185 PnL over 2 active folds
2. Esports K=50, N=3, no gate, no dissent (R6b 2025-07): +13.86pp excess, +$871 PnL, Sharpe=1.75
3. The 2026-01 fold is dead for both tags: price_ceil=0.75 filters everything
4. The 2025-10 Esports fold is structurally hostile (65.4% test base rate, 52.5% strategy HR = -12.9pp)
5. Copy-trader contamination is REJECTED as failure mode: followers have equal or higher HR than leaders
6. Sustainability tiers (R6) do not help: T3 dominates 60-72% of the pool, tier gate kills count

The binding constraint is now one of economics, not signal quality:
- Tennis: signal is real (+30pp excess) but avg fill = 0.545 means break-even HR is 54.5%. The strategy clears it by only ~3pp. PnL is thin: +$185 on 84 signals.
- Esports: signal works in low-base-rate folds (+13.9pp excess) but collapses in high-base-rate folds. At avg fill 0.474 in the good fold, break-even HR is 47.4%. Strategy clears it comfortably (50.6%) but margin is small in dollar terms.

The path forward is not tuning signal parameters further. It is attacking the economics.

---

## Adjacent Signals

### 1. Entry-Depth Continuous Sizing (the copy_trader finding is unused)

The copy_trader analysis found that HR is MONOTONICALLY INCREASING with entry order depth:

- Esports 2025-07: 1st entry HR=47.2%, 4th entry HR=72.6% (+25.4pp)
- Tennis 2026-01: 1st entry HR=52.4%, 4th entry HR=70.0% (+17.6pp)

The current strategy fires at the Nth qualified trader and then stops. It ignores the signal that comes from N+1, N+2, ... later entries. This is leaving money on the table.

Concrete test: instead of a binary fire/no-fire at N=3, use the actual depth at signal time as a continuous position multiplier. Strategy fires at N=3 (minimal size), then adds to position at N=4 and N=5 entries if they arrive within a time window.

```
size = base * (1.0 + 0.5 * max(0, n_qualified_entries - N))
cap at 2.5x
```

At N=5+ entries, the expected HR is ~70%+ (from copy_trader data). At avg fill 0.45, break-even is 45%. The edge at depth is large enough to justify oversized positions.

DuckDB test: bucket signals by final consensus depth (N=3, N=4, N=5, N=6+) and compare HR. If depth-5 HR is confirmed at 65%+ across folds, pyramid sizing is the single highest-leverage improvement remaining.

### 2. Market Coverage Ratio as a Regime Detector

The copy_trader analysis identified but did not implement this: "When pool_size/n_test_markets < 0.1, the signal is too sparse."

The 2026-01 Esports fold: 517 qualified traders / 13,538 test markets = 0.038 -- well below 0.1. This ratio predicts signal quality failure independently of base rate.

For Tennis: 315 traders / 9,474 markets = 0.033 in 2026-01. The 2026-01 fold is dead for both tags. The coverage ratio would have predicted this before running any tick-by-tick validation.

Concrete test: add `coverage_ratio = pool_size / n_test_markets` as a per-fold gate. Skip folds where coverage_ratio < 0.05. Expected effect: the 2026-01 fold is cleanly excluded without needing price_ceil to do the work.

This is distinct from and complementary to the base rate regime gate: the base rate gate handles Esports 2025-10, the coverage ratio gate handles 2026-01.

### 3. Consensus Velocity: Time-to-Nth-Trader as Signal Quality Predictor

The current strategy treats a consensus of 3 that formed in 5 minutes identically to one that formed over 2 hours. But fast consensus formation implies independent simultaneous discovery -- multiple traders examining the same market at nearly the same time suggests an external information event (match scheduled, roster announced, odds movement).

Concrete test: compute time_to_consensus = max(entry_time for traders 1..N) - min(entry_time for traders 1..N). Bucket by fast (<30min), medium (30min-2h), slow (>2h). Compare HR per bucket.

Expected finding: fast consensus carries different information than slow consensus. Fast = event-driven independent discovery (strong signal). Slow = sequential copying (weaker but still positive per copy_trader data). This would justify separate treatment rather than filtering either out.

### 4. Inverse Signal: NO Consensus in High-Coverage, Low-Base-Rate Markets

The strategy is entirely YES-biased. But the dissent exploration shows qualified NO traders exist and provide information. When dissent_ratio < 0.30 (qualified traders are 70%+ on the NO side), a NO entry should outperform the NO base rate by a similar margin to YES entries in favorable regimes.

The dissent.json results already show: Esports pure-YES (dissent=1.0) achieves 91-100% YES HR. The inverse -- high qualified-NO concentration -- should achieve comparably high NO HR in the right markets.

Concrete test: extend the DuckDB sweep to track markets where n_qual_no >= N and dissent_ratio <= 0.30. Compare NO HR to NO base rate. If +10pp excess exists, this is a deployable companion signal that fires in the same infrastructure with inverted direction.

This doubles signal count potential without changing pool construction.

---

## Parameter Variations

### 1. Price Ceiling at 0.40-0.45: The One Untested Fix That Most Changes Economics

This was identified as the #1 fix in the README anti-knowledge section and in Round 1 and Round 2 visionary reviews. After six rounds it still has not been tested in tick-by-tick.

The economics case is clear:
- At price_ceil=0.75 and avg fill=0.545 (Tennis): break-even HR = 54.5%, strategy achieves 57.9% = +3.4pp margin
- At price_ceil=0.45 and likely avg fill=0.40: break-even HR = 40%, strategy achieves ~57% = +17pp margin

The fill price and the price ceiling are not independent. Lowering the ceiling forces entries at lower prices, which shifts the economics dramatically. At 0.40 entry, a 55% HR earns +$0.60 on wins vs -$0.40 on losses = expected PnL of +$0.14 per $1 risked = +14% return. This is a qualitatively different strategy.

The only risk: how many signals survive after price_ceil=0.40? The R5 gate_stats show skip_price=91 for Tennis 2025-07 with price_ceil=0.75. With price_ceil=0.40, more signals are skipped but the ones that fire are at better prices.

Concrete test: re-run the tick-by-tick with price_ceil={0.40, 0.45, 0.50} while holding all other R6b parameters constant. This is one validation run with three parameter values.

### 2. Consensus N as Function of Coverage Ratio (Not Pool Size)

The dynamic N idea appeared in Round 2 as `max(3, ceil(pool_size * 0.08))`. But pool_size is not the right denominator -- coverage ratio (pool_size / n_test_markets) is. When coverage is low (few traders per market), a higher N requirement filters out spurious consensus from random overlap.

Revised formula:
```
n_required = max(3, ceil(N_base / max(coverage_ratio, 0.01)))
```

At coverage=0.30 (2025-07 Esports good fold): n_required = max(3, ceil(3/0.30)) = max(3, 10) = 10
At coverage=0.05 (2026-01 bad fold): n_required = max(3, ceil(3/0.05)) = max(3, 60) = 60 -- effectively suppressed

This is more principled than a hard cap because it adapts to market density rather than raw pool size.

### 3. Tag-Pair Deployment: Sports (non-Tennis, non-Esports) Vectorized Screen

Prior reviews suggested NBA (38K markets, 46.3% YES base rate) as the next category to screen. This is correct but incomplete. The Sports aggregate (227K markets, 40.1% YES base rate) includes categories with even lower base rates and potentially high specialist density.

The proposal: run a single vectorized DuckDB sweep over Sports (excluding Tennis) with the same pool construction as R5. This identifies whether any Sports sub-category has qualified pool sizes of 20-80 traders (the sweet spot demonstrated in Tennis 2025-07 and Esports 2025-07).

Parameters: meh=10pp, mpe=0.80, N=3, K=50 -- identical to R5. Single sweep, <5 minutes. If any Sports sub-tag produces vectorized excess >20pp with >30 signals per fold, it is a candidate for tick-by-tick at no additional framework cost.

This is the cheapest possible expansion: same code, different tag filter.

### 4. Training Window Shortening to Reduce Stale Pool Members

Current training window: 180 days. Esports base rate swings 28pp within a single 180-day period. A trader qualified on a 37% base rate environment is over-stated when the test environment has 65% base rate.

Test: replace 180-day with 90-day rolling window. Key question: does a 90-day Esports window still find 30-50 traders with >=5 qualifying markets and >=10pp excess HR?

The answer determines viability. If yes, a 90-day window requalifies pools quarterly and adapts to base rate regime shifts. If no (too thin), consider 120-day as a compromise.

This directly addresses the Esports non-stationarity without requiring a regime gate: the pool is always calibrated to recent performance.

---

## Cross-Hypothesis Connections

### Connection to tag-hr-copy: The Depth Finding Inverts the Original Failure Lesson

tag-hr-copy failed because individual trades != consensus (README reference: `/mnt/nvme/git/polymarket/polymarket/research/hypotheses/tag-hr-copy/README.md`). The lesson encoded was: wait for N=3+ before firing.

The copy_trader analysis now shows the opposite dynamic at depth: beyond N=3, each additional qualified entry is MORE informative, not less. The tag-hr-copy failure was at N=1 (individual). The copy_trader data shows N=4-5 is where the signal peaks. This reframes the entire progression:

```
N=1 (individual):     47.2% HR  -- structurally broken (tag-hr-copy failure)
N=2 (pair):           57.8% HR  -- first signal emergence
N=3 (triple):         64.1% HR  -- current strategy trigger
N=4 (quad):           72.6% HR  -- peak signal strength
N=5 (quint):          72.4% HR  -- signal plateaus
```

The insight: the correct entry is not AT N=3 but at N=3 minimum and pyramiding through N=4-5. The original tag-hr-copy failure was real but it describes only the N=1 behavior. The hypothesis should be renamed mentally: this is not a consensus strategy, it is a depth-of-conviction strategy.

### Connection to period_base_rate_variance.md and the Unexplained Esports 2025-10 Spike

The knowledge base documents global monthly base rate swings of up to 15pp (`research/knowledge/data/period_base_rate_variance.md`). Esports 2025-10 shows a 28pp within-tag spike (37% train -> 65% test). This is almost 2x the documented global variance.

This spike is undocumented in the knowledge base as a tag-specific phenomenon. It needs to be captured: Esports tag base rate has documented within-fold spikes of 20pp+ that exceed global seasonality. The cause is tournament calendar composition (a tournament with dominant-team structure -- e.g., a team that wins 70% of its matches in a given period -- spikes the YES base rate for all markets in that tournament).

The actionable connection: if the Esports 2025-10 spike can be traced to specific tournaments in the Gamma event metadata, a simple `event_id` blacklist for tournaments with known dominant-team dynamics would have prevented the entire 2025-10 disaster without needing a base rate regime gate.

Reference for knowledge capture: `/mnt/nvme/git/polymarket/polymarket/research/knowledge/data/period_base_rate_variance.md`

### Connection to vectorized_tick_gap_anatomy.md Step 3: The Niche-vs-Popular Distinction

The gap anatomy document warns that consensus 5+ is anti-predictive in popular markets. The copy_trader data confirms the opposite for Tennis and Esports -- HR increases monotonically to N=5. These are niche specialist markets, not popular generalist markets.

The distinction should be formalized: the anti-prediction consensus effect (Step 3 warning) applies specifically to markets where the qualified trader pool is a proxy for public attention (many traders = popular market = efficiently priced). In niche specialist markets (few traders = specialist knowledge = informational advantage), the relationship inverts.

This is a testable hypothesis: partition markets by total trade count (proxy for popularity). Low-trade-count markets should show monotonically increasing HR with consensus depth. High-trade-count markets should show the degradation pattern documented in Step 3. If confirmed, the `market_popularity` dimension becomes a feature of the signal, not just a confound.

Reference: `/mnt/nvme/git/polymarket/polymarket/research/knowledge/pitfalls/vectorized_tick_gap_anatomy.md`

### Connection to graduated_sizing.md Track 1 (Time-to-Live Sizing) -- Now Has a Use Case

The TTL sizing track was deprioritized in R6 as "post-profit" optimization. But the Tennis 2025-07 fold reveals a specific problem TTL sizing would address: avg_hold_hours=11.52h with avg fill=0.545. Markets resolving in 12+ hours at prices above 0.50 are the worst risk/reward profile. They lock capital for half a day at near-break-even prices.

If TTL sizing were applied as a filter (skip markets with expected hold > 8h at fill price > 0.50), the Tennis 2025-07 fold would discard its low-quality slow-resolving markets and retain only the fast high-conviction ones. Combined with price_ceil=0.45, this addresses both dimensions of the economics problem simultaneously.

---

## Compounding Improvements

### 1. The Pyramid Entry: Most Impactful Remaining Change

From the copy_trader depth table, each additional qualified entry above N=3 adds ~8-10pp to HR. The current strategy fires at N=3 and ignores subsequent entries. A pyramid entry strategy:

- At N=3: enter at 0.33x base size ($33 on $100 base)
- At N=4: add 0.33x more ($33)
- At N=5: add 0.33x more ($33)

Total maximum position: base size. Average position: between base/3 and base, depending on how deep the consensus goes.

Expected improvement: average entry across the depth distribution will be smaller (fewer signals reach N=5), but the N=4 and N=5 signals carry 65-72% HR versus the N=3 baseline of 64%. The PnL improvement comes from two effects: better sizing on high-conviction signals AND lower average entry price (entering smaller on N=3 when price may be 0.55+, and adding on N=4-5 when additional confirmation may push price up or stay flat).

This does not require new data sources or framework changes. It requires modifying the strategy to continue processing entries after the initial trigger.

### 2. The 2026-01 Dead Fold: Skip Cleanly With Coverage Gate, Not Price Filter

Currently the 2026-01 fold produces 0 signals because price_ceil=0.75 kills them all. This is a blunt instrument: the fold is dead because coverage is too low (pool_size/markets << 0.05), not because the signal is bad.

The cleaner fix is to skip the 2026-01 fold entirely at the fold-selection stage, before even running the strategy. A coverage gate of pool_size/n_test_markets >= 0.05 would accomplish this with a single additional parameter.

This matters for capital deployment: if the strategy knows it should not trade in the 2026-01 regime, it can redirect capital elsewhere (different tag, paper trading) rather than burning CPU cycles on a confirmed-dead fold.

### 3. Early Exit on Qualified Trader Exits

The strategy currently holds to settlement. But the copy_trader analysis showed followers track leaders closely (26-58% of entries within 30 minutes in the later folds). If qualified traders EXIT their YES positions, that is an updated information signal.

Concrete implementation: monitor qualified trader SELL trades for condition_ids where we hold a position. If any qualified trader with excess_hr > 15pp sells their YES position while we are in a live trade, reduce or close our position.

This is the only exit signal that uses the same information source as the entry signal. It does not add latency requirements -- the existing trade stream already captures SELL trades. The strategy just needs to track them against its open positions.

Expected effect: reduces losses on markets where the qualified traders lose conviction before resolution. The cost is early exit on some markets that would have resolved YES anyway. In net, this should improve the Sharpe more than it hurts PnL.

### 4. Cross-Tag Capital Pooling: Tennis + Esports on a Shared 50-Slot Budget

Tennis avg_hold = 11.52h (2025-07), 3.63h (2025-10). Esports avg_hold = 6.52-9.67h (R6b).
These are approximately anti-correlated in timing: Tennis peaks during daytime (matches schedule), Esports peaks in evenings (tournament streams).

With shared capital and 50 slots, the throughput math:
- Tennis alone at 11h avg hold: 50 * 24/11 = ~109 positions/day
- Esports alone at 8h avg hold: 50 * 24/8 = 150 positions/day
- Combined, non-overlapping: ~200+ positions/day (with proper slot management)

But the most important compounding factor: Tennis trades when Esports is quiet and vice versa. The shared capital pool has near-zero idle time during active periods. At $100/position with 200 positions/day, the daily capital throughput is $20,000 -- meaningful scale even at modest per-position PnL.

---

## New Hypothesis Ideas

For `research/ideas.md` backlog:

1. **consensus-depth-pyramid**: Fire at N=3 with 0.33x size, pyramid to 0.33x more at each subsequent qualified entry (N=4, N=5). Exit pyramid at N=5 or time window close. Expected HR improvement: +8-10pp per depth level per copy_trader data. Entry price advantage: smaller position at N=3 (potentially higher price) replaced by larger position at N=4-5 (confirmed direction). Priority: HIGH -- requires no new data, pure strategy logic change.

2. **market-coverage-regime-gate**: Add per-fold gate: skip if `pool_size / n_test_markets < 0.05`. Directly addresses the 2026-01 dead fold more cleanly than price ceiling. Testable as one additional parameter in the existing framework. Priority: HIGH -- one parameter, three lines of code.

3. **sports-consensus-screen**: Apply the R5 consensus framework to the Sports aggregate (excluding Tennis, including NFL, MLB, Soccer, NBA separately). Single vectorized DuckDB pass with existing parameters. Identifies whether any Sports sub-tag has a viable specialist pool. Priority: MEDIUM -- cheap screen that sets up next hypothesis cycle.

4. **no-consensus-companion**: Track qualified traders who enter NO positions. When dissent_ratio <= 0.30 (70%+ of qualified traders are on NO side) AND qualified-NO count >= N, fire a NO entry. Companion signal to the YES consensus with the same infrastructure. Expected PnL: comparable to YES consensus by symmetry. Priority: MEDIUM -- requires dissent tracking already implemented, just needs direction flip.

5. **consensus-velocity-signal**: Measure time_to_consensus (time from 1st to Nth qualified entry). Fast consensus (<30min) = event-driven independent discovery = potentially stronger signal. Slow consensus (>2h) = sequential information diffusion = weaker but still positive. Test whether these two consensus types should carry different position sizes or be treated as separate signals. Priority: MEDIUM -- one additional field to compute, bucket and compare HR.

6. **esports-tournament-blacklist**: Identify which tournament structures cause YES base rate spikes in Esports (dominant-team tournaments). Use Gamma event metadata to build a blacklist of event types where the YES base rate historically exceeds 55%. Skip these events entirely. Prevents the 2025-10 Esports disaster class of failures at the event level rather than the fold level. Priority: MEDIUM -- requires CH query joining events to outcomes, but highly targeted fix.

7. **consensus-exit-on-reversal**: Track qualified trader SELL trades for open positions. If a qualified trader with excess_hr > 15pp sells their YES position in a market where we are long, reduce position by 50%. If two qualified traders sell, close entirely. This is the only dynamically-updated exit signal that uses the same information source as the entry. Priority: LOW -- adds operational complexity, but elegant in principle and eliminates the worst losing positions.

---

## Summary

After six rounds, the signal exists and is confirmed real (Tennis +30.94pp excess, Esports +13.86pp in the good fold). The hypothesis is not failing from bad signal quality -- it is failing from bad economics. The average fill price of 0.47-0.55 makes break-even HR 47-55%, and the strategy barely clears it.

Three changes, applied together, would convert MARGINAL to PROMISING in a single validation round:

First, lower price_ceil from 0.75 to 0.40-0.45. This has been the #1 recommended fix since Round 1. At price_ceil=0.40, break-even HR drops to 40%, and the demonstrated +30pp excess in Tennis is more than sufficient. This single change changes the economics of every fold.

Second, add the pyramid entry from the copy_trader depth data. Instead of firing a full position at N=3, fire a third-size position and scale up at N=4 and N=5. This improves average HR from 64% (N=3) to 70%+ (N=4-5) while reducing average position size at uncertain signals. The copy_trader finding that HR increases monotonically with depth is the most valuable empirical result in the entire exploration corpus and has not yet been operationalized.

Third, add the coverage ratio gate for the 2026-01 regime. This cleanly kills the dead fold without price filtering, freeing the price filter to do the economic work it is actually needed for.

These three changes are testable in a single tick-by-tick run with the existing strategy_r5.py framework. The expected outcome: Tennis moves from +$185 to +$600-800 across 2 active folds. Esports remains conditionally viable (good folds only). The combined portfolio with 50 shared slots should produce positive PnL across all non-hostile folds.

The biggest open question remains: does price_ceil=0.40 kill signal count entirely in Tennis (where the 2025-10 fold already had only 5 signals at K=50, N=3, vol>=200)? The gate_stats show skip_price counts rising steeply with lower ceilings. This must be measured before committing to the price floor approach.
