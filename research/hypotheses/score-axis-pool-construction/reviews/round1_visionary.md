# Visionary Review: score-axis-pool-construction (Round 1)

**Reviewer**: Visionary agent
**Date**: 2026-03-11
**Discovery verdict**: MARGINAL — DO NOT tick-validate as-is

---

## Summary of What Failed and Why

The score-axis dual-pool construction produced two compounding failures. BUY-only collapses to
near-zero signals because the AND-gate requires two disjoint pools to independently find the
same market via direct BUY YES — statistically rare in a universe of 275K sports markets with
~8 months of qualifying data. Directional mode generates more signals but fires at avg entry
prices of 0.63-0.69, where any trader achieves 57-67% HR — leaving only +1-8pp true alpha,
below the 20-40pp tick degradation budget.

The deeper problem is Pool B (consistency_sharpe specialists). Pool B traders have excess_hr
only 0.30-0.53 — far below Pool A's 0.64-0.75. They are "stably mediocre" traders who
consistently participate but consistently enter at market-consensus prices. Their agreement
with Pool A's sharp traders is not informative: both groups happen to be in the same
well-priced market at the same time, not independently corroborating a mispricing.

---

## Adjacent Signals

1. **Pool A standalone with a max_price gate of 0.55.** The sports_yes_excess_hr_pool (Pool A,
   top-K by excess_hr, composite-scored) is itself an existing validated signal — it is the
   foundation of Sports YES v3. What score-axis-pool-construction implicitly tested was
   "Pool A PLUS Pool B as a quality filter." The analysis shows Pool B adds nothing when Pool A
   already qualifies traders by excess_hr. Drop Pool B, drop the AND-gate, add max_price=0.50
   to force signals into the 0.30-0.50 price band where population HR is 28.4% and genuine
   10pp+ skill shows measurable edge. This is actually the spawned idea
   `sports-yes-single-pool-price-gated` (analysis.md) — it deserves promotion to a full
   hypothesis rather than remaining an orphaned spawned idea.

2. **Sports NO consensus using Pool A composition.** The no_direction_consensus knowledge entry
   (`signals/no_direction_consensus.md`) confirms that NO specialists are a nearly disjoint
   population from YES specialists (Politics YES/NO Jaccard = 0.031). Sports NO has never been
   tested with a composite-scored pool in the same framework as Sports YES v3. The discovery
   analysis notes Sports NO base rate = 58.8% (vs YES 41.2%), which is structurally better —
   signals fire at lower prices (0.20-0.40) where population HR is 20-40% and +10pp trader
   skill creates visible edge. This is the spawned idea `score-axis-no-direction-sports`; it
   deserves immediate vectorized discovery, not parking.

3. **Axis-weighted single pool.** Instead of two disjoint pools with an AND-gate, compute a
   single composite score with a higher weight on consistency_sharpe (e.g., 0.40) relative to
   the standard 0.25 in composite scorecard v3. This tests whether consistency_sharpe as a
   heavier component of the single pool changes pool membership meaningfully — without the
   volume-destroying AND-gate. Cost: one DuckDB sweep. If pool composition shifts substantially
   (Jaccard < 0.70 with v3 pool), this is a new signal dimension. If not, confirms v3 is
   already near-optimal.

4. **Sequential Pool A lead time as entry filter.** The data shows pct_a_first = 0.51-0.75 in
   directional mode (Pool A fires before Pool B in 51-75% of markets). This means excess_hr
   traders genuinely enter markets earlier than consistency_sharpe traders in the majority of
   cases. This temporal ordering is itself a signal: if Pool A trades hour H and Pool B has
   NOT yet entered, the market is still in its "discovery" phase, where genuine alpha exists.
   Entry at Pool A's time (before Pool B confirmation) at lower prices may be more profitable
   than waiting for both. Test: for markets where Pool A fires at avg_price < 0.55, enter at
   Pool A's trigger time without waiting for Pool B. This directly targets the price-ceiling
   problem identified by the researcher.

---

## Parameter Variations

1. **Sports YES, K=50, max_price=0.50, Pool A only (no Pool B AND-gate), directional.** This
   eliminates the fragility problem entirely (no AND-gate to collapse) and addresses the
   price-level-adjusted excess problem (signals forced below 0.50 where population HR = 28%,
   creating room for genuine +15-20pp trader skill to register). Start the DuckDB sweep from
   this configuration. Expected signal count: moderate (Pool A K=50 fires on its own, not
   jointly, which should give 10-30x more signals than joint firing). This is the highest
   priority parameter variation.

2. **Sports, K=25 (tighter pool), directional, max_price=0.60.** K=25 currently yields zero
   signals because the AND-gate plus hold filter is too restrictive. With a single pool and
   max_price gate, K=25 is the configuration where Pool A avg excess_hr = 0.747 — extremely
   high quality traders. Tighter pool + price gate may yield 10-30 signals/8mo at genuinely
   high precision. This is worth sweeping as the "quality over volume" variant.

3. **Sports NO, K=50, Pool A (top-K excess_hr for NO direction), N=1 (single pool), directional.**
   The edge_weighted_skill knowledge confirms Sports NO avg BEH = 0.44, only slightly below
   Sports YES avg BEH = 0.55. A single NO-direction pool with K=50 and no price cap should
   generate substantial signals at prices 0.20-0.45, where genuine NO trader skill is
   unambiguous. The Politics NO v3 result (+9.3pp tick validated) shows NO signals work when
   properly constructed. Sports NO has never been tested at this level of rigor.

4. **Vary the consistency_sharpe Pool B K independently.** All tested combos use equal K for
   both pools. Test asymmetric K: Pool A K=50 (top excess_hr), Pool B K=200 (broader
   consistency pool). With a larger Pool B, the AND-gate fires more often. This may rescue
   the BUY-only throughput problem at the cost of lower Pool B quality. The goal is to find
   whether the AND-gate has any positive signal content at all, independent of pool size
   constraints.

---

## Cross-Hypothesis Connections

- **Sports YES v3 (composite_scorecard.md, signals)**: This hypothesis is structurally subordinate
  to Sports YES v3. Pool A is essentially the v3 YES pool. The AND-gate with Pool B is a proposed
  quality improvement over v3's existing N=2 consensus gate. The finding that Pool B adds nothing
  means the existing v3 consensus at N=2 already captures the dual-axis quality signal implicitly
  (by requiring two traders from the same composite-scored pool, which blends excess_hr and
  consistency_sharpe). There is no free lunch here — v3 already does this.

- **tag-hr-consensus (cross-pollination opportunity)**: The tag-hr-consensus hypothesis discovered
  that pool explosion destroys signal quality when the pool grows above 50-60 traders. Score-axis
  construction is an attempt to solve the same problem via a different mechanism (AND-gate between
  two disjoint pools vs dynamic pool cap). Tag-hr-consensus's recommended fix — hard pool cap at
  50-60 traders by composite score — is more elegant than the AND-gate and should be applied to
  Sports YES v3 before any dual-pool construction is attempted.

- **no_direction_consensus (Politics NO)**: The single clearest opportunity from this hypothesis
  is the Sports NO signal. Politics NO +9.3pp tick-validated proves NO-direction pools work.
  Sports NO has higher volume, shorter hold times (~5-10h vs 54d for Politics NO), and similar
  pool characteristics. Sports NO should be the top-priority spinoff from this work. The
  `score-axis-no-direction-sports` spawned idea should be renamed
  `sports-no-consensus` and given HIGH priority (not MEDIUM).

- **in_play_elite_traders (signals)**: The analysis shows Pool A traders fire before Pool B in
  51-75% of markets (avg gap 1-2h in BUY-only, larger in directional). This is consistent with
  the in-play elite traders finding that the top excess_hr traders lead the broader market by
  ~58 minutes. Pool A may be partially overlapping with the in-play elite pool. Checking Pool A
  membership against the in-play elite top-100 would reveal whether Pool A's early-entry tendency
  is driven by in-play traders contaminating the score-axis signal.

- **price_level_base_rates (data, CRITICAL)**: The fundamental diagnosis is already captured in
  the knowledge base: 0.50-0.70 entry → -7.9pp structural headwind; 0.30-0.50 → -11.7pp. The
  score-axis signal fires in the 0.63-0.69 band, which already has a structural headwind.
  Any positive excess HR over the price-level base rate at this band is genuine, but it must
  overwhelm both the structural headwind AND be > 20pp to survive tick degradation. The
  hypothesis needs to target the 0.30-0.50 band where the -11.7pp structural headwind is
  known and quantified — a strategy that generates +20pp excess over the structural base
  rate there would have genuine survivable alpha.

---

## Compounding Improvements

- **The max_price gate is the single lever that could save this hypothesis.** All 65 signals in
  the K=50 N=1x1 directional best combo fire at avg price 0.674. Applying max_price=0.50 would
  eliminate most or all of them — but the survivors (if any) would have price-level-adjusted
  alpha of +20pp or more since population HR at 0.45 entry is only ~28%. Estimate: 5-15 signals
  in 8 months survive the price gate. At 70%+ HR and $0.50 avg entry, each winner returns $0.50+
  on the YES token. Capital recycling at 5-10h hold and 5-15 signals/month would yield a
  compounding score of 5-15 — potentially competitive with the threshold of 5.0. This is worth
  one DuckDB run of < 5 minutes.

- **Abandon the AND-gate; use Pool A as a quality pre-filter on existing v3 signals.** Rather
  than requiring Pool A AND Pool B to both be present, use Pool A membership as a BOOST signal:
  when the v3 Sports YES consensus fires (N=2), check whether at least one of the two triggering
  traders is in Pool A (top-50 excess_hr). This increases signal quality without collapsing
  volume. The AND-gate doubles the volume collapse; a "at least one Pool A member" requirement
  is much softer and may filter the worst v3 signals (the 44.4% HR month in August 2025).

- **Short hold time is already excellent.** Med hold = 5.2h is very good for capital recycling.
  The problem is not hold time — it is signal quality and price level. Fixing the price level
  problem (max_price=0.55) while keeping the same 5.2h hold would yield a compounding score
  competitive with v3 (which runs at 6.9h med hold with +30pp excess at tick level).

---

## New Hypothesis Ideas
For `research/ideas.md` backlog:

1. **sports-no-consensus**: Apply composite-scored Sports NO pool (top-50 by excess_hr,
   BEH-gated, N=2 consensus) to Sports NO direction. Fires at prices 0.20-0.45 where
   population HR = 3-28%, creating large genuine-excess room. Expected 5-15h hold.
   High compounding score potential. Directly analogous to Politics NO v3 (+9.3pp tick),
   but with Sports dynamics (shorter hold, higher volume). Priority: HIGH.

2. **sports-yes-price-gated-single-pool**: Replace dual-pool AND-gate with single Pool A
   (composite top-50) plus strict max_price=0.50 gate. Forces signals into the 0.30-0.50
   band where population HR = 28%, creating room for +20pp genuine alpha. Estimate 5-20
   signals/month at high precision. Direct simplification of this failed hypothesis.
   Priority: HIGH.

3. **pool-a-as-v3-boost-filter**: Apply Pool A membership check (at least one triggering
   trader must be in top-50 excess_hr) as a post-filter on existing Sports YES v3 signals.
   Would reduce volume by ~30-50% but may eliminate the low-HR months (e.g., Aug 2025).
   Test as meta-filter: compute HR of v3 signals where Pool A member triggered vs not.
   Cost: single DuckDB query. Priority: MEDIUM.

4. **consistency-sharpe-standalone-sports**: Build a Pool B standalone (top-50
   consistency_sharpe traders, NOT in Pool A) and test it as a single-pool consensus signal
   for Sports YES. Pool B has excess_hr 0.30-0.53 and consistency_sharpe 7-13. If Pool B
   alone generates positive price-level-adjusted alpha at N=2, it is a genuinely separate
   signal from v3 (different trader composition, Jaccard=0 with Pool A by construction).
   Current analysis only tested it as an AND-gate partner, never standalone. Priority: LOW.

5. **sequential-pool-a-entry**: For markets where Pool A fires at price < 0.55 AND Pool B
   has not yet entered, enter at Pool A's trigger time WITHOUT waiting for Pool B. This
   targets the core insight that excess_hr traders enter earlier (pct_a_first = 51-75%)
   and at lower prices than the dual-pool AND-gate requires. Pool B confirmation would then
   serve as a stop-loss signal — if Pool B never enters, exit position early. Priority: LOW.

---

## Summary

This hypothesis ran into two structural walls: the AND-gate collapses BUY-only throughput
to noise levels, and directional mode fires too late in the market lifecycle (avg 0.67 entry)
to capture genuine alpha above the price-level base rate. The core concept — that orthogonal
skill axes (excess_hr vs consistency_sharpe) select genuinely different trader phenotypes
— is confirmed valid (Spearman=0.46, Jaccard=0.0), but Pool B (consistency_sharpe) traders
turn out to be "stably mediocre" participants who enter at consensus prices, not genuine
contrarian validators. The most productive next directions are: (1) `sports-no-consensus`
using a single composite-scored Sports NO pool at low prices (HIGH priority, directly
analogous to the validated Politics NO v3 strategy), and (2) `sports-yes-price-gated-single-pool`
testing Pool A alone with max_price=0.50 to force signals into the 0.30-0.50 price band
where genuine excess HR is measurable and survives tick degradation. Both can be tested
with a single DuckDB sweep session. The AND-gate dual-pool construction as originally
specified should not proceed to tick validation without first demonstrating any signals
survive max_price=0.50.
