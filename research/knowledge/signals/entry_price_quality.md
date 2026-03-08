# Entry Price Quality: Calibration Gap, Not Cheap Entry

> **TL;DR**: "Buy cheap = skilled" is wrong on Polymarket. Markets are well-calibrated (entry price ≈ resolution probability), so cheap buyers have low HR by construction. The correct signal is **calibration gap** (`hit_rate - avg_entry_price`) or **bucket excess HR** — does the trader beat the market's implied probability at whatever price they enter?

> [!CRITICAL]
> Never use raw entry price, avg_payoff_when_correct, or cheap_entry_ratio as positive quality signals. They are anti-correlated with HR (IC = -0.49 to -0.80). Polymarket calibration makes these tautological price-level proxies. Only bucket_excess_hr or calibration_gap isolate genuine skill.

> [!WARNING]
> The "sure-thing penalty" hypothesis is INVERTED. Traders with high sure_thing_ratio (>70% of wins at entry > 0.80) have +13.6pp excess HR — they correctly identify near-certainties. However, their calibration_gap is -6.7pp (they underperform implied probability). Penalize calibration_gap < -5pp, not high entry prices.

## Finding

Across 8.9M positions and 55,623 traders:
- Population HR tracks entry price perfectly (6.1% at 0-5¢, 97.6% at 95-99¢)
- Cheap buyers (avg entry < 0.30): 43.6% HR, +13.8pp calibration gap (genuine alpha)
- Sure-thing pilers (avg entry > 0.75): 68.4% HR, -6.7pp calibration gap (negative alpha)
- bucket_excess_hr: IC = +0.918 with HR (controls for price level)
- calibration_gap (= avg_edge): OOS IC = +0.082 (weak but positive)

## Recommended Metrics

```
bucket_excess_hr = weighted_avg(trader_bucket_hr - population_bucket_hr)  # PRIMARY
calibration_gap = hit_rate - avg_entry_price                              # FALLBACK
```

Hard exclusion gates:
- avg_entry > 0.85 AND bucket_excess_hr < +2pp → no alpha
- calibration_gap < -5pp AND n_positions ≥ 50 → chronic overpayer

## Related

- `signals/hr_persistence.md` — HR remains primary signal (IC=0.744)
- `signals/stability_bonus.md` — stability is secondary (r=+0.498)
- `pitfalls/direction_decomposition.md` — must decompose YES/NO when analyzing entry prices

## Tags

`entry-price`, `calibration`, `bucket-excess-hr`, `scorecard`, `critical`, `methodology`
