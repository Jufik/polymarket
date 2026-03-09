# In-Play Elite Traders: Population, Behavior, and Copyability

> **TL;DR**: 1,546 traders have ≥80% HR on ≥50 in-play positions (<4h hold). They are persistent OOS (96% maintain ≥70% HR), trade non-gambling markets (79.4%), and lead the broader market by 58 minutes median. Their edge is real but concentrated in long-shots (<0.30 entry) — the sure-thing segment (0.99) is structural, not skill.

> [!CRITICAL]
> Elite traders at 0.99 entry add only +0.30pp over population. Anyone buying at 0.99 gets 99.46% HR — this is market structure (underpriced certainty), not trader skill. Do NOT build a pool-based strategy for the 0.99 segment. The genuine elite alpha is in long-shots (<0.30): elite HR 24% vs population 3.2% = +13.6pp edge over population, +10.2pp over break-even.

> [!CRITICAL]
> These traders LEAD by 58 minutes median. A consensus-style copy strategy (wait for N traders) does not work — they enter before everyone else. Copying requires real-time wallet monitoring via trades.raw or pending.signal Kafka topics. Only 11.2% of their positions overlap with the broader pool's timing.

> [!WARNING]
> In-play is NOT contamination for copy strategies. It is the signal itself. The "contamination" framing only applies when measuring predictive alpha (can we predict before the event?). For RT copy strategies (can we replicate the trade within seconds?), in-play traders with existing live infrastructure are directly exploitable.

## Population Characteristics

- **1,546 traders** meeting criteria (≥50 in-play positions, ≥80% HR, median vol ≥$5)
- **$1.08B** total in-play volume, **$136** median position size
- **79.4%** also trade non-gambling markets (skill is general)
- Non-gambling HR by tag: Sports 97.2%, Soccer 97.9%, Esports 96.7%, Politics 90.7%
- **Persistent OOS**: 96% of train-active traders maintain ≥70% HR in test (train < 2025-07-01)

## Alpha by Entry Price (Jan 2026 Tick-Validated)

| Entry Price | Elite HR | Population HR | Elite Edge over Pop | Elite Alpha over BE |
|-------------|----------|---------------|--------------------|--------------------|
| 0.99 | 99.75% | 99.46% | +0.30pp | +0.75pp |
| 0.95-0.97 | 93.1-94.8% | 96.7-96.9% | negative | negative |
| 0.85-0.90 | 91.4% | 93.7% | ~0pp | +4.9pp |
| 0.00-0.30 | 24.0% | 3.2% | **+13.6pp** | **+10.2pp** |

The 0.95-0.97 zone is a dead zone — elite traders underperform population there.

## Tick-Validated Performance (Top-100 Pool, Jan 2026)

| Metric | Value |
|--------|-------|
| Fills | 15,891 |
| HR | 94.2% |
| PnL | +$52,932 |
| Tick degradation | 3pp (vs 20-40pp for consensus strategies) |
| Median hold | 2.5h (P90: 20h) |

PnL breakdown: $41K from long-shots (<0.30), $9K from sure-things (0.99), mid-range roughly flat.

## Evidence

- Discovery: `research/hypotheses/in-play-traders/discovery/track_a_results.md`
- Validation: `research/hypotheses/in-play-traders/validation/elite_whale_copy_results.md`
- Ledger: `research/output/ledger_ewc_k100_nop.parquet`

## Related

- `pitfalls/in_play_contamination.md` — in-play framing for predictive strategies (opposite conclusion)
- `signals/composite_scorecard.md` — the pre-event prediction strategy (complementary)
- `data/market_base_rates.md` — 0.99 structural alpha is a base-rate phenomenon

## Tags

`in-play`, `elite-traders`, `whale-copy`, `real-time`, `wallet-monitoring`, `long-shot`, `structural-alpha`
