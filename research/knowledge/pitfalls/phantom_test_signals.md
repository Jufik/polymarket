# Phantom Test Signals: first_trade Filter in Test Window

> [!CRITICAL]
> Without `first_trade >= test_start` in the market-level aggregation, traders who entered a
> market during training contribute phantom signals when that market resolves in the test window.
> These entries are NOT copyable — they happened before the test period started. This inflates
> HR by up to 12pp and signal count by ~32%. ALWAYS add this filter in vectorized sweeps.

## Explanation

In a walk-forward sweep, the test window selects markets where `resolved_at` falls in the test
period. But a trader who entered a market during the training period may appear in those markets
because the market resolved during the test period. That trader's entry is pre-test — a copy
strategy could not have acted on it.

Including these phantom entries overstates signal quality because:
1. Training-period insiders may have entered at genuinely better prices (early information)
2. Their entries inflate the qualified trader count per market
3. Their markets inflate HR because they self-selected into markets they had strong priors on

## Data / Evidence

From discovery R2 vs R3 comparison (2026-03, 2025-07 fold, all tags):
- Phantom positions: 69,477 of 217,895 total = 31.9%
- YES positions specifically: same proportion

R2 vs R3 delta per tag:
| Tag | R2 HR | R3 HR | HR Delta | R2 CS | R3 CS | CS Delta |
|-----|-------|-------|----------|-------|-------|----------|
| Esports | 79.6% | 67.2% | -12.4pp | 73.70 | 34.87 | -52.7% |
| Tennis | 46.7% | 72.4% | +25.7pp | 10.94 | 9.67 | -11.6% |
| 1H | 79.8% | 78.0% | -1.8pp | 22.07 | 19.71 | -10.7% |

Tennis reversed direction: R2 HR was contaminated by low-quality pre-test traders who diluted
the signal. After filtering to only copyable test-period entries, fresh test-period signal
quality is actually higher. Esports lost 12.4pp because phantom early-mover entries were
higher quality (genuine insiders from training).

## Required SQL Pattern

```sql
CREATE OR REPLACE TABLE _tmp_thr_mkt_buy ENGINE = Memory AS
SELECT t.condition_id, any(t.yes_won) AS yes_won,
       dateDiff('hour', max(t.first_trade), any(t.resolved_at)) AS hold_hours,
       uniqExact(t.trader) AS n_qualified
FROM maker_positions_resolved_corrected t
JOIN _tmp_thr_qual_buy q ON t.trader = q.trader
WHERE t.condition_id IN (SELECT condition_id FROM _tmp_thr_tag_mkts)
  AND t.position = 'YES'
  AND toDate(t.resolved_at) >= '{test_start}'
  AND toDate(t.resolved_at) < '{test_end}'
  AND toDate(t.first_trade) >= '{test_start}'   -- CRITICAL: only copyable entries
GROUP BY t.condition_id
HAVING n_qualified >= {consensus}
```

## Related

- `pitfalls/training_window_lookahead.md` — related look-ahead in tick-by-tick providers
- `pitfalls/vectorized_counting_unit.md` — market-level aggregation pattern

## Tags

`phantom-signals`, `look-ahead`, `first-trade-filter`, `walk-forward`, `vectorized`, `CRITICAL`
