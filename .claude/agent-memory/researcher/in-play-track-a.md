# In-Play Traders: Track A Results (2026-03-07)

## Key Numbers (UPPER BOUNDS — vectorized)

**Elite Pool**: 1,546 traders with >=50 in-play (<4h) positions, >=80% HR, >=\$5 median vol
- 319 (20.6%) pure gambling only
- 1,227 (79.4%) also trade non-gambling markets
- 1,112 (71.9%) strong non-gambling signal (>=5 pos, >=80% HR_NG)
- Average HR: 94.2%, median position size: $136
- Total in-play volume: $1.08 billion

**Non-Gambling HR by Tag**: Sports 97.2%, Games 97.2%, Soccer 97.9%, Basketball 97.6%, NCAA 98.2%, Weather 98.4%, Esports 96.7%, Crypto 96.6%, Politics 90.7%

**Persistence** (train pre-2025-07, test post-2025-07):
- 155/171 train-active traders active in test (79.1%)
- 124/155 maintain >=70% HR (96%)
- 94/155 maintain >=90% HR (61%)

**Reference trader 0x751a**: 14,505 positions, 99.73% HR, $551 med vol, $111.9M total vol. Train HR 99.5%, test HR 99.74%. 45% gambling, 55% non-gambling.

## Critical Finding: Elite Traders LEAD the Market

- Median gap: -58 min (elite enters 58 min BEFORE last pool trader)
- 48.5% of positions: elite entered >1h BEFORE pool
- Only 6.3% of positions: elite entered AFTER pool (copyable by watching pool)
- HR is highest when elite enters earliest (98.8% for >1h before pool)

**Implication**: Copy strategy must monitor elite wallets in real-time, not follow pool consensus.

## Top Copyable Traders (CopyScore = test_HR × sqrt(test_N) × (1-gamble_frac))

1. 0x336151559e: 9,644 pos, 99.95% HR, 11.8% gambling — CopyScore 8,654
2. 0x2c45f2be0c: 8,121 pos, 99.98% HR, 4.4% gambling — CopyScore 8,614
3. 0x7846e489e1: 7,875 pos, 99.96% HR, 15.3% gambling — CopyScore 7,510
4. 0xfc25f141ed: 5,718 test pos, 99.83% test HR, 5.6% gambling — CopyScore 7,125
5. 0x751a2b86ca: 13,945 test pos, 99.74% test HR, 45.2% gambling — CopyScore 6,460

## Gambling Market Classification

Pattern: `up-or-down`, `above-or-below`, `higher-or-lower`, `will-bitcoin-`, `will-btc-`, `will-eth-`, `-1h-`, `-24h-` in slug.
Identified: 43,720 / 574,524 markets (7.6%).

## DuckDB Latency Pattern

Binder error: cannot use outer-query alias in subquery FROM clause.
Fix: pre-compute last_non_elite_entry as separate table, then JOIN.
```python
con.execute("CREATE OR REPLACE TABLE _last_non_elite_entry AS SELECT ...")
# Then JOIN this table — do NOT use it as inline subquery
```

## Strategy Recommendations

**Tier 1**: Real-time wallet monitoring of top-20 copy-ranked traders. Immediate copy on new entry. Filter: YES price 0.15-0.85, not pure gambling market.
**Tier 2**: Consensus trigger (N>=3 elite traders in same market within 30 min) — smaller subset but consensus reduces need for real-time monitoring.
**Skip**: Markets where price <0.10 or >0.90 (contamination zone), <15 min remaining (too late to copy).

## Files

- Scripts: `research/hypotheses/in-play-traders/scripts/track_a_ultra_hr.py`, `track_a_latency_persist.py`
- Results: `research/hypotheses/in-play-traders/discovery/track_a_results.md`
- Status: GO for tick validation
