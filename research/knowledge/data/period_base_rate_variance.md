# Period-Specific Base Rate Variance

> **TL;DR**: Monthly YES/NO base rates vary from 20% to 45% YES. Using a fixed 36% YES base rate can cause 15pp+ error in excess HR estimation.

> [!WARNING]
> A strategy showing positive excess HR at fixed base rates may have zero or negative edge in months with extreme base rate skew. Always compute period-specific base rates.

## Finding

Analyzing base rates by month (excluding "Up or Down" gambling markets), the YES win rate varies dramatically:

| Period | YES Base | NO Base | Note |
|--------|---------|--------|------|
| 2025-04 | 36.3% | 63.7% | Normal |
| 2025-07 | 20.4% | 79.6% | Extremely skewed |
| 2025-10 | 35.6% | 64.4% | Normal |
| Overall | 36.3% | 63.7% | Long-run average |

July 2025 had only 20.4% YES wins — meaning the NO base rate was 79.6%. A NO strategy with 80% HR in July has only 0.4pp excess, not the 16pp you'd compute using the fixed 63.7% base rate.

This variance can cause large negative PnL in individual months despite a "high" overall HR.

## Evidence

```sql
SELECT
    toStartOfMonth(resolved_at) AS month,
    countIf(token_won = 1 AND outcome = 'YES') AS yes_won,
    countIf(token_won = 1 AND outcome = 'NO') AS no_won,
    round(yes_won / greatest(yes_won + no_won, 1) * 100, 2) AS yes_pct
FROM markets_resolved AS mr
INNER JOIN markets AS m ON mr.condition_id = m.condition_id
WHERE mr.resolved_at > '1970-01-02'
  AND m.question NOT LIKE '%Up or Down%'
  AND m.question NOT LIKE '%up or down%'
GROUP BY month
ORDER BY month
```

## Impact

- **Excess HR computation**: Must use period-specific base rates, not fixed overall rates
- **Walk-forward backtests**: Compute base rate from the training window and/or the test window
- **PnL estimation**: A strategy can have positive excess HR but negative PnL if the month's base rate is extreme
- **NO strategies most affected**: NO base rate swing from 64% to 80% is a 16pp range

## Related

- `data/market_base_rates.md` -- Overall base rates (this entry adds time-varying context)
- `data/tag_base_rates.md` -- Tag-specific base rates (orthogonal dimension of variance)
- `pitfalls/vectorized_vs_tick.md` -- Vectorized uses resolved positions, affected by base rate swings

## Tags

`base-rate`, `time-varying`, `seasonality`, `PnL-risk`, `data-quality`
