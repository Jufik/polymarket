# NO Trader Pool Contamination at Low Sample Sizes

> **TL;DR**: At min_positions=30, 44.8% of NO-qualified traders have HR >= 95%, mostly due to luck. The NO base rate (63.7%) makes small-sample high HR statistically common.

> [!WARNING]
> NO-direction trader pools at min_positions < 50 are heavily contaminated by lucky small-sample traders. Use min_positions >= 50 or apply Bayesian shrinkage.

## Finding

At min_positions=30, min_excess_hr=0.10 (S2 defaults):

| Direction | Pool | HR >= 95% | HR = 100% | Median positions at 100% |
|-----------|------|-----------|-----------|--------------------------|
| NO | 5,726 | 44.8% (2,563) | 19.5% (1,119) | 35 |
| YES | 5,969 | 12.3% (732) | 4.2% (248) | 54 |

With a 63.7% NO base rate, the probability of achieving 95%+ HR on 35 trials is approximately:
- P(wins >= 33 out of 35 | p=0.637) ~ 2% per trader
- With 100K+ traders in the universe, expect ~2,000 "lucky" traders to qualify
- The 1,119 at exactly 100% aligns with this -- these are noise, not skill

The YES pool is much cleaner because the 36.3% YES base rate makes high-HR flukes much rarer.

## Evidence

```sql
WITH qualified AS (
    SELECT
        lower(p.trader) AS trader,
        p.position AS direction,
        count(*) AS total,
        countIf(p.correct = 1) / count(*) AS hit_rate,
        countIf(p.correct = 1) / count(*) -
            if(p.position = 'YES', 0.3628, 0.6372) AS excess_hr
    FROM trader_positions_resolved AS p
    INNER JOIN markets AS m ON p.condition_id = m.condition_id
    WHERE p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= toDate(now()) - INTERVAL 6 MONTH
      AND m.question NOT LIKE '%Up or Down%'
    GROUP BY trader, p.position
    HAVING count(*) >= 30 AND excess_hr >= 0.10
)
SELECT
    direction,
    countIf(hit_rate >= 0.95) AS hr_above_95,
    countIf(hit_rate = 1.0) AS hr_100pct,
    count(*) AS pool_size,
    round(hr_above_95 / pool_size * 100, 1) AS pct_above_95
FROM qualified
GROUP BY direction
```

## Impact

- **S2 strategy**: Consider direction=YES only, or raise min_positions to 50+ for NO
- **Bayesian approach**: Apply shrinkage: `adjusted_hr = (wins + alpha) / (total + alpha + beta)` with direction-specific priors
- **General**: Any trader qualification based on hit rate must account for base rate and sample size
- **Pool size vs quality**: Raising min_positions from 30 to 50 cuts NO pool by 47% but removes mostly noise

## Related

- `data/market_base_rates.md` -- Base rates that enable NO contamination
- `data/period_base_rate_variance.md` -- Monthly base rate variation amplifies the problem
- `pitfalls/consensus_dedup.md` -- Another pool inflation source

## Tags

`trader-qualification`, `NO-direction`, `sample-size`, `contamination`, `Bayesian`, `signal-quality`
