# Gambling Market Taxonomy: 29.4% of Markets, 56% of Positions

> **TL;DR**: 169K markets (29.4%) are gambling (crypto updown, price levels). They generate 56% of all positions but only 13% of USD volume. Must exclude from trader scorecards.

> [!CRITICAL]
> Any position-count-based metric (trade count, signal count, consensus count) is 56% contaminated by gambling markets unless filtered. Always apply gambling exclusion before computing trader scorecards. Simple filter: `slug NOT LIKE '%updown%' AND NOT LIKE '%up-or-down%'` catches 97% of gambling positions.

> [!WARNING]
> Do NOT use `higher`/`lower` slug patterns as gambling signals — 95%+ false positives ("will unemployment be lower", "lower 48 states"). Only `updown`, `up-or-down`, and crypto-specific `above`/`below` are reliable.

## Finding

Three gambling subtypes:
1. **Updown** (156K markets): `btc-updown-5m-*`, `bitcoin-up-or-down-*` — 5m/15m/4h crypto price direction
2. **Crypto price levels** (12.7K): `bitcoin-above-98k-*`, `eth-below-3k-*`
3. **Multistrike** (~26K): `btc-multistrike-4h-*` — only catchable via `Crypto Prices` tag

Key numbers:
- Gambling YES base rate: 49.4% (near-random) vs Informational: 31.5%
- Median position size: $7.50 (gambling) vs $14.04 (informational)
- 158K traders are gambling-only; 171K participate in both; 636K info-only
- 50% of top-tier informational traders also participate in gambling

## Evidence

Full taxonomy in `research/hypotheses/trader-scorecard/discovery/gambling_market_taxonomy.md`.

## Impact

- **Scorecard**: Exclude gambling positions before computing any metric
- **Consensus**: Gambling-only traders (158K) automatically excluded
- **Volume**: Only 13% of USD excluded — minimal economic impact
- **Good traders**: Must exclude even for top-tier traders (50% crossover rate)

## Related

- `pitfalls/category_column_null.md` — never use markets.category, use slug patterns or event_tags
- `data/tag_base_rates.md` — gambling base rates differ fundamentally from informational

## Tags

`gambling`, `filter`, `market-classification`, `updown`, `scorecard`, `critical`
