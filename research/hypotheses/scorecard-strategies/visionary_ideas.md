# Visionary Review: Scorecard Strategies (Round 1)

**Reviewer**: Visionary
**Date**: 2026-03-07
**Status**: Written pre-tick-validation — based on vectorized findings and full knowledge base.
Update required after `tick_validation_results.md` lands (task #2).

---

## Context and What This Review Covers

Three strategies emerged from the scorecard research:

| # | Strategy | Best Config | Excess HR (UB) | CS (UB) |
|---|----------|-------------|----------------|---------|
| 1 | Smart Money Pool | Esports K=50, N=3, conf>=0.80 | +44pp | 628 |
| 2 | Smart Money Pool | Crypto N=5, conf>=0.90 | +39pp | 187 |
| 3 | Tag-Expert Consensus | Politics NO, K=50, N=3, >=4h | +20pp | ~40 |

This review looks across all three for connections, adjacent signals, parameter variations,
and new ideas that the per-strategy researchers may not have surfaced. It also draws on the
trader-scorecard synthesis, two rounds of tag-hr-consensus reviews, and the full knowledge base.

---

## Adjacent Signals

### 1. Score-Weighted Consensus Vote (from vol-weighted finding)

The vol-weighted direction finding (+5-16pp over head-count) generalizes to a broader principle:
traders who commit more capital carry more information. The natural extension is to weight
each trader's directional vote not by their trade volume but by their **composite scorecard
score** — so that a trader with excess_hr=+40pp and stability=8.0 carries 4x the vote of
one with excess_hr=+10pp and stability=2.0.

Formally: `score_weighted_direction = sum(composite_score_i * sign_i)` where `sign_i = +1 for YES, -1 for NO`.

This combines the vol-weighted insight with the scorecard ranking — the two strongest findings
in the research. Neither has been tested in combination. The score-weighted signal could
widen the gap beyond +14.8pp (the current Elections maximum) for high-quality pools.

Concrete test: replace `vol_weighted_direction` in the strategy2 SQL with
`sum(composite_score * CASE WHEN position='YES' THEN 1 ELSE -1 END)`. Run DuckDB sweep.
Expected: 2-8pp improvement over vol-weighted in Elections and Crypto where trader quality
variance is highest.

### 2. Elite-Selection + Smart-Pool Entry (Strategy 3 + Strategy 2 hybrid)

Strategy 3 found that elite participation identifies markets with +22.7pp YES win rate lift
(47.7% vs 25.0% baseline). Strategy 2 found that vol-weighted smart pool consensus gives
signal direction. These two findings were tested independently.

The hybrid: use **elite presence as the market gate** (any of the 517 elite traders entered),
then **smart pool direction** as the entry signal (vol-weighted consensus from the full
qualified pool). This separates the two functions cleanly:
- Elite = quality filter on WHICH markets to touch
- Smart pool = quality signal on WHICH DIRECTION to trade

This is more powerful than either individually. Elite-gated markets already have +22.7pp
YES lift. Smart pool direction within those markets should add another 10-20pp.

Testable in one DuckDB pass: filter markets to those with any elite participation, then
apply the strategy2 smart pool signal to determine direction. Compare HR to strategy2 alone.

### 3. Temporal Entry Stacking: Three Independent Signals

The research identified entry timing as an unresolved dimension. Consider that within a
single market, there may be THREE distinct observable entry events:

1. **First qualified entry** — a single high-score trader enters. Early signal, noisy.
2. **Consensus trigger** — Nth qualified trader enters. Mid-signal, validated.
3. **Elite confirmation** — an elite pool trader enters after consensus. Late signal, very high confidence.

These are ordered in time within the same market. The hypothesis: markets that complete all
three stages (first entry → consensus → elite confirmation) have dramatically higher HR than
those that only reach stage 2.

Test: for each market that had consensus (strategy2 signal), check if any elite trader entered
AFTER the consensus trigger. Compare HR: consensus-only vs consensus+elite-confirmation.
If the "triple-confirmation" bucket has 10pp+ higher HR, it becomes a standalone signal.

### 4. Consensus Divergence as Anti-Signal (NO trade)

All three strategies focus on the majority direction. The research noted that qualified traders
who split (some YES, some NO) may carry a contrarian signal. Specifically:

When the vol-weighted direction shows a STRONG lean (e.g., 80%+ of vol is YES) but the
head-count direction shows a WEAK lean (e.g., 55% YES), the vol-head disagreement identifies
markets where a few large traders strongly disagree with many small traders.

In Elections, vol-weighted beats head-count by 14.8pp — meaning the small-count-many-traders
direction is often wrong by a large margin. This creates a NO side trade: when head-count
says YES but vol-weighted says NO (or vice versa), the minority-vol side may be exploitable.

Test: extract markets where `vol_dir != head_count_dir` (the 19-20% of markets that disagree).
The vol side wins 67-89% of the time — so the head-count side is losing 67-89% of the time.
This is a direct NO trade signal. Signal count: 19% of all qualified markets × each tag volume.

### 5. Base Rate Regime as a Cross-Strategy Gate

The knowledge base documents period-specific base rate variance (20.4% to 36.3% YES monthly).
The round2 visionary review for tag-hr-consensus identified that a 2025-10 Esports fold with
65.4% YES base rate caused the strategy to fail — any YES strategy would have looked falsely
good while excess HR collapsed.

This is a cross-cutting concern that applies to ALL three scorecard strategies. A shared
**regime gate** — computed once per tag per day, applied to all strategies simultaneously —
would suppress false signals during anomalous base rate periods. The gate logic:

```
rolling_30d_base_rate = YES wins in tag over last 30 days
historical_avg = 6-month average for same tag
suspend_if: |rolling_30d_base_rate - historical_avg| > 12pp
```

This is not a strategy modification — it is a deployment pre-check that runs before any
signal fires. Implementable as a shared module. Estimated cost: suppresses ~10-15% of signals.
Expected benefit: avoids entire disaster folds, as documented in the tag-hr-consensus failure.

---

## Parameter Variations

### 1. Cross-Tag Confidence Weighting

Strategy 2 currently uses a single confidence threshold (e.g., conf>=0.80) across all tags.
But the per-tag IC data from the scorecard synthesis shows:

| Tag | HR Persistence IC |
|-----|------------------|
| Elections | 0.874 |
| Crypto | 0.869 |
| Sports | 0.675 |
| Esports | — (not measured) |

High-IC tags (Elections, Crypto) have more persistent, reliable qualified pools. The confidence
threshold should be **lower** for high-IC tags (fewer signals needed to confirm direction)
and **higher** for low-IC tags (need stronger consensus to trust noisy signal).

Proposed tag-specific thresholds:
- Elections, Crypto: conf >= 0.70 (IC is high, pool is reliable)
- Sports, Esports: conf >= 0.85 (IC is lower, need stronger consensus)

This would increase signal volume for Elections/Crypto and reduce false signals for Sports/Esports.
DuckDB sweep: one pass replacing `conf >= 0.80` with `conf >= tag_specific_threshold`.

### 2. Pool Construction: Dual-Period Qualification

The current scorecard builds pools from a fixed train period (before 2025-12-05). The risk
is that Esports base rate non-stationarity within the training window means traders qualified
during a 10% base rate window look exceptional but are mediocre in a 65% base rate window.

A **dual-period qualification** approach:
- Qualify traders on the full 6-month window (for sample size)
- But weight by the most recent 90-day window (for recency)
- Only include traders whose excess HR is positive in BOTH periods

This is a minimal change to the pool SQL: add a second CTE computing trailing-90d excess HR,
then inner-join to require `excess_hr_6m > 0 AND excess_hr_90d > 0`. Expected effect:
smaller, more reliable pool, fewer stale qualifiers, lower signal volume but better quality.

### 3. Asymmetric K-Cap by Tag Maturity

The K-cap analysis (strategy2) showed Esports K=50 → 100% HR but Sports K=All is necessary
for signal volume. The difference: Esports has a small, specialized community where the top-50
traders are genuinely elite. Sports has a massive community where even the top-50 may not be
specialist enough.

The parameter to sweep: `K = min(pool_size, target_percentile)` where `target_percentile` is
defined as the top 5% of each tag's qualified pool, not a fixed number. This makes K adaptive:

- Esports (880 qualified): top 5% = K~44 — close to the optimal K=50 already found
- Sports (21,242 qualified): top 5% = K~1,062 — allows more signal volume than K=50 would
- Crypto (3,493 qualified): top 5% = K~175 — between the current K=100 and K=All

Test by replacing fixed K values with percentile-based cuts. Expected: slight HR improvement
for medium-sized tags (Crypto, Elections) with no loss in signal volume.

### 4. Entry Price Relative to Market Consensus (not absolute price)

Strategy 2 found that entry price at 0.45-0.75 is the "uncertainty zone" worth trading, while
strategy 3 found that applying a price ceiling INVERTS the signal. The reconciliation: what
matters is not the absolute price but the **price relative to the qualified traders' collective
entry**.

A better filter: compute the volume-weighted avg entry price of the first K qualified traders.
If the current market price (at signal trigger time) is within 5pp of their avg entry price,
they entered BEFORE the crowd — genuine information advantage. If current price is already
10pp+ above their avg entry, the market has already repriced and they were just early followers.

Signal condition: `current_market_price - avg_qualified_entry_price <= 0.05`

This requires real-time market price data (available from CLOB WS), not just historical
positions. It is implementable in live deployment and resolves the price-filter inversion
paradox found in strategy 3.

### 5. Hold Filter Calibration: Tag-Specific Rather Than Universal 4h

The current 4h hold filter was derived primarily from sports (soccer, basketball) where games
last 2-3 hours. But different tags have different contamination windows:

| Tag | Game/Event Duration | Recommended Hold Filter |
|-----|--------------------|-----------------------|
| Soccer | 90+30 min | >=6h (game + buffer) |
| Basketball/NBA | 2.5h | >=5h |
| Esports | 1-3h per match | >=4h |
| Tennis | 2-5h | >=6h |
| Politics | N/A (no in-play) | >=1h (latency only) |
| Crypto | N/A | >=1h |
| Elections | N/A | >=1h |

A tag-specific hold filter would stop applying the 4h filter to Politics/Crypto/Elections
(where there is no in-play contamination, only latency), potentially recovering 50-100
signals/month that are currently filtered out unnecessarily.

---

## Cross-Hypothesis Connections

### Connection to tag-hr-consensus (MARGINAL verdict)

The tag-hr-consensus research (now two review rounds deep) validated Esports and Tennis signals
with Sharpe >3 in good folds but suffered from pool explosion (774 qualified Esports traders
in 2026-01) and base rate non-stationarity. The scorecard-strategies research confronts the
same pool explosion risk in a different form: Strategy 2 Esports K=50 has only 880 total
qualified traders, but if that pool grows to 3,000+ in a future period, K=50 becomes a
fragile hard cap rather than an adaptive quality gate.

The dynamic-threshold approach proposed in the round2 visionary review for tag-hr-consensus
applies directly here: `effective_min_excess_hr = base + k * max(0, pool_size - pool_target)`.
This single change prevents pool explosion for ALL three scorecard strategies without
tag-specific tuning. It should be adopted as a shared infrastructure component.

Reference: `/mnt/nvme/git/polymarket/polymarket/research/hypotheses/tag-hr-consensus/reviews/round2_visionary.md`

### Connection to tag-hr-copy (REJECTED) — first-mover premium

The tag-hr-copy rejection revealed that individual trades ≠ consensus. But buried in that
failure was an insight: the first qualified trader to enter a market is more informative than
the Nth. In tag-hr-copy, traders who were early in the market's trade sequence had higher HR
than those who entered later into an already-active market.

The scorecard-strategies framework now has the scaffolding to test this properly. The
`signal_entry = max(first_trade)` consensus trigger fires at the LAST qualified trader —
but the FIRST qualified trader's entry time is also available. The spread between first and
Nth entry (how long it took to build consensus) may predict signal quality:

- **Tight consensus** (all N traders within 30 min): genuine coordinated information
- **Slow consensus** (traders entering over 12+ hours): possibly independent arrivals, not coordination

Test: add `consensus_build_time = max(first_trade) - min(first_trade)` to the signal features.
Bucket signals by consensus_build_time and compare HR. If tight consensus has 5pp+ higher HR,
add `consensus_build_time <= 60min` as a parameter.

Reference: `/mnt/nvme/git/polymarket/polymarket/research/hypotheses/tag-hr-copy/README.md`

### Connection to hr_persistence.md — tag IC as signal quality multiplier

The scorecard synthesis found per-tag HR persistence IC varies from 0.674 (Sports) to 0.874
(Elections). This is not just a scorecard construction detail — it predicts how reliable the
SIGNAL from each tag's qualified pool will be over time.

A pool of Elections traders has 0.874 IC between their training HR and their test HR. When
they form consensus, you can be much more confident their signal reflects genuine skill than
when a Sports pool with IC=0.674 forms consensus. This suggests that position sizing should
be scaled by tag IC:

`position_size = base_size * (1 + tag_ic - 0.70)`

At Elections IC=0.874: 1.174x base. At Sports IC=0.674: 0.974x base. This is a principled
risk-adjustment that the research currently lacks — all tags are sized equally despite very
different signal reliability.

Reference: `/mnt/nvme/git/polymarket/polymarket/research/knowledge/signals/hr_persistence.md`

### Connection to pnl_ic_near_zero.md — why compounding score needs an economic term

The scorecard synthesis confirmed that PnL IC from HR is 0.005 — near zero. Yet all three
scorecard strategies are evaluated primarily by HR. The compounding score formula
(`excess_hr * avg_edge_usd / median_hold_days`) correctly includes `avg_edge_usd`, but
the strategy sweep results only report HR and excess HR, not economic edge.

The risk: the strategy optimizes for HR and selects markets where qualified traders enter
small positions at low edge (e.g., a $0.10 YES position in a market pricing YES at 0.09 —
technically correct but $0.09 edge). These would show 90% HR but negligible PnL.

Before tick validation, add an economic filter to the signal: only fire if the signal-time
vol-weighted entry price implies `edge_per_dollar >= 0.10` (i.e., the market is not already
fully priced by the qualified traders' entries). This maps directly to the scorecard's
`avg_edge_usd` component and ensures economic viability.

Reference: `/mnt/nvme/git/polymarket/polymarket/research/knowledge/signals/pnl_ic_near_zero.md`

### Connection to ideas.md — esports-sub-tag and specialist-consensus-earnings

Two ideas already queued in `ideas.md` become more actionable given the strategy2 findings:

**esports-sub-tag**: Strategy 2 found K=50 Esports pool gives 100% HR. If this is
decomposed by game (CS2, Dota2, LoL, Valorant), the per-game pool at K=50 would be ~12
traders per game. These tiny per-game pools are either extraordinarily predictive (top 12
CS2 specialists) or too thin to be meaningful. The data to test this is in the Parquet
snapshot. This should be the FIRST extension after tick-validation confirms the Esports signal.

**specialist-consensus-earnings**: Earnings markets have 72.9% YES base rate. A qualified
pool requiring excess_hr above 72.9% would be extremely selective — only traders who
correctly pick winning earnings plays in a market that is already biased 73% YES. These
traders are genuine insiders or analysts. If N=3 of them agree, the signal HR could
approach 95%+. Signal volume would be thin (Earnings is a small tag) but compounding score
would be high due to short hold time (earnings resolve within hours of announcement).

Reference: `/mnt/nvme/git/polymarket/polymarket/research/ideas.md`

---

## Compounding Improvements

### 1. Multi-Tag Portfolio: Orthogonal Signal Combination

The three strategies cover tags with different time horizons and signal cadences:

| Strategy | Tags | Hold | Signals/mo | Peak Hours |
|----------|------|------|-----------|-----------|
| Smart Pool | Esports | 2h | ~200 | Asian/EU evenings |
| Smart Pool | Crypto | 5h | ~190 | 24/7 |
| Tag-Consensus | Politics | 36h | ~156 | Daytime US/EU |

The correlation between Esports and Politics signals is near zero — they are completely
different markets, different traders, different news cycles. The correlation between Esports
and Crypto may be slightly positive (same "online native" trading community) but signals
still arrive at different times.

At 50 position slots, a joint portfolio would have:
- Esports: ~25 concurrent positions (200/mo at 2h hold)
- Crypto: ~27 concurrent positions (190/mo at 5h hold)
- Politics: ~234 concurrent positions (156/mo at 36h hold)

Total concurrent demand (~286) exceeds 50 slots. The binding constraint is Politics, which
occupies slots for 36 hours. **Recommended capital allocation**: 40 slots for Esports+Crypto
(fast-recycling), 10 slots for Politics (slow but high excess HR). This gives:
- Esports+Crypto: ~390 signals/mo at avg 3.5h hold = 56.9 concurrent — needs priority queue
- Politics: ~31 signals/mo at 36h hold = 10 concurrent — fits allocation

The portfolio-level compounding score is NOT the sum of individual CS — it is modulated
by capital utilization. For Esports+Crypto together: CS improvement from capital sharing
is approximately proportional to the fraction of time slots are idle in each strategy alone.

### 2. Entry Timing Ladder: Early Entry Captures Better Price

The consensus trigger fires at `max(first_trade)` — the Nth trader. But all N traders have
already entered at their individual entry prices. By the time the strategy fires, the market
may have already moved.

A **ladder entry** approach:
1. After the (N-1)th qualified entry, set a limit order 3-5pp below current market price
2. If filled within 15 minutes → signal confirmed, hold to resolution
3. If not filled → wait for Nth qualified entry at market price

This captures the price improvement of entering slightly before full consensus while still
requiring N-1 qualified traders for signal validation. The expected effect: 3-5pp better
entry price on 30-50% of signals = meaningful PnL improvement over uniform market-price entry.

This requires the live orderbook feed (available from CLOB WS) but no additional pool
construction or signal computation.

### 3. Asymmetric Hold: Early Exit on Price Confirmation

Markets that move to 0.85+ within 60 minutes of entry are providing confirmation. Most of
the PnL is captured in that first move; the remaining hold to 1.0 earns diminishing returns.

An **early exit gate**: if market price crosses 0.85 and hold time remaining is >30 min,
exit at 0.85 and free the capital. Expected PnL cost: ~2-3pp per position. Expected capital
recycling improvement: 20-30% more positions per day from freed slots.

This is most valuable for Esports (2h hold — early exit after 30 min means 75% capital
recycling improvement) and less valuable for Politics (36h hold — early exit saves minimal
time relative to hold duration).

### 4. Sequential Capital Recycling: Stake the Winnings

The compounding score formula assumes fixed position size. In practice, a correctly-sized
bankroll management scheme would reinvest winnings from short-cycle positions (Esports, 2h)
into later positions the same day, compounding returns intraday.

At 60% tick-by-tick HR (post-degradation estimate) on Esports:
- Morning session: 10 positions at $100 each, expected 6 correct × $X each
- Afternoon session: reinvest morning profits into 10 more positions
- Evening session: reinvest afternoon profits

Over 15-trading-days, the compounding effect at 60% HR is meaningful even without strategy
changes. This is a capital management improvement, not a signal improvement — implementable
immediately given any viable signal.

---

## New Hypothesis Ideas

For `research/ideas.md` backlog:

### HIGH Priority

1. **score-weighted-consensus**: Replace vol-weighted direction with composite-score-weighted
   direction. `direction = sum(composite_score_i * sign_i)`. Tests whether trader quality
   weighting outperforms dollar-weighting. Same signal infrastructure as strategy2, one-line
   change to the SQL. Priority: HIGH — builds directly on the vol-weighted finding, trivial
   DuckDB sweep.

2. **elite-gate-smart-pool**: Use elite pool (517 traders) as market selector, then apply
   full smart pool direction signal within selected markets. Two-stage filter: gate on elite
   presence (+22.7pp market quality lift), then consensus direction from broader qualified pool.
   Priority: HIGH — direct combination of strategy2 + strategy3 strongest findings.

3. **consensus-build-time**: Add `max(first_trade) - min(first_trade)` as a signal quality
   feature. Test whether tight consensus (all N traders within 30-60 min) significantly
   outperforms slow consensus (N traders over 4-12 hours). Priority: HIGH — directly
   addresses the consensus gap mechanism identified in `pitfalls/vectorized_vs_tick.md`.

4. **regime-gate-shared**: Implement per-tag rolling 30d base rate monitor, suspend all
   strategies when deviation exceeds 12pp from 6-month average. Shared infrastructure module.
   Priority: HIGH — addresses the single largest identified failure mode across all strategies.

### MEDIUM Priority

5. **vol-head-disagree-no-trade**: Trade the NO direction when vol-weighted and head-count
   direction disagree (vol says NO, head-count says YES). The vol direction wins 67-89% of
   the time when they disagree — the losing head-count side is a tradeable signal. Priority:
   MEDIUM — needs vectorized sweep to estimate signal volume and excess HR.

6. **esports-game-decomposition**: Decompose Esports by game (CS2, Dota2, LoL, Valorant)
   using Gamma event slug or name parsing. Per-game K=50 pools. Tests base rate stability
   and whether the 100% vectorized HR is driven by one specific game. Priority: MEDIUM —
   already queued in ideas.md, now more urgent given the 100% HR finding.

7. **politics-elections-joint**: Combine Politics NO and Elections NO qualified pools into a
   single signal. Both show genuine non-in-play signals with 1-2d hold. A trader who covers
   both tags may appear in either pool — requiring them to qualify in BOTH before counting
   their vote might give a smaller but higher-quality signal. Priority: MEDIUM — requires
   cross-tag pool join, testable in DuckDB.

8. **tag-ic-sized-positions**: Scale position size proportionally to the tag's HR persistence
   IC (Elections=0.874 → 1.17x, Sports=0.675 → 0.97x). Principled risk adjustment without
   requiring new signal research. Priority: MEDIUM — implementable immediately once any
   strategy is deployed.

### LOW Priority

9. **earnings-specialist-consensus**: Apply Strategy 1 consensus to Earnings tag (72.9% YES
   base rate). Qualified pool requires excess_hr above 72.9% threshold. Very selective
   pool, thin signal volume, but potentially extreme HR. Priority: LOW — Earnings is a small
   tag, validate other tags first.

10. **consensus-decay-monitor**: Track qualified pool size over rolling 60-day windows. Alert
    when pool shrinks >30% (traders losing their qualified status). Leading indicator of signal
    quality deterioration before PnL degrades. Priority: LOW — operational monitoring, not
    alpha generation.

---

## Summary

The three scorecard strategies are more complementary than competitive. The key cross-pollination
opportunities, in priority order:

**Most actionable (implement before or during tick validation)**:
1. The **elite-gate-smart-pool** combination directly marries Strategy 3's strongest finding
   (+22.7pp market quality lift) with Strategy 2's strongest signal (vol-weighted consensus
   direction). This should be added to the tick validation queue as a fourth candidate.

2. The **regime gate** (rolling 30d base rate monitor, suspend at 12pp deviation) should be
   built as shared infrastructure now — it is the single most documented failure mode across
   tag-hr-consensus and scorecard-strategies research.

3. The **score-weighted consensus** is a one-line SQL change with potentially significant
   upside. It should be tested in the same vectorized pass as the tick validation prep.

**Most creative (worth a dedicated DuckDB sweep)**:
4. **Temporal entry stacking** — markets that complete all three stages (first qualified entry
   → consensus → elite confirmation) may have dramatically higher HR. This is a genuinely
   new signal layer that no prior review has proposed.

5. **Consensus build-time as signal quality gate** — tight consensus (all N traders within
   30 min) may be the single strongest predictor of tick-by-tick fidelity. If confirmed, it
   directly closes the vectorized-to-tick gap by filtering out the weakest signals before
   they reach the replay runner.

The most important open question that the tick validation will answer: does the +44pp excess
HR on Esports Smart Pool survive to +15pp or better after consensus gap and in-play filtering?
If yes, the portfolio combination becomes immediately deployable. If it degrades to <10pp,
the research pivots to Politics NO (more resilient, less dependent on fast in-play filtering)
as the primary signal with Esports as a secondary experiment.
