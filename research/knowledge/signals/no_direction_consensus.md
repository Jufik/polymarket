# NO-Direction Consensus Strategy: Politics NO

> **TL;DR**: Politics NO K=100 N=2 is the first tick-validated NO-direction consensus strategy. +9.3pp excess HR over 73.6% NO base rate. Tick BEATS vectorized (+0.6pp), confirming genuine consensus signal rather than vectorized artifact. Slow capital recycler (54-day avg hold, Sharpe=0.55) — MARGINAL deployment candidate.

> [!WARNING]
> The 54-day average hold means capital is locked for extended periods. At $100/position and 347 fills over 8 months (~43 fills/month), maximum concurrent capital needed is 43 × 54d / 30d ≈ $7,740 continuously deployed. Returns are positive but Sharpe is low — monitor for deterioration carefully.

> [!TIP]
> Tick BEATING vectorized (+0.6pp) is unusual and meaningful. It confirms the signal fires before the broad market population has priced in the outcome — consensus among skilled NO traders is genuinely early information, not retroactive pattern matching.

## Key Results (Tick-by-Tick Validated, 2026-03-09)

| Metric | Value |
|--------|-------|
| Strategy | Politics NO K=100 N=2 |
| Test period | 2025-07-01 — 2026-03-01 (8 months) |
| Total fills | 347 |
| Tick HR | 83.0% |
| NO base rate (test period) | 73.6% |
| **Excess HR** | **+9.3pp** |
| Net PnL | $33,941.52 |
| Sharpe | 0.55 |
| Max drawdown | $1,068.96 |
| Profit factor | 6.75 |
| **Avg hold** | **1308h (54 days)** |
| % fills < 24h | 19.1% |
| Vectorized UB excess | +8.8pp |
| Tick vs vectorized | **+0.6pp (tick beats UB)** |

## What "Tick Beats Vectorized" Means

In every other strategy, tick underperforms vectorized because:
1. The strategy enters at the Nth trader's price (worse than the blended average)
2. Capital constraints limit concurrent positions
3. Fill model introduces slippage

Politics NO ticking +0.6pp ABOVE vectorized means: the consensus trigger fires at a moment where the market has not yet fully priced the NO outcome. The NO traders identified by the composite scorecard are genuinely early. When the Nth qualified NO trader enters, the market is still offering favorable NO prices — the vectorized average (which uses full position history including later entries) actually captures worse prices than the trigger.

## Pool Composition: NO Specialists

NO pools are fundamentally distinct from YES pools for the same tag:

| | Politics YES Pool | Politics NO Pool |
|-|------------------|-----------------|
| Pool size (K=100) | 100 traders | 100 traders |
| Jaccard overlap | 1.0 (self) | 0.031 (vs YES) |
| Interpretation | YES specialists | NO specialists |

**3.1% overlap means NO specialists are an almost entirely distinct population from YES specialists.** A trader who is skilled at predicting YES outcomes is not the same person as one who is skilled at predicting NO outcomes. This is consistent with the direction decomposition finding: 51% of traders are NO-skilled, 12.6% are YES-skilled, 3.3% are dual-skilled.

## Why Politics NO Works (Mechanism)

Politics markets are strongly NO-biased (23.2% YES base rate = 76.8% NO base rate). Most political predictions fail — "will X happen" usually resolves NO. However:

1. The raw NO base rate is 73.6-76.8% depending on the period
2. Near-certainty NO bets (already trading at 0.90+) are trivially correct
3. The BEH gate (bucket_excess_hr >= 0.02) removes traders who only bet on near-certainty NOs
4. After BEH filtering, remaining NO traders demonstrate genuine predictive edge in the 0.50-0.80 NO price range

The signal fires when N=2 qualified NO specialists enter a Politics market on the NO side. This consensus gate removes noise from individual traders making opportunistic bets.

## Capital Considerations

With 54-day average hold:
- **Capital efficiency**: very low. A Sports YES position (6.9h avg hold) recycles capital 188x faster for the same investment
- **Diversification value**: Politics NO resolves on different schedules than Sports/Crypto. Useful as a slow, high-conviction tail to a portfolio
- **Position size**: because Sharpe=0.55, keep position sizes conservative (10-20% of Sports YES allocation)
- **Compounding score**: `+9.3pp × $97.77 avg edge / 54 days ≈ 0.017 $/day` — well below Sports YES at `+30pp × edge / 0.29 days`

## NO Pool Building

Pool built using `build_politics_no_pool()` in `research/hypotheses/scorecard-v3-strategies/scripts/build_pools_v3.py`.

Key differences from YES pool building:
- `position = 'NO'` in all queries
- `correct = 1 when NO wins (yes_won = 0)` — already handled by `maker_positions.correct`
- `NO entry price = 1 - YES price` (from `yes_entry_data` when available, else `abs(net_usd)/net_no`)
- `NO base rate = avg(correct)` over all NO positions in training window
- BEH gate applied identically: bucket_excess_hr >= 0.02 before composite scoring

The `conviction_ratio >= 0.90` gate is omitted for NO pools (NO positions rarely have clean conviction signals from `net_usd/volume` due to split mechanics). This is intentional — requiring conviction would shrink the NO pool to near-empty.

## Vectorized Discovery Pattern

The vectorized sweep for NO signals uses identical structure to YES sweeps but with `position = 'NO'`:

```sql
WITH consensus_markets AS (
    SELECT
        condition_id, position,
        count(DISTINCT trader) AS n_qualified,
        max(first_trade) AS signal_entry,
        first(resolved_at) AS resolved_at,
        first(correct) AS market_correct
    FROM maker_positions_resolved_corrected p
    JOIN no_pool q ON p.trader = q.trader
    WHERE p.position = 'NO'
      AND toDate(p.resolved_at) >= '{test_start}'
      AND toDate(p.first_trade) >= '{test_start}'  -- phantom signal filter
    GROUP BY condition_id, position
    HAVING n_qualified >= 2
)
SELECT
    count(*) AS n_signals,
    avg(market_correct) AS hr,
    median(date_diff('day', signal_entry, resolved_at)) AS hold_days
FROM consensus_markets
```

## Strategy Implementation

Use `TokenMapStrategy` with `direction_filter="NO"`:

```python
from research.strategies.consensus_v2 import TokenMapStrategy
from research.hypotheses.scorecard_v3_strategies.scripts.build_pools_v3 import build_politics_no_pool

pool, tag_markets, gambling_markets = build_politics_no_pool(k=100)
strategy = TokenMapStrategy(
    name="politics_no_k100_n2",
    pool=pool,
    tag_markets=tag_markets,
    gambling_markets=gambling_markets,
    n_threshold=2,
    token_map=token_map,
    direction_filter="NO",  # fire only on NO consensus
    size_usd=100.0,
)
```

Direction is determined by asset_id lookup in `token_map`: if the Nth qualifying trader's asset is the NO token for that condition_id, the direction is NO.

## Related

- `signals/composite_scorecard.md` — composite scoring (same system, applied to NO direction)
- `signals/edge_weighted_skill.md` — BEH gate mechanism and direction decomposition
- `data/tag_base_rates.md` — Politics NO base rate 73.6-76.8% (varies by period)
- `pitfalls/vectorized_vs_tick.md` — why tick beating vectorized is a positive signal
- `pitfalls/sell_is_exit.md` — SELL trades excluded from NO consensus (BUY NO only)

## Tags

`consensus`, `no-direction`, `politics`, `tick-validated`, `pool-building`, `slow-capital`, `beh-gate`
