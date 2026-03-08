# Visionary Review: tag-hr-consensus (Round 2)

**Reviewer**: Visionary
**Date**: 2026-03-06
**Status of hypothesis**: MARGINAL — tick-by-tick complete. Signal is real; PnL is broken by
fill price and pool explosion. Review covers: what to iterate vs pivot, which graduated sizing
track matters most, the pool explosion fix, category expansion timing, and new hypothesis ideas
from the failure mode.

---

## Answering the Stated Questions

### Q1: Iterate or pivot?

Iterate. The case for iteration is clean:

- Esports 2025-07 fold: Sharpe=3.27, PnL=+$616, HR=51.9% at fill=0.443. That is not noise.
- Esports-sensitive 2025-07: Sharpe=6.69, PnL=+$1,060 on 46 signals with a 28-person pool.
- Tennis 2026-01: HR=53.2%, PnL=+$2,496, Sharpe=3.65. Independently positive.

These are not lucky folds. A signal that produces Sharpe >3 in a fold is real. The problem is
structural regime mismatch (pool explosion) and fill price erosion — both are fixable without
changing the core signal. A pivot would discard confirmed alpha. The three failure modes
(2025-10 Esports base rate spike, 2026-01 pool explosion, fill price ~0.50 break-even) are
diagnosable and have specific remedies, not diffuse signal absence.

The condition for abandoning: if, after a hard pool size cap (<=50 traders) and a price floor
at 0.40, the Esports 2026-01 fold still produces negative PnL. That would mean the market has
structurally matured past the signal's viable window.

### Q2: Which graduated sizing track flips MARGINAL to exploitable?

Track 4 (signal-time volume) first, but not as a sizing track — as a hard gate.

Here is why: the 2025-10 Tennis disaster (180 signals, HR=38.3%, loss of $4,296) is the dominant
drag on aggregate PnL. That fold has n_markets=928 and pool=131. No amount of position sizing
fixes 180 signals at near-random HR — you need to not fire those signals. Signal-time volume is
the filter that can do this: if 3 qualified traders enter a Tennis market but their combined
position is $80, they are noise. If their combined position is $800, they are informed.

Track 2 (trader quality score) is second priority. The 2026-01 Esports fold has 774 qualified
traders. N=4 of 774 is statistically indistinguishable from chance. But if those 4 traders have
individual excess HR of 30pp each (not just the 10pp entry bar), the consensus is genuinely elite.
A quality-weighted gate (only fire if mean(trader_excess_hr) >= 20pp) would compress the 774-pool
to its best performers without needing an absolute size cap.

Track 3 (contradictory signals) is third. The 2025-10 fold may have had qualified NO traders
entering alongside YES traders — if the dissent ratio was <0.7 in those failing markets, this
filter alone could suppress most of the disaster fold.

Track 1 (time-to-live sizing) is last. Hold times are already short (Esports median 7h, Tennis
median 5-13h). Capital recycling is not the binding constraint. Fix the signal first.

### Q3: Elegance of pool explosion fix

The most elegant fix is not a hard cap — it is a **dynamic excess HR floor**.

A static `max_pool_size=50` is brittle: it discards the entire pool if it grows to 51 traders,
regardless of why. The underlying problem is that as the Esports market grew, new traders with
marginal excess HR (just above the 10pp threshold) joined the pool and diluted it.

The fix: **raise the pool entry bar proportionally to pool size**. Specifically:

```
effective_min_excess_hr = base_threshold + k * max(0, pool_size - pool_target)
```

Where `base_threshold = 10pp`, `pool_target = 40`, `k = 0.5pp per trader above target`.

At pool_size=40: threshold stays at 10pp.
At pool_size=60: threshold rises to 20pp (60-40=20, k*20=10pp additional).
At pool_size=100: threshold rises to 40pp — only genuinely elite traders qualify.
At pool_size=774: threshold would be ~397pp — physically impossible, pool contracts to elite tier.

This is self-regulating: as the market grows, the bar rises, contracting the pool back to the
most informed traders. No arbitrary cutoff, no fold-specific tuning. The parameter `k` can be
swept (0.3 to 1.0) in a single DuckDB pass.

Secondary constraint: cap `max_pool_size` at 60 absolute as a safety floor.

### Q4: Category expansion — now or wait?

Wait, but do one specific thing first: check whether NBA passes the basic signal screen.

The reason to wait on broad expansion: Esports and Tennis are not yet solved. Deploying
resources to new categories before fixing the structural failure (pool explosion, fill price)
would split attention without producing a deployable strategy. The 2025-10 disaster fold
alone represents $4,296 in simulated losses — that must be addressed before claiming the
signal is viable.

The reason to check NBA now: from `data/tag_base_rates.md`, NBA has 38K markets and 46.3%
YES base rate — nearly balanced. It has enough volume to build qualified pools and enough
signal density to measure excess HR. If the pool explosion problem is less severe in NBA
(fewer whale traders), a single vectorized pass could reveal whether the category is
promising before committing to tick-by-tick work. This is cheap (one DuckDB query) and
informs prioritization for the next hypothesis cycle.

Do not touch: Earnings (72.9% YES base rate — a trivial YES strategy would appear skilled),
Elections (9% YES base rate — qualified pool contamination risk), any NO-biased tags.

### Q5: New hypotheses from the failure mode

Three hypotheses emerge from this specific failure anatomy:

**1. Regime-gated consensus**: The 2025-10 Esports fold had a 65.4% YES base rate — a
non-stationary spike. The fold was a trap: any YES strategy would show inflated absolute HR
while excess HR collapsed. A regime gate that measures the rolling 30-day base rate and
suspends the strategy when it deviates >15pp from the 6-month average would have entirely
avoided this fold. This is not a separate strategy — it is a deployment gate. But it spawns
a hypothesis: **can per-tag base rate regime changes be predicted from market creation
patterns?** If the number of new Esports markets per week spikes before a base rate shift,
that's an early warning signal.

**2. Fill-price-first entry**: Tennis 2025-07 showed 28pp excess HR but negative PnL
because avg fill = 0.517. At N=3 consensus, the entry fires when the 3rd qualified trader
enters — but that trader might enter at 0.70. The strategy should check the current market
price BEFORE executing and skip if price > 0.40. This is distinct from `price_ceil` (which
filters the qualified pool) — it is a real-time entry gate at execution time. The new
hypothesis: **consensus fire + real-time price gate at 0.40**. Expected effect: 30-40% of
signals are skipped, but PnL per signal improves dramatically.

**3. First-mover premium**: The 2025-07 Esports fold had only 47 qualified traders but
produced the best results. The 2026-01 fold had 774. But within the 2026-01 fold, the
first traders to enter a given market are more informed than the ones who enter later. A
consensus signal that fires ONLY when the Nth qualifying trader is also the Nth BUY in the
entire market (not just among qualified traders) would identify markets where the qualified
traders arrived before the crowd. This is the **first-mover consensus** hypothesis: signal
fires at consensus, conditional on qualified traders being disproportionately early in the
market's trade sequence.

---

## Adjacent Signals

### 1. Regime-Conditioned Entry Gate

The 2025-10 Esports fold (base rate = 65.4%) is a known trap and produced -$197 loss. The
key observation is that this fold's base rate was 20pp above the adjacent folds. A real-time
regime filter — suspend YES consensus trading when rolling 30-day tag base rate exceeds
fold-average base rate + 12pp — would have skipped the entire 2025-10 fold.

This is testable in one DuckDB query: for each signal, compute the rolling 30-day YES win
rate in the same tag, then filter signals to those where rolling_base < global_base + 12pp.
Expected cost: ~15% of signals. Expected benefit: entire disaster folds avoided.

### 2. First-Mover Rank Filter

At the moment the Nth qualified trader enters, compute that trader's BUY rank in the market
(i.e., how many total traders entered YES before them). If rank is 1-10, the qualified traders
are genuinely early-information. If rank is 50+, they are following the crowd and the
market is already efficiently priced.

Signal sketch: fire entry only when `qualified_trader_rank_in_market <= 15`. This would
strongly filter the 2026-01 pool explosion because later-entering "qualified" traders (those
who entered in a large, mature pool) would be ranked well into the hundreds and skipped.

### 3. Exit Signal: Consensus Reversal

The strategy currently holds to settlement. But if a qualified trader SELLS their YES position
before settlement, that is a reversal signal — they exited early, suggesting their information
updated. An early exit trigger: if any qualified trader sells their position in a market where
we hold a consensus entry, evaluate closing early.

This is particularly relevant for Tennis (avg hold 13h in 2025-07 fold) where the PnL was
negative despite high HR. Some of those losses may have been observable earlier via qualified
trader exits.

### 4. Causal Volume as Standalone Signal (Cross-Reference from R1)

This was identified in Round 1 but becomes more urgent after tick validation. The volume
correlation with HR was confirmed as look-ahead in the discovery notes. The question of
whether signal-time volume (N traders' combined position sizes) predicts HR is now the
critical causal test. If signal-time vol predicts HR at even +15pp, it becomes a hard gate
rather than an optional filter.

Concrete test: DuckDB query computing `sum(abs(net_usd)) for qualified traders 1..N` at
signal time, then bucketing by volume tier and comparing HR. Expected to reveal whether
the 2025-10 Tennis disaster would have been filtered by a signal-vol threshold.

---

## Parameter Variations

### 1. Dynamic Pool Threshold (The Structural Fix)

As described in Q3 above: replace static `min_excess_hr_pp` with a dynamic formula that
scales with pool size. DuckDB sweep parameters:

```
k_values = {0.3, 0.5, 0.7, 1.0}
pool_target = {30, 40, 50}
effective_min_excess_hr = base + k * max(0, pool_size - pool_target)
```

This replaces the `max_pool_entry_price` parameter as the primary quality gate. Run as a
vectorized sweep — should complete in <5 minutes.

### 2. Price Floor at Entry Time (0.35–0.45 range)

Current test used `price_ceil=0.75`. The fill price analysis shows entries at 0.517 barely
break even at 52% HR. Add a `min_fill_price` threshold is wrong — the issue is the CEILING
is too high. Lower `price_ceil` from 0.75 to 0.45.

At fill price 0.45: break-even HR = 45%. The confirmed excess HR of +11.6pp for Tennis is
sufficient to clear 45% break-even consistently. This single change would have fixed the
Tennis 2025-07 fold (avg fill 0.517 → filtered down to the subset with fill < 0.45).

Test: `price_ceil = {0.45, 0.50, 0.55, 0.60, 0.75}` — compare HR and PnL across all folds.

### 3. Pool Qualification Window: 60 Days vs 90 Days vs 180 Days

Current: 180-day training window. The Esports base rate non-stationarity (10% → 65% within
one 180-day period) means a trader qualified on a 10% base rate environment looks
exceptional but may be mediocre in a 65% environment. A 60-day rolling window would
re-qualify pools monthly, adapting to regime shifts.

Trade-off: shorter windows mean less data per trader (noisier HR estimates). The minimum
viable sample size per trader is ~10 markets. Test: can a 60-day window find enough traders
with >=10 qualifying markets in the Esports tag?

DuckDB check: `count(distinct condition_id) FROM maker_positions WHERE resolved_at IN last 60 days AND tag='Esports'`. If >200 markets per window, the 60-day window is viable.

### 4. Consensus N as a Function of Pool Size

Instead of fixed N=3 or N=4, compute N as a fraction of pool size. Specifically:

```
n_required = max(3, ceil(pool_size * 0.08))
```

At pool_size=47: n_required = max(3, 4) = 4 — matches the best Esports 2025-07 result.
At pool_size=774: n_required = max(3, 62) = 62 — effectively suppresses signals in large pools.

This self-regulates the consensus requirement proportional to pool dilution, without an
arbitrary hard cap. The constant (0.08 = 8%) is sweepable: {0.05, 0.08, 0.10, 0.12}.

---

## Cross-Hypothesis Connections

### Connection to tag-hr-copy (REJECTED)

The pool explosion in 2026-01 (774 Esports traders) recreates the structural failure of
tag-hr-copy at a different level. tag-hr-copy failed because individual trades ≠ consensus.
The 2026-01 fold fails because N=4 of 774 ≠ informed consensus. The root cause is identical:
the counting unit has become too coarse relative to the population.

The fix (dynamic threshold or proportional N) is a generalization of the lesson from
tag-hr-copy: the signal unit must remain a meaningful fraction of the signal population.

Reference: `/mnt/nvme/git/polymarket/polymarket/research/hypotheses/tag-hr-copy/README.md`

### Connection to esports-sub-tag (queued in ideas.md)

The 2025-10 base rate spike (65.4%) remains unexplained. The most likely explanation is
tournament composition: a major tournament in a specific game (e.g., a Dota2 world
championship) may have produced a run of YES outcomes, spiking the tag-level base rate.

If the 2025-10 spike is game-specific, then a per-game qualified pool would naturally
isolate it: the spike would appear in one game's fold but not contaminate the others.
This cross-hypothesis connection is now actionable — the data to test it exists in the
Parquet snapshot. A DuckDB query joining Esports markets to their sub-tags (if available
in the Gamma event metadata) would reveal whether the base rate spike is compositional.

Reference: `/mnt/nvme/git/polymarket/polymarket/research/ideas.md` (esports-sub-tag entry)

### Connection to period_base_rate_variance.md

The knowledge base documents global monthly base rate swings of up to 15pp. The Esports
2025-10 fold shows a 20pp within-tag spike. This is larger than the global variance — it is
not explainable by global seasonality. The within-tag base rate variance for Esports is a
distinct, undocumented phenomenon. It should be added to the knowledge base as a specific
finding: Esports tag base rate has documented spikes of 20pp+ correlated with tournament
calendars. This is critical context for any future Esports signal.

Reference: `/mnt/nvme/git/polymarket/polymarket/research/knowledge/data/period_base_rate_variance.md`

### Connection to hold_time_capital.md

The knowledge base confirms Esports has 0.3d median hold — 10x faster than the next
category. The tick validation shows avg_hold_hours of 3.66-7.36 hours, consistent. This
is a genuine advantage: at 50 concurrent positions, Esports-only strategy has throughput
of ~167 positions/day. The PnL drag is not from capital lock-up — it is purely from entry
price and signal quality. This isolates the problem and confirms iteration is worthwhile.

Reference: `/mnt/nvme/git/polymarket/polymarket/research/knowledge/execution/hold_time_capital.md`

### Connection to vectorized_tick_gap_anatomy.md (Step 3 Warning)

The knowledge base warns that consensus 5+ is often anti-predictive in popular markets.
The tick results for Esports 2026-01 (N=4 of 774 traders, HR=46%) are consistent with
this warning — a 4-person consensus is not a 4-person consensus when the pool is 774.
The effective consensus quality is denominator-dependent, not absolute.

This reinforces the dynamic N approach: the consensus threshold in the gap anatomy document
(Step 3) applies to the population, not the absolute count.

Reference: `/mnt/nvme/git/polymarket/polymarket/research/knowledge/pitfalls/vectorized_tick_gap_anatomy.md`

---

## Compounding Improvements

### 1. Signal-Time Volume Gate (Track 4 as Gate, Not Sizing)

From `plans/graduated_sizing.md` Track 4: compute the combined net_usd of the N qualifying
traders at signal time. Use this as a hard gate: skip entry if signal_time_vol < $150.

At $150 threshold: the 2025-10 Tennis disaster markets (where qualified traders entered
tiny positions, suggesting low conviction) would be suppressed. The 2025-07 profitable
Esports markets (large positions, genuine conviction) would survive.

This is the fastest capital efficiency improvement because it directly suppresses the
disaster fold without touching pool construction.

### 2. Two-Stage Entry: Wait for Price Confirmation

After the Nth consensus trigger, wait up to 30 minutes for the market price to confirm
direction. Specifically: only execute if the price at T+30min is within 3pp of the entry
signal price (not already moved to 0.70+). Markets that have already run to 0.70+ by the
time we'd execute are late entries — the signal already played out.

This is a real-time entry filter implementable at zero additional data cost (uses the
existing orderbook/trade stream). Expected effect: 20-25% of signals are late-entry
misses, replaced by higher-quality early entries.

### 3. Rolling Portfolio: Esports + Tennis Asynchronous

Tennis and Esports have different peak signal hours. Tennis tournaments run during European
daytime (10:00-22:00 UTC). Esports events cluster in Asian and European evenings. The
signals are likely asynchronous — running both in the same capital pool reduces idle time
while maintaining category-specific pools.

At 50 slots: avg 7h hold for Esports, avg 5-13h hold for Tennis → combined throughput of
~110-120 positions/day. Both tags can be active simultaneously without competing for the
same market (different underlying events), so correlation is low. This reduces variance
of a single-tag deployment at no cost to signal quality.

---

## New Hypothesis Ideas

For `research/ideas.md` backlog:

1. **consensus-regime-gate**: Suspend YES consensus signals when the rolling 30-day tag
   base rate deviates more than 12pp above the 6-month average. Directly addresses the
   2025-10 disaster fold pattern. Implementable as a pre-signal check. Priority: HIGH —
   test in one DuckDB pass before next tick validation.

2. **first-mover-consensus**: Fire consensus only when the Nth qualified trader is also
   among the first 15 total buyers in the market (not just among qualified traders). Filters
   the 2026-01 pool explosion by identifying markets where qualified traders arrived before
   the crowd. Priority: HIGH — extends current framework with one additional join.

3. **dynamic-n-pool**: Replace fixed N with `max(3, ceil(pool_size * 0.08))`. As pool grows,
   N grows proportionally, maintaining consensus as a meaningful fraction of the pool.
   Self-regulating parameter that replaces ad-hoc pool size caps. Priority: HIGH — trivial
   DuckDB sweep extension.

4. **esports-game-decomposition**: Decompose Esports by game (CS2, Dota2, LoL, Valorant)
   using Gamma event metadata. Build per-game qualified pools. Tests whether the 2025-10
   base rate spike is game-specific (tournament composition effect). Priority: HIGH — data
   likely available in event name parsing or existing sub-tags.

5. **price-floor-0.40**: Lower price_ceil from 0.75 to 0.40-0.45. At fill price 0.40,
   break-even HR drops to 40%, well within the demonstrated excess. Single parameter change
   that would have fixed Tennis 2025-07 negative PnL. Priority: HIGH — cheapest fix.

6. **nba-consensus-screen**: Apply the consensus framework to NBA (38K markets, 46.3% YES
   base rate). Single vectorized pass to check if excess HR exists before committing to
   tick-by-tick work. Priority: MEDIUM — category expansion should follow Esports/Tennis
   stabilization but a cheap screen is low-cost insurance.

---

## Summary

The MARGINAL verdict is not a signal rejection — it is a parameter indictment. The signal
exists in 2025-07 (Esports Sharpe=6.69) and 2026-01 (Tennis Sharpe=3.65), and its failure
in 2025-10 is traceable to a specific, documented failure mode: a non-stationary base rate
spike that a regime gate would have avoided entirely.

The single highest-leverage next action is the combination of two cheap changes: lower
`price_ceil` from 0.75 to 0.45, and add the regime-gate (suspend when rolling 30-day base
deviates > 12pp). These two modifications are testable in one DuckDB sweep pass in under
5 minutes and would directly address the two root causes of the 2025-10 disaster fold without
changing pool construction at all. If that sweep shows positive PnL across all three folds,
the hypothesis graduates from MARGINAL to PROMISING and tick-by-tick re-validation is
warranted.

If the sweep still fails 2025-10 after these gates, then implement the dynamic pool threshold
(dynamic N or k-scaled excess HR floor) as the structural fix. Only after pool construction is
stable should graduated sizing (Tracks 1-3) be introduced — sizing on a broken signal amplifies
losses, not gains.

Category expansion (NBA) should be a one-query screen run in parallel, not a blocking
dependency. It costs nothing and sets up the next phase while Esports/Tennis iterate.
