# Trader Scorecard Framework

**Date**: 2026-03-07
**Status**: design
**Author**: quant-research-strategist

---

## 1. Purpose and Scope

The trader scorecard is a **quantitative profile** attached to each maker address, updated periodically, that answers two questions:

1. **Copy signal**: "Should we follow this trader's individual entries?" (who to copy)
2. **Consensus qualification**: "Should this trader's entry count toward the consensus trigger?" (who counts)

These are distinct use cases with overlapping but non-identical metric priorities.

| Use case | Primary metrics | Secondary metrics | Decision |
|----------|----------------|-------------------|----------|
| Copy signal | Hit rate, stability, striking | Conviction, timing | Follow individual trades from top-scored traders |
| Consensus qual | Hit rate, stability | Tag specialization | Include in the consensus pool (binary gate + weight) |

The scorecard does NOT replace the consensus trigger mechanism. It improves WHO qualifies for the pool and HOW MUCH weight each trader's entry carries.

---

## 2. The Four Proposed Metrics: Analysis

### 2.1 Hit Rate (Weighted)

```
hit_rate_weighted(w) = SUM(correct_i * w_i) / SUM(w_i)
w_i = exp(-lambda * days_since_resolution_i)
```

**Strengths**: Directly measures the target variable. Exponential decay adapts to regime changes (traders who were sharp 6 months ago but are cold now get downweighted). This is the single most important metric -- all other metrics are meaningful only if the trader has positive excess HR.

**Weaknesses and design decisions**:

> [!CRITICAL]
> Hit rate MUST be computed as EXCESS over the tag-specific base rate, not absolute HR.
> A trader with 70% absolute HR on Culture (88.6% NO base) is -18.6pp below base -- unskilled.
> A trader with 50% absolute HR on Elections (9% YES base) is +41pp above base -- elite.
> See `data/tag_base_rates.md`.

> [!WARNING]
> The decay parameter lambda creates a tradeoff: fast decay (lambda=0.02, ~50d half-life)
> adapts to regime changes but requires high trade frequency to maintain stable estimates.
> Slow decay (lambda=0.005, ~140d half-life) provides stability but misses regime shifts.
> Esports base rate swung 28pp within a single 6-month training window.
> Recommendation: lambda = ln(2)/90 ~ 0.0077 (90-day half-life, balances adaptiveness and stability).

> [!WARNING]
> Hit rate from `trader_positions_resolved` is corrupted for ~12% of positions due to
> split-position blind spots. Use `maker_positions_resolved_corrected` exclusively.
> See `pitfalls/split_position_blind_spot.md`.

**Recommended formula** (excess, tag-specific, decay-weighted):

```
excess_hr_weighted(trader, tag) =
  SUM(correct_i * w_i) / SUM(w_i) - base_rate(tag, period)
where w_i = exp(-ln(2)/90 * days_since_resolution_i)
```

### 2.2 Conviction

```
conviction(w, market) = buys_in_dir / (buys_in_dir + sells_against)
```

**Strengths**: Captures positional consistency. A trader who enters YES then rapidly sells half their position is less convicted than one who holds.

**Weaknesses**:

> [!WARNING]
> SELL semantics are ambiguous on Polymarket. SELL YES could be: (a) exiting a position
> (low conviction), or (b) a split-minted token being sold after the trader ALSO holds
> the other side. 55.9% of makers have used the split route. Conviction computed naively
> from BUY/SELL counts conflates these two behaviors.
> See `pitfalls/sell_is_exit.md` and `pitfalls/split_position_blind_spot.md`.

**Revised approach**: Instead of trade-level BUY/SELL ratios, use **net position direction consistency**:

```
conviction_score(trader, tag) =
  fraction of markets where final net position direction matches the trader's
  FIRST trade direction in that market
```

This measures "did the trader change their mind?" which is less contaminated by split mechanics. A trader who always sticks with their initial thesis (even if wrong) has high conviction.

**Alternative (simpler, recommended for v1)**: Drop conviction as a standalone metric and subsume it into the **multi-trade dedup** question. Our prior research found that deduplicating multiple trades from the same trader is counterproductive -- ongoing trading IS the conviction signal (`pitfalls/dedup_counterproductive.md`). Therefore, conviction is already captured by the consensus trigger: a trader who enters a market counts as one vote regardless of how many times they trade it.

**Recommendation for v1**: OMIT conviction as a separate scored metric. It adds complexity, is contaminated by split mechanics, and the information it would carry is already captured by the binary "trader has a position in this market" signal.

### 2.3 Striking Score

```
striking_score(w) = mean(edge/vol * weight)
edge = |resolution_price - entry_price|
vol = realized volatility of the market
```

**Strengths**: Captures value-finding ability. A trader who enters at 0.30 in a market that resolves to 1.00 has a stronger "strike" than one entering at 0.90.

**Weaknesses**:

> [!WARNING]
> Entry price is the STRONGEST predictor of YES HR in our data (Exploration 2 in
> signal_quality_summary.md). Markets entered at 0.70+ show 88-100% HR. Markets entered
> below 0.40 show 0-28% HR. This means striking_score is highly correlated with hit_rate
> but in the OPPOSITE direction to what the formula implies.
> Entry at 0.30 (high "edge") is the WORST bucket. Entry at 0.85 (low "edge") is the BEST.

The formula assumes a contrarian thesis: "traders who find distant-from-consensus entries are skilled." Our data contradicts this -- the strongest signal comes from traders **confirming favorites** at high prices, not from contrarian bets at low prices.

**Revised approach**: Replace the contrarian striking score with a **calibration score**:

```
calibration_score(trader, tag) =
  mean( |entry_price_i - realized_payout_i| * w_i )
  where realized_payout = 1.0 if correct, 0.0 if incorrect
  Lower is better (closer to perfectly calibrated)
```

A well-calibrated trader enters at prices that closely match the true probability. When they enter at 0.80, the market resolves YES ~80% of the time. When they enter at 0.30, YES resolves ~30% of the time. This measures price efficiency, not contrarianism.

**Alternative (simpler, recommended for v1)**: Use **average entry price on correct predictions** as a proxy. This directly measures "when this trader is right, how much does the market move?" Higher average correct-entry price means the trader enters late (lower upside but higher accuracy). Lower average correct-entry price means the trader enters early (higher upside but lower accuracy). The copy strategy can then weight by risk/reward preference.

**Recommendation for v1**: Replace striking_score with `avg_edge_usd`:

```
avg_edge_usd(trader, tag) =
  mean(pnl_i | correct_i = 1) * excess_hr(trader, tag)
  where pnl_i = (1.0 - entry_price_i) * position_size
```

This is the expected dollar edge per position. It directly feeds into the compounding score and has an economic interpretation: how much does following this trader earn per bet?

### 2.4 Stability Bonus

```
stability_bonus(w) = mean(window_hr) / (std(window_hr) + epsilon) * log(1 + n_windows)
```

**Strengths**: This is the Sharpe ratio of hit rates across time windows, scaled by sample size. It penalizes streaky traders and rewards consistency. Our Track 5 (Sustainability Tiers) research confirmed that temporal consistency matters: traders who maintain excess HR across different base rate regimes are genuinely skilled, not lucky.

**Weaknesses**:

> [!WARNING]
> Most Polymarket traders have very few resolved positions per tag per window.
> With 5 positions in a 2-month window, the HR estimate is +/- 22pp (binomial SE).
> Stability computed over noisy window-level HRs will itself be noisy.
> Minimum requirement: >= 3 windows with >= 5 positions each (15 total minimum).

> [!TIP]
> The `log(1 + n_windows)` term prevents infinite stability from a single window
> (std = 0 with one data point). But it also means a trader who has been active
> across many windows gets a scale boost regardless of consistency. Consider capping
> the log term at log(6) = 1.79 to prevent sample-size inflation.

**Revised approach**: Rename to **consistency_sharpe** for clarity, and cap the sample-size multiplier:

```
consistency_sharpe(trader, tag) =
  mean(excess_hr_per_window) / (std(excess_hr_per_window) + 0.05)
  * min(log(1 + n_windows), 1.8)
  where windows are 60-day non-overlapping blocks
  and excess_hr is computed per-window against the window's tag-specific base rate
```

**Critical design choice**: Use excess_hr per window (not absolute HR) to automatically correct for base rate non-stationarity within the stability calculation. A trader who tracks the base rate across regimes (HR drops when base rate drops) has ZERO excess stability -- correctly scored as no edge. A trader who maintains positive excess_hr when the base rate swings (e.g., Esports going from 37% to 65%) has genuine cross-regime stability -- highly valuable.

---

## 3. Missing Metrics to Add

Based on our research corpus, the following metrics are NOT in the original four but carry proven signal:

### 3.1 Signal-Time Volume (PROVEN CAUSAL)

From Track 4 exploration: the total USD volume committed by the first N qualifying traders at signal time is a monotonically increasing predictor of YES HR. Esports xlarge (>$1k): +27-33pp excess. Tennis xlarge: +20-45pp excess.

```
signal_vol(trader, market) = abs(net_usd) on first entry
```

This is not a per-trader metric (it is per-market-signal), but the TRADER'S typical commitment size is a useful profile field:

```
avg_commitment(trader, tag) = median(abs(net_usd_i)) across positions
```

Traders who put down $2,000 per position carry more information (and more risk) than $20 position traders. This is size-weighted edge: do they size up when confident?

### 3.2 Entry Timing (Market Lifecycle Position)

From Exploration 5: market age at signal time shows moderate but inconsistent effects. However, a different timing signal was identified in the copy_trader analysis:

```
avg_entry_order(trader, tag) =
  mean(entry_rank_i) across all consensus markets where this trader participated
```

Where `entry_rank_i` is this trader's position in the entry sequence (1st, 2nd, 3rd...) for each market. From the copy_trader data:

| Entry order | HR | Excess |
|-------------|-----|--------|
| 1st | 47.2% | +10.5pp |
| 2nd | 57.8% | +21.0pp |
| 3rd | 64.1% | +27.3pp |
| 4th | 72.6% | +35.8pp |
| 5th | 72.4% | +35.6pp |

Traders who are consistently followers (high avg_entry_order) have HIGHER HR than leaders. This is counterintuitive but confirmed: followers wait for more information before committing.

**For copy strategy**: This metric is less useful (we need leaders to trigger the signal). But for **consensus qualification**, it reveals that follower entries are more informative -- a consensus that includes followers should be weighted higher than one consisting entirely of first-movers.

### 3.3 Tag Specialization Index

```
specialization(trader) =
  max(n_positions_per_tag) / sum(n_positions_all_tags)
```

A value near 1.0 means the trader is a specialist (e.g., only trades Esports). A value near 0.0 means the trader is a generalist. From prior research, per-tag scorecards matter because base rates vary 9-73% across tags. A specialist's scorecard in their tag is more reliable than a generalist's score averaged across tags they occasionally dabble in.

### 3.4 Drawdown / Max Consecutive Losses

```
max_consecutive_losses(trader, tag) =
  max run length of consecutive incorrect positions
```

This is a risk metric, not a return metric. A trader with 65% HR but a max streak of 12 consecutive losses is riskier to copy than one with 65% HR and max streak of 5. This matters for position sizing and capital allocation: high-drawdown traders should receive smaller per-position sizing even if their aggregate HR is identical.

### 3.5 Profit Factor

```
profit_factor(trader, tag) =
  sum(pnl_i | correct_i = 1) / abs(sum(pnl_i | correct_i = 0))
```

Values > 1.0 mean gross profits exceed gross losses. Unlike HR alone, profit factor captures whether the trader makes more on wins than they lose on losses. A trader with 45% HR but 3.0x profit factor (big wins, small losses) may be more valuable than one with 60% HR and 1.1x profit factor (small wins, similar-sized losses).

---

## 4. Composition: How the Metrics Combine

### 4.1 Rejected Approaches

**Pure multiplicative** (`score = hr * conviction * striking * stability`): Rejected. A zero in any component zeroes the entire score. Metrics with fundamentally different scales produce unstable products. A trader with excellent HR but zero stability (new trader, one window) gets score = 0, which is too harsh.

**Weighted linear sum** (`score = w1*hr + w2*stability + ...`): Rejected as the final form because it assumes metrics are substitutable. A trader with 0% HR but perfect stability should never be selected. Linear sums allow compensation across dimensions that should be non-compensatory.

**Threshold-only** (must pass minimums on each, then rank by HR): Too rigid. The current meh/mpe threshold system is already this approach. It throws away gradient information within the qualifying pool.

### 4.2 Recommended Approach: Tiered Gate + Weighted Composite

A two-stage architecture:

**Stage 1: Hard gates (binary pass/fail)**

These are non-negotiable minimums. Failing any one disqualifies the trader.

| Gate | Threshold | Rationale |
|------|-----------|-----------|
| Minimum positions | >= 10 resolved in tag | Statistical significance (binomial SE < 15pp at n=10) |
| Minimum excess HR | > 0pp (above tag-specific base rate) | Must demonstrate SOME predictive ability |
| Minimum activity windows | >= 2 non-empty windows | Cannot assess stability from a single burst |
| Bot guard | < 10,000 positions total | Filter automated market makers |
| Split-corrected data | Use `maker_positions_resolved_corrected` | Avoid PnL corruption from split positions |

**Stage 2: Composite score (continuous, for ranking and weighting)**

After passing gates, compute a weighted composite:

```
composite_score(trader, tag) =
    0.45 * norm(excess_hr_weighted)
  + 0.25 * norm(consistency_sharpe)
  + 0.20 * norm(avg_edge_usd)
  + 0.10 * norm(profit_factor)
```

**Weight rationale**:

- **Excess HR (0.45)**: The primary signal. Everything else is secondary to "does this trader predict outcomes?" Our entire research corpus confirms that HR -- after base rate adjustment -- is the dominant feature. Pool selectivity (K=10-30 by excess_hr) was the single strongest lever in exploration.

- **Consistency Sharpe (0.25)**: The anti-luck filter. Track 5 sustainability tiers showed that traders who maintain excess across sub-windows are genuinely skilled. This separates a trader who went 20/20 in one lucky month from one who went 8/10 across four separate months. Also the primary defense against base rate non-stationarity -- the #1 failure mode in our Esports research.

- **Average Edge USD (0.20)**: The economics signal. A trader with 60% excess HR who enters at 0.90 (max gain = $0.10) generates less capital-weighted edge than one with 40% excess HR who enters at 0.40 (max gain = $0.60). This metric ties the scorecard directly to the compounding score.

- **Profit Factor (0.10)**: The risk asymmetry signal. At 0.10 weight it has limited influence but serves as a tiebreaker among similarly-scored traders. Traders with high profit factor size up when confident and cut losses when wrong -- desirable behavior for copy following.

### 4.3 Why Not Include Conviction, Timing, or Specialization?

**Conviction**: Subsumed. The consensus trigger (one vote per trader per market) already implements a form of conviction measurement. Multiple trades from the same trader in the same market are deduplicated. Adding conviction as a score component would double-count this information.

**Entry timing (avg_entry_order)**: Excluded from the score because the finding (followers > leaders) applies at the CONSENSUS level, not the individual trader scoring level. A trader who is always a follower has high HR because they wait for confirmation -- but we need some traders to be leaders to trigger the consensus. Penalizing leaders in the scorecard would shrink the pool of early entrants, weakening the consensus trigger mechanism.

**Tag specialization**: Not a scoring metric but a FILTER. The scorecard should be tag-specific (see Section 5). A specialist's score IS their tag score. A generalist's score in each tag is computed independently. Specialization index is metadata for the operator (how many tags does this trader have usable scorecards in?), not a component of the score itself.

---

## 5. Normalization

### 5.1 Approach: Percentile Rank Within Tag Cohort

Each metric is normalized to [0, 1] via percentile rank within the cohort of traders who passed Stage 1 gates for that tag:

```python
def norm(values: list[float]) -> list[float]:
    """Percentile rank normalization to [0, 1]."""
    ranks = scipy.stats.rankdata(values, method='average')
    return (ranks - 1) / (len(ranks) - 1)  # 0 for min, 1 for max
```

**Why percentile rank, not z-score or min-max?**

- **Z-score**: Assumes normality. HR distributions are bounded [0, 1] and often skewed. Z-scores can produce negative values that are hard to interpret and combine.
- **Min-max**: Sensitive to outliers. A single trader with 95% HR in a tag where the median is 55% would compress all other traders to near-zero. Min-max also shifts every time a new extreme trader appears.
- **Percentile rank**: Non-parametric, robust to outliers, bounded [0, 1], and invariant to monotonic transformations of the underlying metric. A trader in the 75th percentile of excess HR is ranked the same whether the range is [5%, 40%] or [5%, 95%].

### 5.2 Minimum Cohort Size

Percentile normalization requires a meaningful cohort. If a tag has only 8 traders passing Stage 1 gates, percentile ranks are meaningless (each trader jumps ~12.5pp in rank).

**Minimum cohort size**: 20 traders per tag. Below this, fall back to the global cohort (all tags pooled, with tag-specific excess HR) for normalization.

### 5.3 Score Distribution Monitoring

Track the composite score distribution per tag over time. If the distribution narrows (all traders clustering near 0.5), the scorecard has lost discrimination power -- the qualifying gates are too strict or the metrics are too correlated. If the distribution widens bimodally, the pool contains two distinct populations that may warrant separate treatment.

---

## 6. Tag-Specific vs Global Scorecards

### 6.1 Recommendation: Tag-Specific Primary, Global Fallback

**Tag-specific is mandatory.** The evidence is overwhelming:

| Factor | Tag-specific impact | Evidence source |
|--------|-------------------|----------------|
| Base rates | 9% to 73% YES across tags | `data/tag_base_rates.md` |
| Hold times | 0.3d (Esports) to 22.4d (Politics) | `execution/hold_time_capital.md` |
| Pool quality | K=20 optimal for Esports, K=50 for Tennis | `signal_quality_summary.md` |
| Pool dynamics | Esports pool explodes 47 -> 774; Tennis stable 83 -> 315 | `copy_trader_notes.md` |
| Base rate non-stationarity | Esports swings 28pp per fold; Tennis stable | `period_base_rate_variance.md` |

A global scorecard would conflate a Crypto specialist (23.4% YES base) with a Culture specialist (11.7% YES base). Their absolute HRs are not comparable.

**Implementation architecture**:

```
trader_scorecard (global)
  ├── tag_scorecard[Esports]
  │     ├── excess_hr_weighted: 0.22
  │     ├── consistency_sharpe: 1.83
  │     ├── avg_edge_usd: $48.50
  │     ├── profit_factor: 2.1
  │     ├── composite: 0.78
  │     ├── n_positions: 84
  │     └── last_active: 2026-02-15
  ├── tag_scorecard[Tennis]
  │     ├── ... (same fields)
  │     └── composite: 0.42
  ├── tag_scorecard[Politics]
  │     └── (below gate: n_positions < 10)
  └── global_meta
        ├── primary_tag: Esports
        ├── n_active_tags: 2
        ├── specialization_index: 0.67
        └── total_positions: 126
```

### 6.2 Cross-Tag Portability

Should a trader with a strong Esports scorecard qualify for the Tennis consensus pool if they start trading Tennis?

**No.** Until they accumulate the minimum positions in Tennis (10 resolved), their Tennis scorecard is invalid. Cross-tag portability violates the finding that tag-specific base rates and trader populations are fundamentally different.

**Exception**: During an initial cold-start period for a NEW tag (one we have not previously tracked), we MAY bootstrap using global excess HR as a weak prior, heavily discounted:

```
cold_start_score(trader, new_tag) =
  0.3 * global_excess_hr(trader)  # weak prior
  + 0.7 * tag_excess_hr(trader, new_tag)  # accumulates over time
  transition to pure tag score when n_positions_in_tag >= 10
```

---

## 7. Temporal Strategy: When to Recompute

### 7.1 Recommended: Event-Triggered + Daily Batch

Two update mechanisms run in parallel:

**Event-triggered (incremental)**: After each market resolution in a tag, recompute the scorecard for all traders who had positions in that market. This keeps the scorecard fresh in fast-resolving categories (Esports: 167 resolutions/day at 50 slots) without wasteful recomputation.

```python
def on_resolution(condition_id: str, tag: str, resolution: MarketResolution):
    affected_traders = get_traders_with_positions(condition_id)
    for trader in affected_traders:
        scorecard = recompute_scorecard(trader, tag)
        cache.put(trader, tag, scorecard)
```

**Daily batch (full rebuild)**: Once per day, recompute all scorecards from scratch. This catches any data corrections, handles the exponential decay properly (decay is continuous but incremental updates may accumulate rounding errors), and ensures consistency.

### 7.2 Recomputation Window

The scorecard looks back:
- **Decay window**: All resolved positions with `exp(-lambda * days)` weighting (no hard cutoff)
- **Effective window**: ~270 days (3 half-lives at lambda = ln(2)/90). Positions older than 270 days contribute < 12.5% weight.
- **Consistency windows**: 60-day non-overlapping blocks within the effective window (up to ~4-5 windows)

### 7.3 Pool Refresh for Consensus Strategy

The consensus strategy qualification pool is rebuilt per fold (walk-forward) in backtesting. In production, it would rebuild daily or on a configurable schedule, using the scorecard composite as the ranking metric.

```python
def rebuild_pool(tag: str, k: int = 30) -> set[str]:
    """Select top-K traders by composite score for this tag."""
    scorecards = get_all_scorecards(tag)
    ranked = sorted(scorecards, key=lambda s: s.composite, reverse=True)
    return {s.trader for s in ranked[:k]}
```

---

## 8. Minimum Data Requirements

### 8.1 Per Trader, Per Tag

| Requirement | Threshold | Rationale |
|-------------|-----------|-----------|
| Resolved positions | >= 10 | Binomial SE < 15pp. At n=10, a 70% HR has 95% CI [35%, 93%] -- wide but usable. |
| Active windows | >= 2 | Cannot compute consistency_sharpe with 1 window (std = 0). |
| Recency | >= 1 position in last 90 days | Inactive traders should not remain in the pool indefinitely. |
| Total positions (bot guard) | < 10,000 | Exclude automated market makers with extreme position counts. |

### 8.2 Why 10 and Not 20 or 50?

The minimum is a GATE, not a scoring threshold. With 10 positions, the SE of HR is sqrt(0.5 * 0.5 / 10) = 15.8pp -- noisy but not unusable. The consistency_sharpe computation compensates: a trader with high variance across windows (noisy estimates) gets a low consistency score, which depresses their composite.

Raising the minimum to 20+ would severely limit the pool for niche tags (Tennis had only 15 qualifying traders in the 2025-07 fold at 5-position minimum) and for new tags being bootstrapped. The scorecard framework should be inclusive at the gate level and discriminating at the scoring level.

### 8.3 Tag-Specific Minimum Adjustment

Some tags have much higher trading frequency than others:

| Tag | Median positions per active trader per fold | Suggested minimum |
|-----|---------------------------------------------|-------------------|
| Esports | ~30 | 10 (standard) |
| Sports/Tennis | ~15 | 10 (standard) |
| Politics | ~5 | 8 (reduced -- long-dated markets, fewer resolutions) |
| Culture | ~8 | 8 (reduced -- but be aware 88.6% NO base) |

For tags with median < 10 positions per fold, consider reducing the minimum to 5 with a mandatory confidence penalty:

```
confidence_penalty = min(1.0, n_positions / 10)
adjusted_composite = composite * confidence_penalty
```

### 8.4 Bootstrap Confidence

For borderline traders (10-15 positions), compute a bootstrap 95% CI on excess_hr:

```python
def bootstrap_ci(outcomes: list[bool], n_boot: int = 1000) -> tuple[float, float]:
    """Bootstrap 95% CI for hit rate."""
    boot_hrs = [np.mean(np.random.choice(outcomes, len(outcomes))) for _ in range(n_boot)]
    return np.percentile(boot_hrs, [2.5, 97.5])
```

If the lower bound of the CI is below the base rate, flag the trader as "unconfirmed edge" even if their point estimate is above base rate. This prevents small-sample lucky traders from qualifying.

---

## 9. Failure Mode Analysis

### 9.1 Overfitting to Recent Streaks

**Risk**: Exponential decay upweights recent results. A trader who went 8/8 in the last 2 weeks dominates the scorecard, even if their prior 6 months were 50/50.

**Mitigation**: The consistency_sharpe component explicitly penalizes this. A streak-driven trader has high recent HR but low cross-window consistency. With 0.25 weight on consistency_sharpe, a streak trader's composite is capped below a consistently-good trader even if their recent HR is higher.

**Additional safeguard**: Cap the maximum contribution of any single window to the weighted HR:

```
max_window_weight = 0.40  # no single 60-day window contributes > 40%
```

### 9.2 Survivorship Bias

**Risk**: We only score traders who have resolved positions. Traders who entered positions that have not yet resolved are invisible. If these unresolved positions are systematically different (e.g., long-dated political markets with different win rates), the scorecard is biased toward fast-resolving market types.

**Mitigation**: Track unresolved position count and duration:

```
pct_unresolved = n_open_positions / (n_resolved + n_open_positions)
avg_unresloved_age_days = mean(now - entry_time for open positions)
```

If `pct_unresolved > 0.50`, the scorecard is based on less than half of the trader's activity. Flag as "partial scorecard." Do not use for consensus qualification until resolved fraction exceeds 0.50.

### 9.3 Gaming (If Scores Become Public)

**Risk**: If traders learn the scoring formula, they could manipulate their scorecard -- e.g., taking many small high-probability positions (BUY YES at 0.95 near resolution) to inflate HR.

**Mitigations**:
1. **Minimum edge filter**: Positions with entry price > 0.90 are excluded from scoring (max possible edge = $0.10, not economically meaningful for copy following).
2. **Position size minimum**: Positions below $5 USD are excluded (dust trades are noise).
3. **The mpe gate** (max pool entry price) already addresses this: mpe <= 0.80 excludes sure-thing entries.
4. **Publish ranks, not formulas**: If scores are ever made visible, show ordinal rank (1st, 2nd, ...) not the composite number. This makes reverse-engineering harder.

### 9.4 Regime Change

**Risk**: A 2024 election specialist has a strong Politics scorecard, but Elections are one-off events. In 2025, there are no US presidential elections, and the trader's edge may not transfer to midterm or foreign politics markets.

**Mitigations**:
1. **Exponential decay** (90-day half-life) naturally attenuates stale scores. After 180 days of inactivity, the score is at ~25% of its peak.
2. **Recency gate** (>= 1 position in last 90 days) removes fully inactive traders.
3. **Per-window excess HR** in consistency_sharpe: a trader who was only active in one window (election season) gets n_windows = 1, which caps their consistency_sharpe multiplier at log(2) = 0.69 (less than half of the max 1.8).
4. **Event-type segmentation** (future work): subdivide Politics into sub-tags (US Elections, Geopolitics, Policy) for finer-grained scorecards.

### 9.5 Base Rate Non-Stationarity

**Risk**: The #1 failure mode from our research. Esports base rate swung from 37% to 65% across folds. A trader qualified during a 37% base rate regime has inflated excess HR that collapses in a 65% regime.

**Mitigations**:
1. **Per-window base rate in consistency_sharpe**: Excess HR per window is computed against that window's base rate, not a fixed rate. A trader who tracks the base rate (HR rises when base rate rises) scores near-zero on consistency_sharpe, correctly reflecting no genuine edge.
2. **Regime gate at the strategy level**: If the current tag base rate deviates > 15pp from the training-period average, suspend consensus signals entirely. This is separate from the scorecard -- the scorecard is always computed, but the strategy chooses not to act when the regime is hostile.
3. **Decay speed can be adapted per tag**: For high-volatility tags (Esports), use faster decay (60-day half-life). For stable tags (Tennis, Sports), use standard 90-day.

### 9.6 Correlated Pool (Herding)

**Risk**: From Exploration 4: the dominant trader appeared in 55% of consensus markets. When 1-2 traders drive most consensus signals, the "independent agreement" thesis breaks down.

**Mitigations at the scorecard level**: The scorecard itself cannot fix this (it scores individuals, not pairs). But the scorecard enables a FIX at the consensus level:

```python
def compute_independence_weight(trader, market, pool):
    """Reduce weight if trader is highly correlated with others in pool."""
    co_occurrence = count markets where trader AND any other pool member both traded
    independence = 1.0 - (co_occurrence / trader.n_positions)
    return max(independence, 0.2)  # floor at 0.2 to avoid zeroing anyone
```

This requires the scorecard to store co-occurrence data, not just individual metrics. This is a **v2 enhancement** -- too complex for v1.

### 9.7 Copy-Trader Circularity

**Risk**: Followers have higher HR than leaders (confirmed finding). If the scorecard rewards followers, the pool fills with followers. But followers need leaders to follow. If all leaders are downscored, the consensus trigger loses its first movers.

**This is not a failure mode.** The consensus trigger fires when N unique traders enter. The entry ORDER is not part of the trigger condition. Whether trader A enters first and trader B follows, or vice versa, the consensus still fires at the Nth entry. The scorecard selects WHO counts, not WHEN they enter. Leaders are not penalized because avg_entry_order is intentionally excluded from the composite score (see Section 4.3).

---

## 10. Copy vs Consensus Implications

### 10.1 For COPY (Following Individual Top Traders)

The copy strategy follows the INDIVIDUAL entries of top-scored traders without waiting for consensus.

**Score priorities**: The copy strategy cares most about:

| Priority | Metric | Why |
|----------|--------|-----|
| 1 | excess_hr_weighted | Direct prediction of outcome quality |
| 2 | avg_edge_usd | Dollar-weighted profitability per trade |
| 3 | profit_factor | Risk/reward asymmetry (big wins, small losses) |
| 4 | consistency_sharpe | Stability matters less for individual following |

**Composite weight adjustment for copy**:

```
copy_composite =
    0.40 * norm(excess_hr_weighted)
  + 0.15 * norm(consistency_sharpe)
  + 0.30 * norm(avg_edge_usd)
  + 0.15 * norm(profit_factor)
```

Shifts weight from consistency (0.25 -> 0.15) to avg_edge_usd (0.20 -> 0.30) because the copy strategy's economics depend heavily on per-trade edge magnitude.

> [!WARNING]
> Individual copy strategies have been REJECTED in our research (tag-hr-copy: 67% vec -> 46% tick).
> The scorecard does not fix the structural problem: individual entries are 20-30pp noisier than
> consensus signals. The copy composite is defined for completeness but should be used with extreme
> caution and ONLY after tick-by-tick validation of the specific copy approach.

### 10.2 For CONSENSUS (Determining Who Counts as "Qualified")

The consensus strategy waits for N qualified traders to converge on a market.

**Score priorities**: The consensus strategy cares most about:

| Priority | Metric | Why |
|----------|--------|-----|
| 1 | excess_hr_weighted | Prediction quality of each contributing voter |
| 2 | consistency_sharpe | Stable predictors are more reliable voters |
| 3 | profit_factor | Tiebreaker |
| 4 | avg_edge_usd | Matters for position sizing, not qualification |

**Composite weight for consensus** (use the default weights from Section 4.2):

```
consensus_composite =
    0.45 * norm(excess_hr_weighted)
  + 0.25 * norm(consistency_sharpe)
  + 0.20 * norm(avg_edge_usd)
  + 0.10 * norm(profit_factor)
```

**Two uses of the consensus composite**:

1. **Pool selection** (binary): Top-K by consensus_composite qualify for the pool. This replaces the current meh/mpe threshold system with a smoother, multi-dimensional selection.

2. **Weighted consensus** (continuous, v2): Instead of counting each trader as 1 vote, weight by their composite score. A Tier-1 trader's entry counts as 1.5 votes; a Tier-4 entry counts as 0.5.

```python
def weighted_consensus(traders_in_market: list[Trader], tag: str) -> float:
    """Weighted consensus count."""
    return sum(
        trader.scorecard[tag].composite
        for trader in traders_in_market
    )

# Fire signal when weighted consensus >= threshold
# threshold calibrated per tag from training data
```

This is more robust than flat counting because a market with 2 Tier-1 traders (weighted sum = 1.6) can trigger before a market with 3 Tier-4 traders (weighted sum = 1.2), correctly reflecting that the quality of agreement matters more than the quantity.

---

## 11. Composition Formula: Final Specification

### 11.1 Data Schema

```python
@dataclass(frozen=True, slots=True)
class TraderScorecard:
    trader: str               # lower-cased maker address
    tag: str                  # e.g., "Esports", "Tennis"
    computed_at: datetime     # timestamp of last computation

    # Stage 1 gates (all must pass)
    n_positions: int          # resolved positions in tag
    n_active_windows: int     # 60-day windows with >= 1 position
    last_active: datetime     # most recent position resolution
    is_bot: bool              # total positions >= 10,000

    # Stage 2 scored metrics (raw, pre-normalization)
    excess_hr_weighted: float # decay-weighted excess HR vs tag base rate
    consistency_sharpe: float # Sharpe of per-window excess HR
    avg_edge_usd: float       # mean PnL on correct predictions * excess HR
    profit_factor: float      # gross_profit / gross_loss

    # Composite (after percentile normalization)
    composite: float          # weighted sum of normalized metrics

    # Metadata
    avg_commitment_usd: float # median position size
    avg_entry_order: float    # avg position in entry sequence
    pct_unresolved: float     # fraction of positions still open
    confidence_penalty: float # min(1.0, n_positions / 10)

    @property
    def passes_gates(self) -> bool:
        return (
            self.n_positions >= 10
            and self.n_active_windows >= 2
            and self.excess_hr_weighted > 0.0
            and not self.is_bot
            and (datetime.now(tz=UTC) - self.last_active).days <= 90
        )
```

### 11.2 Computation Pipeline

```
Step 1: Extract positions from maker_positions_resolved_corrected
        Filter by tag (via event_tags join chain, NOT market_categories)

Step 2: For each trader with positions in this tag:
        a. Compute decay-weighted HR and excess vs tag-specific base rate
        b. Split into 60-day windows, compute per-window excess HR
        c. Compute consistency_sharpe from window-level excess HRs
        d. Compute avg_edge_usd from correct-position PnLs
        e. Compute profit_factor from gross profit / gross loss
        f. Apply gates (Stage 1)

Step 3: Among gate-passing traders (minimum 20 for percentile norm):
        a. Percentile-rank each metric
        b. Compute composite = weighted sum of normalized metrics
        c. Apply confidence_penalty for small-sample traders

Step 4: Rank by composite, select top-K for consensus pool
```

### 11.3 SQL Sketch (DuckDB)

```sql
-- Step 1: Extract tag-filtered resolved positions
WITH tag_positions AS (
    SELECT
        mp.trader,
        mp.condition_id,
        mp.first_trade,
        mp.resolved_at,
        mp.correct,
        mp.realized_pnl,
        mp.position,
        mp.net_usd,
        et.label AS tag
    FROM maker_positions mp  -- use corrected view
    JOIN markets m ON mp.condition_id = m.condition_id
    JOIN events e ON m.event_id = e.id
    JOIN event_tags et ON e.id = et.event_id
    WHERE et.label = {tag}
      AND mp.resolved_at IS NOT NULL
      AND mp.position IN ('YES', 'NO')
),
-- Step 2a: Decay-weighted HR
trader_metrics AS (
    SELECT
        trader,
        count(*) AS n_positions,
        sum(correct * exp(-0.0077 * date_diff('day', resolved_at, CURRENT_DATE)))
          / sum(exp(-0.0077 * date_diff('day', resolved_at, CURRENT_DATE)))
          AS hr_weighted,
        -- Base rate for this tag and recent period
        hr_weighted - {tag_base_rate} AS excess_hr_weighted,
        -- avg_edge_usd: mean PnL on correct positions * excess HR
        avg(CASE WHEN correct = 1 THEN realized_pnl ELSE NULL END)
          * (excess_hr_weighted) AS avg_edge_usd_raw,
        -- profit_factor
        sum(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END)
          / nullif(abs(sum(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END)), 0)
          AS profit_factor,
        -- metadata
        median(abs(net_usd)) AS avg_commitment_usd,
        max(resolved_at) AS last_active
    FROM tag_positions
    GROUP BY trader
    HAVING n_positions >= 10
      AND excess_hr_weighted > 0
),
-- Step 2b: Per-window consistency
window_hrs AS (
    SELECT
        trader,
        floor(date_diff('day', resolved_at, CURRENT_DATE) / 60) AS window_id,
        avg(correct) - {tag_base_rate} AS window_excess_hr,
        count(*) AS window_n
    FROM tag_positions
    WHERE trader IN (SELECT trader FROM trader_metrics)
    GROUP BY trader, window_id
    HAVING window_n >= 3
),
consistency AS (
    SELECT
        trader,
        avg(window_excess_hr) / (stddev(window_excess_hr) + 0.05)
          * least(ln(1 + count(*)), 1.8) AS consistency_sharpe,
        count(*) AS n_active_windows
    FROM window_hrs
    GROUP BY trader
    HAVING n_active_windows >= 2
)
-- Step 3: Combine and rank
SELECT
    m.*,
    c.consistency_sharpe,
    c.n_active_windows,
    percent_rank() OVER (ORDER BY m.excess_hr_weighted) AS pct_hr,
    percent_rank() OVER (ORDER BY c.consistency_sharpe) AS pct_cons,
    percent_rank() OVER (ORDER BY m.avg_edge_usd_raw) AS pct_edge,
    percent_rank() OVER (ORDER BY m.profit_factor) AS pct_pf,
    0.45 * pct_hr + 0.25 * pct_cons + 0.20 * pct_edge + 0.10 * pct_pf AS composite
FROM trader_metrics m
JOIN consistency c ON m.trader = c.trader
ORDER BY composite DESC
```

---

## 12. Deployment Strategy

### 12.1 Vectorized Validation (Phase 1)

Before implementing the scorecard in the strategy, validate its predictive power vectorized:

1. Build scorecards from training-window data
2. Select top-K by composite
3. Compute consensus HR in test window
4. Compare to the current meh/mpe threshold selection

**Success criterion**: Top-K by composite produces >= 5pp higher excess HR than top-K by excess_hr alone, with comparable or higher signal count.

### 12.2 A/B Comparison (Phase 2)

Run two consensus strategies in parallel:
- Strategy A: current pool qualification (meh >= 10pp, mpe <= 0.80)
- Strategy B: scorecard-based pool (top-K by composite)

Same consensus trigger (N unique traders), same sizing, same tag filter. Compare HR, PnL, Sharpe across 3+ walk-forward folds.

### 12.3 Tick-by-Tick Validation (Phase 3)

Only after vectorized A/B shows improvement (Phase 2), run SyncReplayRunner tick-by-tick validation. Apply the standard 20-40pp discount to vectorized results.

### 12.4 Production Integration (Phase 4)

The scorecard becomes a FeatureProvider in the strategy framework:

```python
class ScorecardProvider(FeatureProvider):
    """Provides trader scorecards as features."""

    async def compute(self, backend: FeatureBackend, train_end_date: str):
        """Build scorecards from training data."""
        self.scorecards = build_scorecards(backend, train_end_date)
        self.pool = select_top_k(self.scorecards, tag=self.tag, k=self.k)

    def on_trade(self, trade: NormalizedTrade, ctx: InMemoryContext):
        """Update consensus tracking with scorecard-weighted votes."""
        if trade.maker in self.pool:
            weight = self.scorecards[trade.maker].composite
            ctx.features[f"consensus_{trade.condition_id}"] += weight
```

---

## 13. Open Questions for the Research Team

### 13.1 Weight Calibration

The composite weights (0.45/0.25/0.20/0.10) are proposed based on qualitative assessment of each metric's importance. Should they be:
- **Fixed** (current proposal): Simple, interpretable, no overfitting risk.
- **Calibrated from data**: Run a grid search over weight combinations in walk-forward. Risk: overfitting to training folds.
- **Learned**: Use logistic regression where the target is "did markets with consensus from top-K by this composite resolve YES?" Risk: model complexity, requires careful regularization.

**Recommendation**: Start fixed, measure sensitivity to +/- 0.10 perturbation of each weight. If the results are stable to perturbation, the weights are fine. If highly sensitive, investigate calibration.

### 13.2 Decay Parameter Selection

Is 90-day half-life optimal? Alternatives:
- 60-day (aggressive): Better for Esports (base rate swings fast), worse for Politics (too few data points per window).
- 120-day (conservative): Stabler estimates but slower regime adaptation.
- **Tag-specific decay**: Esports 60d, Sports 90d, Politics 120d. Adds one parameter per tag.

### 13.3 Multi-Outcome Market Handling

Markets with N outcomes have a structural NO base rate of (N-1)/N (see `pitfalls/multi_outcome_base_rate.md`). Should the scorecard adjust excess HR for multi-outcome markets separately?

**Proposal**: Tag the market as single-outcome vs multi-outcome. Compute excess HR using the appropriate base rate. This avoids the +10pp base rate inflation trap for traders who specialize in multi-outcome events.

### 13.4 Position-Level vs Trade-Level Scoring

The scorecard currently operates at position level (one observation per trader-market pair). Should it also track trade-level metrics?

- **Position-level** (current): Clean, each (trader, market) is one observation with binary outcome.
- **Trade-level**: Richer (captures entry timing, position building, partial exits) but noisy (10-30 trades per position dilute the signal). Our research found position-level is the correct counting unit.

**Recommendation**: Position-level for all scoring. Trade-level data only used for entry timing metadata (avg_entry_order) and commitment size (abs(net_usd)).

### 13.5 Score Persistence and Versioning

When the scoring formula changes (new weights, new metrics added):
- Do we recompute historical scores? (Expensive but ensures consistency.)
- Do we version the scores? (Score v1 = {weights}, Score v2 = {new_weights}.)
- Do we track score changes over time? (Useful for detecting gaming, but storage cost.)

**Proposal**: Version the formula. Store (trader, tag, formula_version, computed_at, composite). This allows A/B testing different formulas in production.

### 13.6 Interaction with Graduated Sizing

The graduated sizing plan (Track 5) proposed sustainability tiers (Tier 1-5) with sizing multipliers (2.0x to 0.25x). The scorecard composite replaces these discrete tiers with a continuous score.

**Integration**: The sizing multiplier becomes a function of composite:

```python
def sizing_multiplier(composite: float) -> float:
    """Map composite score to position size multiplier."""
    if composite >= 0.80:
        return 2.0  # top quintile
    elif composite >= 0.60:
        return 1.5
    elif composite >= 0.40:
        return 1.0  # baseline
    elif composite >= 0.20:
        return 0.5
    else:
        return 0.25  # bottom quintile (barely qualifying)
```

Or continuous: `multiplier = 0.25 + 1.75 * composite` (linear from 0.25x to 2.0x).

---

## 14. Summary

### What we are building

A per-trader, per-tag quantitative profile with four scored metrics (excess HR, consistency, edge, profit factor), gated by minimum data requirements, combined via percentile-ranked weighted composite.

### Key design decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Composition | Tiered gate + weighted composite | Non-compensatory gates + smooth ranking |
| Normalization | Percentile rank within tag cohort | Non-parametric, robust, bounded |
| Tag strategy | Tag-specific primary, global fallback | Base rates vary 9-73%, pools differ structurally |
| HR metric | Excess over tag-specific base rate | Absolute HR is meaningless without base rate context |
| Decay | 90-day half-life (tag-adjustable) | Balances adaptiveness and stability |
| Conviction metric | OMIT in v1 | Contaminated by split mechanics, subsumed by consensus dedup |
| Striking score | REPLACE with avg_edge_usd | Contrarian thesis contradicted by data (high entry price = best signal) |
| Stability | RENAME to consistency_sharpe | Clarity + per-window excess HR for base rate correction |
| Minimum positions | 10 per tag | Tight enough for statistical meaning, loose enough for niche tags |

### Implementation priority

| Phase | What | Effort | Expected impact |
|-------|------|--------|-----------------|
| 1 | Vectorized scorecard computation (DuckDB) | 1-2 days | Baseline metrics for all traders in Esports/Tennis |
| 2 | A/B vs meh/mpe threshold | 0.5 day | Confirm scorecard selects better pool |
| 3 | Tick-by-tick validation | 1 day | Confirm edge survives execution |
| 4 | Production integration | 2-3 days | FeatureProvider + daily refresh |

### What is NOT in v1

- Conviction metric (omitted -- split contamination)
- Weighted consensus (v2 -- requires continuous weighting in strategy)
- Co-occurrence / independence weighting (v2 -- complex, marginal impact)
- Cross-tag portability (v2 -- needs more data on cross-tag predictiveness)
- Gaming resistance beyond basic filters (v2 -- only needed if scores become visible)
- Tag-specific decay rates (v2 -- start with uniform 90-day)
