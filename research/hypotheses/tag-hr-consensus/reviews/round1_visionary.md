# Visionary Review: tag-hr-consensus (Round 1)

**Reviewer**: Visionary
**Date**: 2026-03-06
**Status of hypothesis**: PROMISING — vectorized discovery complete, pending tick-by-tick validation

---

## Adjacent Signals

### 1. Volume-as-Primary-Signal (no trader pool required)

The discovery that HR tracks market volume monotonically (0% at <$10, 75%+ at >$1k) suggests that
**market volume itself is a signal independent of trader identity**. The qualified trader consensus
and the volume filter may be measuring the same underlying phenomenon from different angles: informed
capital concentrating on predictable outcomes.

Concrete test: build a signal that fires when any Esports/Tennis market crosses a volume threshold
within a defined pre-resolution window, *without* any trader qualification. Compare HR against the
consensus-gated version. If comparable, the trader classification overhead can be dropped entirely,
massively simplifying deployment.

Signal sketch: `total_qualified_volume >= $1k AND hold_hours_to_close <= 4h`

### 2. Volume Velocity (rate of accumulation, not level)

The discovery notes that vol>$2k improves Tennis HR by 6pp over vol>$1k. But the *timing* of
volume accumulation is untested. A market that reaches $2k in 30 minutes carries a different signal
than one that accumulates $2k over 3 days.

Test: compute `volume_per_hour` over the last N hours at consensus trigger. Compare HR across
velocity buckets. High velocity + consensus may push HR further past 90%.

### 3. Cross-Market Consensus (same event, multiple markets)

Many Esports events have multiple markets per match (map winner, player kills, tournament winner).
When qualified traders converge on the YES side across 2+ markets from the same event, the signal
orthogonality is higher than a single-market consensus — each market is an independent bet on the
same underlying outcome.

Test: join consensus signals by `event_id` (from the `markets` table). Fire entry in all markets
of an event where at least 2 distinct markets each have N>=3 qualified trader consensus. Expected
HR improvement: 3-8pp over single-market consensus.

### 4. Inverse Signal on Low-Volume Markets (NO side)

The volume-HR relationship shows micro-markets (<$10) have 0% YES HR. That is: **YES NEVER wins
in micro-markets that attract qualified traders**. This implies a NO signal exists in the micro tier.
The structure may be: qualified traders enter micro-markets as bots testing market mechanics, and
these markets resolve NO by default.

Test: flip signal direction in vol<$10 markets. Expected NO HR: 90%+ (but thin markets, execution
difficult). This is low-priority for deployment but worth quantifying.

### 5. Consensus Quality: Trader Disagreement as Anti-Signal

The sweep only measures YES consensus. What happens in markets where qualified traders split
(some YES, some NO)? A market with 3 qualified YES traders and 2 qualified NO traders may be
genuinely ambiguous — or it may be an anti-signal where the YES side is wrong.

Test: add a "dissent ratio" column to market stats: `n_qual_yes / (n_qual_yes + n_qual_no)`.
Filter to dissent_ratio >= 0.8 (strong majority). Compare HR to the current
all-markets-with-N-yes result. Expected improvement: 4-10pp.

---

## Parameter Variations

### 1. Staggered Entry: Wait for Nth+1 Trader After Window Close

Current signal fires at `max(first_trade)` — i.e., the moment the Nth trader enters. An alternative
is to wait a fixed delay (e.g., 15 minutes) after the Nth entry before firing, to confirm consensus
is not noise. The 2h median hold leaves room for a 15-minute delay without material capital cost.

Test: add `entry_delay_minutes` to sweep: {0, 5, 15, 30}. Measure HR vs entry_delay. This is
particularly relevant for Tennis where the W=8h parameter sensitivity suggests timing matters.

### 2. Pool Qualification: Rolling 90-Day vs 6-Month Window

Current pool uses a 6-month training window. Esports base rates shift dramatically (10% to 65%)
within that window. A trader with 70% HR during a period of 10% base rate is extraordinarily
skilled; the same HR during a 65% base rate period has minimal excess. Rolling shorter windows
(90 days) would re-qualify pools more frequently and adapt to the non-stationary environment.

Concrete change: replace `INTERVAL 6 MONTH` with `INTERVAL 90 DAY` in pool construction. Re-run
sweep. Expected effect: higher precision pool (fewer stale qualified traders) at cost of lower
recall.

### 3. N=6 and N=7 for Esports (above current sweep ceiling)

The sweep only tested N={2,3,4,5}. Esports has 628 signals at N=5 across 3 folds — enough volume
to test N=6 (expected ~150-200 signals) and N=7 (~50-80). Higher N should increase HR at cost of
signal frequency. Worth quantifying the HR ceiling before validation.

### 4. Price-Stratified Consensus: Only Count Traders Below Entry Threshold

The sweep uses `max_pool_entry_price` to exclude sure-thing buyers from pool qualification.
A tighter version: only count traders in the consensus who entered *below* the price ceiling,
discounting late entrants who entered near resolution. This weights the consensus toward traders
with genuine predictive advantage, not liquidity-takers.

### 5. Time-of-Day Filter for Tennis

The W=8h sensitivity finding (W/2=4h drops HR by 9.1pp) implies Tennis consensus forms during
specific pre-match hours. If tennis matches cluster at predictable UTC times (e.g., Wimbledon
at 10:00-22:00 UTC), a time-of-day filter could improve signal quality without changing N or W.

Test: add `hour_of_day_range` parameter. Split signals into morning (00-12 UTC) and afternoon
(12-24 UTC). If HR differs by >5pp, condition entry on the better window.

---

## Cross-Hypothesis Connections

### Connection to tag-hr-copy (REJECTED)

The root cause of tag-hr-copy's failure was individual-vs-consensus mismatch. This hypothesis
directly addresses that failure. But the connection runs deeper: tag-hr-copy showed 1H markets
are confirmed gambling (HR=49.8% vs base 47.3%) and should never be added to this sweep, even
as a curiosity test. The bot contamination problem in Esports (3000+ trades/month, HR=0%) is
structurally similar to what would happen in 1H if qualified pool construction failed — worth
noting in the esports-bot-classification specification.

Reference: `/mnt/nvme/git/polymarket/polymarket/research/hypotheses/tag-hr-copy/README.md`
Reference: `/mnt/nvme/git/polymarket/polymarket/research/knowledge/pitfalls/individual_vs_consensus_signal.md`

### Connection to esports-sub-tag idea (queued)

The Esports base rate non-stationarity (10% in 2025-01 to 65% in 2025-10) may be driven by
different game composition within the Esports tag — CS2 vs Dota2 vs LoL vs Valorant have
different match structures and YES frequencies. If one game drove the 65% peak, per-game
consensus pools would isolate that dynamic and prevent contamination across game types.

This is a high-value next step IF the tick-by-tick validation of consensus shows residual HR
instability across folds. Per-game decomposition could be the explanation and fix.

Reference: `/mnt/nvme/git/polymarket/polymarket/research/ideas.md` (esports-sub-tag entry)

### Connection to tag_base_rates.md (Earnings tag)

The knowledge base shows Earnings has a 72.9% YES base rate — the highest of any tag. If a
qualified trader pool can be constructed for Earnings (different dynamics, fewer bots, scheduled
events like tag-hr-copy's 1H but with genuine edge), consensus signals there could have even
higher absolute HR than Esports/Tennis, though excess HR may be lower.

The hold time would differ (Earnings markets may be longer-dated), but the consensus mechanism
should apply. Earnings markets are fundamentally scheduled events like Tennis — informed traders
(insiders, analysts) have genuine edge.

Reference: `/mnt/nvme/git/polymarket/polymarket/research/knowledge/data/tag_base_rates.md`

### Connection to vectorized_tick_gap_anatomy.md (Step 3 warning)

The gap anatomy document warns that high-consensus markets can be ANTI-predictive because they
are popular/efficient-priced. This contradicts the finding here (N=5 is optimal, not degraded).
The reconciliation: Esports and Tennis markets with N=5 are NOT the globally popular markets —
they are niche events that happen to attract a small pool of genuinely informed specialists.
The anti-prediction effect at high consensus applies to cross-market consensus (elections, NBA),
not niche-sport specialist consensus.

This distinction is worth capturing explicitly: specialist consensus in illiquid niche markets
behaves differently from generalist consensus in liquid popular markets.

Reference: `/mnt/nvme/git/polymarket/polymarket/research/knowledge/pitfalls/vectorized_tick_gap_anatomy.md`

---

## Compounding Improvements

### 1. Pre-Signal Volume Check at Execution Time

The vol>=1k filter is currently applied over resolved positions (backward-looking). In live
deployment, you need to know volume BEFORE resolution. The market may have $100 volume at
consensus trigger time and grow to $1k before close — or it may stay at $100. If you can
observe real-time market volume from the CLOB WS orderbook stream, add a pre-trade volume check
at the moment of intended execution. This gates the 2h hold to only high-confidence markets and
reduces capital tied up in failed signals.

### 2. Tiered Position Sizing by Consensus Strength

Current sweep uses uniform position sizing. The compounding score formula
(`excess_hr * median_pnl / median_hold_days`) assumes flat sizing. Volume-quality correlation
suggests a natural sizing rule: `position_size = base * (market_vol / $1k)^0.5` — larger
positions in higher-volume markets where HR is demonstrably higher.

At $1k market vol: base size. At $5k: 2.2x base. At $10k: 3.2x base. With a cap at 5x.
This would improve PnL per capital unit without changing the signal.

### 3. Staggered Portfolio Across Both Tags

Esports has 209 signals/fold (monthly), Tennis has 27-91 depending on params. At 2h median hold,
these are almost entirely non-overlapping in time. A joint portfolio running both tags with
combined capital allocation would have higher throughput than either tag alone. Target: 250-300
combined signals/month at avg 2h hold = ~25 concurrent positions at any time with 50-slot
budget.

### 4. Early Exit Trigger

The 2h median hold is driven by market resolution time. If the market price moves strongly toward
1.0 after consensus entry (e.g., price >= 0.92 within 30 minutes), an early exit at near-certainty
captures most of the PnL without holding to resolution. This frees capital faster.

Test: add an `exit_price_trigger` parameter to tick-by-tick validation. At 0.90 trigger:
estimated hold reduction of 20-30 minutes at cost of ~1-2pp of PnL per position.

---

## New Hypothesis Ideas

For `research/ideas.md` backlog:

1. **volume-velocity-consensus**: Fire entry when both consensus (N>=3 qualified traders) AND
   volume velocity (>$500/hour in the last 2 hours) are present. Combines two signals that each
   separately predict YES wins. Priority: high.

2. **esports-game-decomposition**: Decompose the Esports tag by game type (CS2, Dota2, LoL,
   Valorant) and build per-game qualified pools. Tests whether the non-stationary base rate
   and bot contamination are game-specific. Expected: one game drives most of the consensus
   signal, the rest are noise. Priority: high (do after tick-by-tick validation).

3. **specialist-consensus-earnings**: Apply consensus mechanism to Earnings tag (72.9% YES base
   rate, scheduled events). Build qualified pool from traders with excess HR above 72.9% base.
   Expect lower excess HR than Esports but potentially higher absolute HR and different
   hold-time profile. Priority: medium.

4. **dissent-filtered-consensus**: Add trader disagreement filter to consensus signal. Only fire
   when dissent ratio (n_qual_yes / total_qual) >= 0.8. Tests whether unanimous consensus
   outperforms majority consensus. Priority: medium.

5. **consensus-decay-detector**: Track whether the Esports qualified pool shrinks over time
   (traders dropping below excess HR threshold). If pool size is declining, signal quality is
   declining. Build a pool health metric that can trigger automatic re-qualification or strategy
   pause. Priority: low (operational concern, not alpha).

---

## Summary

The most immediately actionable direction is the **volume-as-primary-signal** test. The discovery
shows volume tracks HR almost perfectly across four tiers, and the qualified trader consensus may
be a proxy for "smart money is accumulating" rather than an independent signal. If volume alone
achieves 70%+ HR in Esports/Tennis markets, it simplifies the live strategy dramatically —
no pool qualification, no bot filtering, no walk-forward retraining, just a volume threshold and
a market-type filter. This should be tested in the same vectorized framework before tick-by-tick
validation of the full consensus strategy, because it could replace or augment the consensus
mechanism at lower operational complexity.

The second-highest-priority direction is **esports-game-decomposition**. The non-stationary base
rate (10% to 65% across folds) is the largest unresolved risk in this hypothesis. If that variance
is driven by a single game (e.g., Dota2 tournaments concentrating in Q4 with high YES rates), then
the cross-fold stability of the signal is much better than it appears — the instability is
compositional, not structural. Understanding this resolves the main reason to distrust the
walk-forward results before committing to live deployment.
