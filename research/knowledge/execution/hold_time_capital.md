# Hold Time Determines Capital Efficiency

> **TL;DR**: Long-dated markets (politics 22d, crypto 11d) consume position slots for weeks. Sports (1.4d) and esports (0.3d) are 10-50x more capital-efficient.

> [!WARNING]
> Without `max_hold_hours` filtering, the first 50 fills (often long-dated) lock all capital for weeks. Always add a hold-time gate.

> [!TIP]
> Consider position sizing inversely proportional to expected hold time: `size = base_size * (target_hold / expected_hold)`.

## Finding

With N max concurrent positions, throughput = N / avg_hold_time. Market categories have vastly different hold times:

| Category | Median Hold | Avg Hold | Throughput (50 slots) |
|----------|------------|----------|----------------------|
| Esports | 0.3d | 0.4d | 167/day |
| Sports | 0.5d | 1.4d | 36/day |
| Crypto | 3.7d | 11.0d | 5/day |
| Other | 4.1d | 9.3d | 5/day |
| Politics | 12.3d | 22.4d | 2/day |

A single politics position blocks a slot for 22 days — the same slot could serve 73 esports positions.

In tick-by-tick simulation without hold-time filtering: peak concurrent = ~2,816 positions vs 50 max slots = capital starvation. The first 50 fills (often long-dated) lock everything.

## Evidence

```sql
SELECT
    cat,
    round(quantile(0.5)(hold_days), 1) AS med_hold,
    round(avg(hold_days), 1) AS avg_hold,
    count(*) AS n
FROM _tmp_s1d_oos_positions
WHERE pos IN ('YES','NO') AND train_hr >= 0.75
GROUP BY cat ORDER BY med_hold
```

## Impact

- **Strategy**: Add `max_hold_hours` parameter. Skip markets where `closed_at - now > threshold`
- **Vectorized**: Must weight by hold time, not just count positions
- **Sizing**: Consider position size inversely proportional to expected hold time
- **Capital planning**: At $10/bet with 50 slots: sports-only = $360/day throughput, politics = $20/day

## Related

- `execution/position_settlement.md` — Settlement frees capital; hold time determines how long slots are blocked
- `pitfalls/vectorized_vs_tick.md` — Capital constraint is Gap #3 in vectorized vs tick divergence

## Tags

`hold-time`, `capital-efficiency`, `category`, `execution`, `position-lifecycle`
