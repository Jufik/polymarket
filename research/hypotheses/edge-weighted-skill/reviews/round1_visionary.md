# Visionary Review: edge-weighted-skill (Round 1)

**Date**: 2026-03-09
**Reviewer**: visionary

---

## Adjacent Signals

### 1. Direction-Conditioned Pool Specialization

The 51% NO-skilled vs 12.6% YES-skilled split is a massive asymmetry that discovery only touched on. The adjacent signal is to build **direction-specific pools** as first-class citizens rather than post-hoc filters. A NO-specialist pool for Esports and NFL (where NO-skilled dominates) and a YES-specialist pool for Politics could be managed as separate strategies with independent K values. The composition analysis shows these are genuinely different populations — do not blend them into a single ranked list and filter later.

Concrete step: Run decomposition_sweep again with two separate scoring passes — one on YES positions only, one on NO positions only — then build separate K=25/K=50 pools per direction per tag. Compare pool composition stability across folds between unified and split approaches.

### 2. Dual-Skilled Traders as a "Both Sides" Arb Signal

The 964 dual-skilled traders (3.3% of pool) show high excess HR on both YES and NO sides simultaneously. The top dual-skilled traders have overall HR 87-99.9% with yes_bucket_excess and no_bucket_excess both positive. This is a distinct population with a different behavioral profile: they are likely handicappers who model the full distribution, not directional betters. Their signal structure is: when they enter a market at all, the direction matters less than the fact of entry.

Adjacent signal: treat dual-skilled traders as a **market-selection signal** (which markets resolve clearly), not a direction signal. Combine their YES and NO entries into a single "market quality" filter — use their presence as a pre-screen before copying ANY direction from other pools. Expected effect: reduces exposure to ambiguous markets.

### 3. NO-Skill as a Hedge Layer for YES-Copy Strategies

The InPlay Copy K=10 at 90.9% HR fires 121 signals/month at $1,553 PnL. The Edge Consensus K=200 N=2 fires 761 signals/month at $3,582 PnL and 63.7% HR. These two strategies are tracking different market regimes (near-certainty vs mid-price). The NO-skilled pool of 14,915 traders is largely unexploited.

Adjacent signal: In a market where the InPlay copy fires on YES side, simultaneously check whether any top-50 NO-skilled traders have recently exited (SELL NO, which is a bullish confirmation) or entered NO side (bearish contradiction signal that should suppress the copy). This uses the NO-pool as a **veto gate** rather than a separate copy strategy.

### 4. Recency-Windowed Bucket Excess HR

Current bucket_excess_hr is computed over all historical data. The walk-forward shows Politics composite is stable (σ=0.059 at K=25) while Crypto edge_primary is unstable (σ=0.236 at K=25). The instability in Crypto edge_primary suggests that Crypto traders' edge concentration at specific price buckets changes over time — likely related to market regime (bull/bear).

Adjacent signal: compute bucket_excess_hr over a rolling 90-day window in addition to the lifetime window. For Crypto specifically, the recency-windowed version may be more predictive despite the overall finding that naive HR beats recency-weighted HR (`signals/hr_persistence.md`). The persistence finding is for raw HR, not bucket-adjusted excess — the two are different constructs.

---

## Parameter Variations

### 1. Consensus K=200 N=2 — Tag-Isolated Variant

The K=200 N=2 consensus at 63.7% HR is the strongest mid-price result ($3,582 PnL, 761 signals). But this pool mixes tag types. Walk-forward shows tag-specific optimal scoring differs substantially. A parameter variation to test: **K=200 restricted to Politics-only traders** (Politics YES-skilled pool) with N=2 threshold. Expected: higher excess HR (+10-15pp) with lower signal count. The Politics composite scorecard already shows K=100 N=5 is tick-validated at +41pp — the K=200 N=2 may hit a sweet spot between signal count and quality.

### 2. Minimum Cell Occupancy Gate for Bucket Excess Scoring

The pre-mortem flagged that thin cells (trader with 20 positions across 5 tags x 2 directions x 5 buckets = 2 positions per cell) corrupt bucket_excess_hr. Untested parameter: **require minimum 10 positions per price bucket per direction per tag** before computing bucket_excess_hr for that trader's cell. Traders who don't meet this threshold fall back to the simpler excess_hr. This is a data quality gate that likely improves the signal-to-noise ratio of the edge-primary scoring.

### 3. InPlay Copy K=10 — Exclude Sure-Thing Contamination

InPlay K=10 shows 90.9% HR and $1,553 PnL on 121 signals. The copy_vs_pooling analysis shows 19% of in-play copy signals are in longshot (0-22% HR) and 8% are sure-thing. The elite whale copy validated result (`elite_whale_copy_results.md`) shows the bulk of PnL comes from the 0.10-0.30 price zone ($19K from $44-50 avg PnL per fill) and the 0.90+ zone (volume game). Untested: **InPlay K=10 with price exclusion of the 0.95-0.97 dead zone** (which has negative alpha per `in_play_elite_traders.md`). This should improve HR modestly and reduce capital allocation to the structural drag zone.

### 4. Walk-Forward Fold Granularity

Current walk-forward uses 3 folds with 12m train / 3m test. The Spearman rank correlations across folds are unstable (many negative values), especially for Sports. Untested: **monthly walk-forward** with 6m train / 1m test. Tighter folds detect faster pool turnover. If monthly Spearman is consistently positive, that validates the strategy for monthly pool refresh. If it's negative even at 1m, the signal is too noisy for production. The 3-fold structure may be masking monthly instability.

---

## Cross-Hypothesis Connections

### Connection to tag-hr-copy (FAILED)

The failed `tag-hr-copy` hypothesis (`research/ideas.md:Tested`) failed because it fired on individual trades, not N-trader consensus. The K=200 N=2 consensus here solves exactly that root cause — this is the structural fix that `tag-hr-consensus` (queued) was supposed to explore. The edge-weighted-skill hypothesis has already built the vectorized upper bound for what `tag-hr-consensus` was going to test. The queued idea can be marked as **superseded** by this hypothesis's consensus results — no separate experiment needed.

### Connection to in-play-traders (VALIDATED)

The InPlay K=10 at 90.9% HR here aligns with the validated elite whale copy at 94.2% HR. However, the pool construction differs: edge-weighted uses bucket_excess_hr scoring on in-play specialists, while the validated strategy uses CopyScore (HR x sqrt(N) x gambling_frac). The two approaches select **different traders** (the copy_vs_pooling analysis shows 0% overlap at K=10). This is worth investigating: which pool performs better in tick-by-tick? If the bucket_excess_hr pool matches or beats CopyScore at K=10, it supersedes the prior approach.

### Connection to composite_scorecard (VALIDATED, `signals/composite_scorecard.md`)

The walk-forward result confirms the pre-mortem concern: Track B (mid-price consensus) largely re-discovers what the composite scorecard already validated (+39.8pp Sports, +41pp Politics in tick). The main contribution of this hypothesis is the direction decomposition and bucket_excess_hr as a scoring component. The composite scorecard should be updated to reflect that the Politics K=100 N=2-3 variant here (vs N=5 in the validated scorecard) may offer better signal count with modest HR cost.

### Connection to esports-sub-tag (queued, `research/ideas.md`)

The decomposition shows Esports has only 40 YES-skilled traders (avg BEH 0.0274 — near zero) but 50 NO-skilled traders (avg BEH 0.4458). This strongly confirms the `esports-sub-tag` queued idea: Esports YES is structurally weak, but NO-skilled edge is substantial. The per-game decomposition (CS2, Dota2, LoL, Valorant) suggested there may be further concentration within specific games.

### Connection to longshot-narrowband (spawned from longshot_elite_results)

The longshot_elite tick validation (`longshot_elite_results.md`) found that the 20-30% price band is the only zone with positive alpha (+7pp over break-even). The edge-weighted hypothesis has a NO-skill population of 14,915 traders where many operate in the mid-price zone. The **NO side of the 20-30% range** (which corresponds to YES entry at 70-80%) is unexplored. A NO-skilled trader entering NO at 0.25 is making a YES-equivalent bet at 0.75 — a zone with +5.4pp structural alpha from the price base rates. This cross-connection is promising.

---

## Compounding Improvements

### 1. Portfolio Allocation Across Three Tracks

The three viable tracks have low pool overlap (11-17%):
- InPlay K=10: 121 signals/month, $1,553 PnL, 3.1h hold → CS = 90.9 × 1553/121 / (3.1/24) = high
- Consensus K=200 N=2: 761 signals/month, $3,582 PnL, 5.4h hold
- Edge Copy K=100: 2,121 signals/month, $665 PnL, 12.6h hold

Running all three as a portfolio adds these streams without correlation (different traders, different regimes). The total potential PnL is $5,800/month across the three tracks before any capital interaction. The key question is whether capital can be allocated independently — if yes, this is a near-trivial compounding win.

**Concrete step**: check whether a single market can appear in multiple tracks simultaneously. If overlap is low (expected given the pool separation), allocate capital proportionally (e.g., 40% InPlay, 40% Consensus, 20% Edge Copy based on Sharpe).

### 2. Dynamic Pool Refresh Cadence

The InPlay K=10 has small N (121 signals) so pool composition matters a lot — one bad trader corrupts 10% of signals. Monthly pool refresh may be too slow. The `live/consumers/market_events.py` infrastructure supports real-time pool refresh on resolution events. For InPlay specifically, use **weekly pool refresh** during training window recomputation. The walk-forward stability shows Politics is stable enough for monthly refresh; Sports shows instability that warrants weekly for K=25.

### 3. Consensus Entry Timing Optimization

The K=200 N=2 fires when the 2nd trader enters. But the vectorized implementation uses avg pool entry price, which may not reflect the signal trigger timing. In tick execution, the entry is at N=2 trigger time — by which point the price may have already moved toward resolution. The 5.4h hold suggests these are not in-play markets. An improvement: record the TIME of the 1st pool-trader entry and use that as a signal pre-alert (send to `pending.signal`), then confirm at N=2 to execute. This could capture 10-20 minutes of price advantage before N=2 triggers.

---

## New Hypothesis Ideas

For `research/ideas.md` backlog:

1. **dual-skill-market-selector**: Use the 964 dual-skilled traders (both YES and NO BEH >= 0.02) as a market-quality pre-screen rather than a direction signal. When dual-skilled traders enter any market, treat it as a signal that the market is resolvable and well-understood. Copy ANY direction from the primary pool in that market with higher confidence. Priority: medium.

2. **no-direction-consensus**: Build a consensus pool strategy explicitly for NO-skilled traders (14,915 available). Politics and Esports show strong NO-skilled populations. K=50 N=2 NO-only consensus in Politics as a standalone strategy, completely separate from the YES-track. The structural framing has been YES-dominant throughout; NO has been an afterthought despite representing 51% of skilled traders. Priority: high.

3. **portfolio-three-tracks**: Formal backtest of the InPlay K=10 + Consensus K=200 N=2 + Edge Copy K=100 as a combined portfolio with capital allocation rules. Check for market overlap and inter-track correlation. If correlation is low (expected), this is the fastest path to $5K+/month total PnL. Priority: high.

4. **longshot-no-narrowband**: Restrict to NO positions at 70-80% entry price (equivalent to YES at 20-30% structural headwind zone, but NO side has +5.4pp structural alpha). Use NO-skilled top-50 pool. This cross-connects `longshot-narrowband` findings with the NO-skill discovery. Priority: medium.

5. **crypto-recency-window**: For Crypto specifically, test bucket_excess_hr computed on 90-day rolling window instead of lifetime. The edge_primary instability (σ=0.236) in Crypto may be regime-driven, meaning recent data is more predictive than lifetime data — opposite of the general HR persistence finding. Priority: low (narrow scope).

---

## Summary

The most promising next direction is the portfolio combination of the three already-discovered tracks. The low pool overlap (11-17%) means combining InPlay K=10 + Consensus K=200 N=2 + Edge Copy K=100 as an independent portfolio is likely near-additive: roughly $5.8K/month total PnL before capital constraints. This does not require new research — it requires a portfolio backtest and capital allocation framework, then tick validation of the Consensus K=200 N=2 track (the only unvalidated track of the three).

The second priority is the NO-direction gap: 51% of skilled traders are NO-specialists but all strategies have been analyzed through a YES lens. A dedicated NO-direction consensus strategy (especially for Politics and Esports) represents a structurally distinct, large population that has not been exploited. This is the highest-upside undiscovered territory in the current dataset.
