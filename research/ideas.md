# Strategy Research Idea Backlog

## Queued

(see spawned sections below)

## In Progress

(none)

## Tested

### inplay-early-trigger
**Status**: REJECTED (2026-03-09) -- structurally invalid
**Signal**: Enter when InPlay (N=1) fires if Sports consensus (N=2) already exists
**Finding**: Hypothesis is a tautology -- N=1 always fires before N=2 on same pool.
Sports consensus cannot exist at InPlay's signal time. No look-ahead-free variant improves
on Sports standalone. Sports N=2 has 66x better compounding score (148 vs 2.2).
**Key insight**: Price moves toward correct outcome between 1st and 2nd trader entry,
giving Sports BETTER entry prices on long-shot winners ($595 avg win vs $80 at InPlay time).
**Analysis**: `research/hypotheses/inplay-early-trigger/discovery/analysis.md`

### politics-yes-v3
**Status**: VALIDATED — signal real but fill price model matters
**Spawned from**: edge-weighted-skill / scorecard-v3
**Signal**: +43-47pp excess HR over 18.8% base rate (N=3: 62.3% HR, N=5: 65.5% HR)
**At max_price=0.80**: NEGATIVE PnL (breakeven = 80% HR). All fills forced to $0.80.
**At actual trigger prices (no cap)**: N=3 +$25.4K PnL, Sharpe 0.56, 351 fills/8mo.
**Alpha concentrates in longshots**: <0.30 bucket = 14% of fills, 105% of PnL ($26.7K).
High-price entries (>0.50) have negative EV despite high HR.
**Recommended**: N=3 with PriceGatedStrategy max_price=0.50 as filter (not fill price).
**Config**: K=100 BEH-gated pool, combined YES+NO consensus, direction_filter=YES.
**Vectorized misleading**: YES-only counting ≠ combined consensus model.
**Analysis**: `research/hypotheses/scorecard-v3-strategies/validation/politics_yes_v3_results.md`

### microstructure-calibration-upgrade
**Status**: INVESTIGATED (2026-03-09) — NOT worth pursuing as priority
**Original hypothesis**: Fill model responsible for 10-20pp of vec-to-tick gap
**Finding**: Fill model contributes <1pp HR impact and 1-5% PnL impact (MAC calibration)
**Key data**: MAC half-spread = 0.01 median, Roll = 0.19 (17x — captures fundamentals, not friction).
Sports median spread = 0.000. For $10 trades, slippage = $0.00-$0.10.
**Root cause of gap**: Consensus dedup (8-15pp), SELL filtering (5-10pp), capital constraint (3-8pp)
**Recommendation**: Keep SimulatedExecutor for validation. Use RealisticFill+MAC only if PnL precision needed.
**Analysis**: `research/hypotheses/microstructure-calibration/discovery/analysis.md`
**Knowledge**: `execution/spread_microstructure.md`

### portfolio-three-tracks
**Status**: completed — InPlay EXCLUDED, 2-track (Sports YES + Politics NO) recommended
**Correlation**: near-zero (r = -0.06 to +0.09). Excellent diversification.
**Critical finding**: Sports YES and InPlay share 99.5% of markets (same direction).
InPlay adds -$482 incremental PnL on exclusive markets, degrading fast (Feb: -$7K).
InPlay marginal Sharpe = -0.15 (hurts portfolio).
**Recommended config**: $5K budget, Sports S=20 P=20, InPlay disabled.
Combined Sharpe 7.76, max DD $4,339, PnL $175K/10mo.
**Bottleneck**: signal generation (2,369 fills total), not capital.
**Spawned ideas**: InPlay-as-early-trigger, Politics hold-time reduction
**Analysis**: `research/hypotheses/portfolio-three-tracks/discovery/portfolio_analysis.md`

### dual-skill-market-selector
**Status**: REJECTED — dual-skill entry is a popularity proxy, not a quality signal
**Spawned from**: edge-weighted-skill (964 dual-skill traders)
**Sports YES filter**: passes 99.6% of signals (zero discrimination)
**Politics NO filter**: ANTI-PREDICTIVE (-12pp HR vs unfiltered; NOT-entered markets = 97.4% HR)
**Standalone signal**: near base-rate HR (50-52% Sports YES vs 40.7% base; 68-77% Politics NO vs 78.6% base)
**Root cause**: dual-skill = active traders in popular, liquid, contested markets. Popularity ≠ quality.
**Key lesson**: "Market quality via trader entry" is confounded with volume/popularity.
Low-volume, uncontested markets are easier (higher directional HR) but less interesting.
**Analysis**: `research/hypotheses/dual-skill-market-selector/discovery/analysis.md`

### crypto-yes-v2-maxprice065
**Status**: PARKED — will be a dedicated strategy (separate from consensus portfolio)
**Tick-validated**: +37.4pp excess HR, Sharpe 1.44, 122 fills/8mo, $47,932 PnL
**Key finding**: BEH gate OVER-FILTERS Crypto (37 vs 50 traders, Jaccard=0.28). Use v2 pool.
**Config**: K=50 N=2 max_price=0.65 YES-only. 7/8 months profitable. CS=8.97.
**Analysis**: `research/hypotheses/scorecard-v3-strategies/discovery/crypto_maxprice065_rerun.md`

### edge-weighted-skill
**Status**: partially promoted → scorecard-v3
**Core hypothesis**: BEH as primary scoring weight — REJECTED (less stable than composite)
**What worked**: BEH as qualification gate (removes 26% of Crypto pool), NO-direction consensus
(Politics NO +9.3pp tick), direction decomposition (51% NO-skilled vs 12.6% YES-skilled)
**Tick-validated (v3)**: Sports YES +30pp/Sharpe 5.23, Politics NO +9.3pp, InPlay +26.9pp
**Promoted**: Sports YES v3 to paper_dev
**Knowledge**: `signals/edge_weighted_skill.md`, `signals/no_direction_consensus.md`, `methodology/README.md`

### tag-hr-copy
**Status**: rejected (individual signal — consensus gap)
**Tags tested**: Esports, 1H, Tennis (BUY-only, BUY+SELL directional)
**Vectorized (R3 UB)**: Esports HR=67.2%/CS=34.87, 1H HR=78.0%/CS=19.71, Tennis HR=72.4%/CS=9.67
**Tick-by-tick**: Esports HR=45.8%, 1H HR=49.8% (≈base), Tennis HR=40.6% — all negative PnL
**Root cause**: Vectorized measured N-trader consensus; tick strategy fired on individual trades
**Lesson**: See `pitfalls/individual_vs_consensus_signal.md`

### politics-active-exit
**Status**: VALIDATED (2026-03-09) — capital recycling via early profit-taking
**Spawned from**: portfolio-three-tracks
**Signal**: Exit at 50% of max payout instead of holding to resolution.
**At P=20**: $31,368 PnL vs $12,646 hold (+148%), 327 vs 197 fills, med hold 0.9d vs 4.7d.
**Mechanism**: Capital recycling only — unconstrained PnL nearly unchanged ($34,639 vs $33,942).
**Portfolio**: Politics Sharpe 4.28 -> 5.66, MaxDD $1,568 -> $900.
**Capital**: Exit@50% at P=10 ($1K capital) > Hold at P=20 ($2K). Frees $1K for Esports track.
**Monthly**: Exit@50% outperforms in 7/10 months; underperformance trivial ($-14 to $-186).
**Extra fills quality**: 92.3% win rate, $132.4 avg PnL (better than hold-constrained average).
**Hybrid strategies**: Tested price-bucket-dependent exits — uniform Exit@50% wins.
**Slippage**: $29 total (negligible). MAC half-spread 0.001 for politics.
**Analysis**: `research/hypotheses/politics-active-exit/discovery/analysis.md`

### tag-hr-consensus
**Status**: VALIDATED (Esports YES) / REJECTED (Tennis NO) — 2026-03-09
**Spawned from**: tag-hr-copy (consensus fix)
**Fix**: N-trader consensus threshold in tick strategy, matching vectorized counting unit.
**Esports YES K50 N3**: 64.0% HR, +17.7pp market excess, $14.8K PnL, Sharpe 9.65, 297 fills, 3.8h hold. CS=55.
**Esports YES K100 N4**: 64.2% HR, +17.9pp market excess, $6.0K PnL, Sharpe 10.98, 123 fills. CS=57.
**Tennis NO K50 N2**: 56.0% HR, -0.9pp market excess (FAIL). Position-level base (36.5%) made it look good (+19.5pp) but market-level NO base (56.9%) reveals zero edge. PnL from asymmetric payoffs only.
**Vec-to-tick gap**: 3pp (Esports) vs 21-32pp (tag-hr-copy). Consensus alignment dramatically reduces gap.
**Key learning**: Always use MARKET-LEVEL base rate, not position-level, for edge assessment.
**7/8 months profitable** for Esports (weakest: Jan 2026, 51.7% HR, still positive PnL).
**Recommended**: Esports YES K50 N3 for portfolio integration (new tag, near-zero correlation expected).
**Analysis**: `research/hypotheses/tag-hr-consensus/discovery/analysis.md`

## Queued — Spawned from portfolio-three-tracks

### inplay-early-trigger [REJECTED]
**Spawned from**: portfolio-three-tracks
**Tested**: 2026-03-09
**Result**: REJECTED -- structurally invalid. InPlay (N=1) ALWAYS fires before Sports (N=2)
because they use the same pool and 1 < 2. Sports consensus cannot exist at InPlay's signal time.
Using Sports as retroactive filter is look-ahead bias. No real-time observable proxy exists.
Earlier entry INCREASES hold time (+10.9h) and DECREASES PnL (Sports 66x better CS).
**Spawned ideas**: cross-pool-consensus, price-decline-longshot-signal
**Analysis**: `research/hypotheses/inplay-early-trigger/discovery/analysis.md`

### politics-active-exit [VALIDATED]
**Spawned from**: portfolio-three-tracks
**Tested**: 2026-03-09
**Signal**: Exit Politics NO positions at 50% of max payout instead of holding to resolution.
**Finding**: At P=20, Exit@50% yields $31,368 PnL vs $12,646 hold-to-resolution (+148%).
327 fills accepted (vs 197), median hold 0.9d (vs 4.7d), ROC/day 4.3x improvement.
**Mechanism**: Purely capital recycling — unconstrained PnL is nearly identical ($34,639 vs $33,942).
Early exits free slots, allowing 130 more signals to be filled (92.3% win rate, $132.4 avg PnL).
**Portfolio**: Politics Sharpe 4.28 -> 5.66, MaxDD $1,568 -> $900.
**Capital**: Exit@50% at P=10 ($1K) outperforms Hold at P=20 ($2K). Frees $1K for Esports track.
**Risks**: Requires real-time NO price monitoring (CLOB WS). Slippage negligible ($29 total).
**Spawned ideas**: politics-exit-implementation (production CLOB integration)
**Analysis**: `research/hypotheses/politics-active-exit/discovery/analysis.md`

## Queued — Spawned from tag-hr-copy

### esports-price-regime [LOW]
**Spawned from**: tag-hr-copy price analysis
**Summary**: Target 0.60-0.75 fill price bucket in Esports specifically — observed 64% HR (458 fills).
Apply price floor AND ceiling, skip consensus requirement. Simpler signal, fewer parameters.
**Note**: See `signals/price_regime_hr_correlation.md`
**Deprioritized**: Esports consensus (tag-hr-consensus) already validated at 64% HR. Price regime adds complexity for marginal gain.

### esports-sub-tag [COMPLETED -- NO NEW SIGNAL]
**Spawned from**: tag-hr-copy Esports analysis
**Tested**: 2026-03-09
**Result**: Sub-tag decomposition adds NO incremental value. Pool traders are cross-game
generalists (13 traders, all bet 2+ games). Game-specific pools infeasible (only CS2 has
enough qualified traders). LoL shows higher HR (81.2% at N=3) but only 16 signals.
Parameter robustness confirmed: all 25 K x N cells positive excess HR (+9.1 to +38.0pp).
K50 N3 remains optimal. No new ideas spawned.
**Analysis**: `research/hypotheses/esports-sub-tag/discovery/analysis.md`

## Queued — Spawned from tag-hr-consensus

### esports-portfolio-integration [HIGH]
**Spawned from**: tag-hr-consensus (Esports YES validated)
**Summary**: Add Esports YES K50 N3 as third track in portfolio (Sports YES + Politics NO + Esports YES).
Near-zero correlation expected (different tag, different market dynamics, different hold times).
Esports has 3.8h median hold vs Sports 33d and Politics 7.5d — excellent capital recycling.
**Expected impact**: +$14.8K PnL/8mo, Sharpe improvement, genuine 3-track diversification.

### esports-consensus-robustness [COMPLETED -- ROBUST]
**Spawned from**: tag-hr-consensus validation
**Tested**: 2026-03-09 (as part of esports-sub-tag)
**Result**: All 25 K x N cells show positive excess HR. No cliff or fragile peak.
K50 N3 is at the optimal precision/throughput tradeoff. K25 N2 offers 60% more signals
at 3pp less excess HR (low priority to tick-validate). Signal degrades gradually with
larger K (wider pool) and lower N (less consensus). Compounding score peaks at high-N
configs but signal count too low to be practical.
**Analysis**: `research/hypotheses/esports-sub-tag/discovery/analysis.md`

## Queued -- Spawned from inplay-early-trigger

### cross-pool-consensus [REJECTED — 2026-03-09]
**Spawned from**: inplay-early-trigger (rejection finding: same pool = tautological timing)
**Tested**: 2026-03-09 — vectorized discovery sweep, 4 pool construction variants
**Result**: NO-GO. BUY-only mode too thin (3-12 signals/8mo, ~98% throughput collapse vs single-pool).
Directional mode fires at avg price 0.85-0.90 (near-certainty in-play markets), break-even HR = 85-90%.
Cross-pool does NOT solve timing: pools fire simultaneously (med gap 0h). No sequential confirmation.
**Key findings**:
- All pool variants achieve Jaccard=0.000 (true independence achievable)
- Score-axis split (excess_hr pool vs consistency pool): +16pp HR vs random split in directional mode
  — real signal from orthogonal skill dimensions
- BUY-only splits halve per-pool signal rate; cross-pool overlap is geometric (~12 vs 741 signals)
- No improvement over existing v3 Politics YES strategy
**Spawned ideas**: score-axis-pool-construction [MEDIUM], sequential-cross-pool [LOW]
**Analysis**: `research/hypotheses/cross-pool-consensus/discovery/analysis.md`

### score-axis-pool-construction [MEDIUM]
**Spawned from**: cross-pool-consensus discovery
**Summary**: Use score_axis construction (top-K by excess_hr vs top-K by consistency_sharpe, disjoint) as
a quality filter on existing v3 strategies. Instead of cross-pool confirmation, require that a market
has traders from BOTH skill axes as a signal quality gate. Vectorized shows +16pp HR vs single-pool N=2.
Test as a meta-filter on Sports YES — apply after consensus, before fill, to check dual-axis representation.
**Expected impact**: Higher HR with lower volume. May unlock a reliable Sports YES BUY-only signal.
**Risk**: Very thin throughput in BUY-only (historical <15 signals/8mo). May work better in directional mode.

### sequential-cross-pool [LOW]
**Spawned from**: cross-pool-consensus discovery
**Summary**: Enforce strict temporal ordering — Pool A (excess_hr ranked traders) must fire > 2h before
Pool B (consistency ranked traders) for the signal to count. Test: does the subset of markets where
excess_hr traders "lead" the consistency traders by 2-24h have higher HR?
**Basis**: score_axis BUY-only showed Pool A first 100% of time with 2.8h median gap.
**Risk**: Will reduce signal count further (already <15 BUY-only). Low priority.

### price-decline-longshot-signal [LOW]
**Spawned from**: inplay-early-trigger (price movement finding)
**Summary**: On markets where price moves DOWN between 1st and 2nd pool trader entry (30% of cases),
Sports enters at dramatically lower prices yielding $595 avg win vs $80 at earlier entry.
Price decline after first pool trader entry may itself be a bullish long-shot signal.
Hypothesis: if price drops > 30% after InPlay (N=1) entry, enter at the lower price.
**Risk**: Small sample (375 markets), 55.5% HR, may be pure long-shot asymmetry, not skill.

## Parked

### tennis-directional [PARKED]
**Spawned from**: tag-hr-copy R3 Tennis DIR sweep
**Summary**: Tennis DIR showed HR=72.4% in vectorized, but Tennis NO K50 N2 had zero market-level
excess in tick validation (-0.9pp). Tennis YES is also weak. Tennis consensus signals lack genuine
edge despite high absolute HR. Position-level base rate (36.5%) is misleading.
**Revisit when**: Per-game Tennis decomposition shows specific games with genuine excess.

(none otherwise)
