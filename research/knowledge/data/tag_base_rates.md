# Tag-Specific Base Rates

> [!CRITICAL] A single global base rate (38% YES / 62% NO) is wrong for most tags.
> Tag-specific base rates vary from 9% YES (Elections) to 73% YES (Earnings).
> Any excess HR computation MUST use tag-aware base rates or results are meaningless.

## Base Rates by Tag (2025, excl. Up or Down, min 50 markets)

### Extreme YES bias (>50%)
| Tag | Markets | YES Rate |
|-----|---------|----------|
| Earnings | 612 | 72.9% |
| Earnings Calls | 765 | 58.8% |

### Balanced (~40-55%)
| Tag | Markets | YES Rate |
|-----|---------|----------|
| Tariffs | 140 | 47.9% |
| Sports (aggregate) | 227K | 40.1% |
| NBA | 38K | 46.3% |
| Esports | 46K | 45.8% |

### Moderate NO bias (25-40%)
| Tag | Markets | YES Rate |
|-----|---------|----------|
| Finance | 8.8K | 35.3% |
| Trump | 7.6K | 34.1% |
| Crypto | 35K | 28.9% |
| Science | 619 | 27.0% |
| Geopolitics | 3.9K | 27.0% |
| Trump Presidency | 2.8K | 27.7% |

### Strong NO bias (10-25%)
| Tag | Markets | YES Rate |
|-----|---------|----------|
| Politics | 18K | 23.2% |
| Economy | 2.7K | 23.4% |
| Weather | 11K | 14.1% |
| Culture | 21K | 11.7% |
| Tech | 4.3K | 12.4% |
| AI | 1.3K | 10.0% |

### Extreme NO bias (<10%)
| Tag | Markets | YES Rate |
|-----|---------|----------|
| Elections | 3.4K | 9.0% |
| Neg Risk | 12.8K | 9.1% |
| Music | 4.3K | 8.8% |
| Movies | 7.1K | 9.7% |
| Golf/PGA | 4.6K | 3.1% |

## Implications

1. **Excess HR must be tag-adjusted**: A trader with 50% HR on Elections is +41pp skilled.
   Same 50% HR on Earnings is -23pp below base — unskilled.
2. **NO-biased tags (Culture, Movies, Music)**: Trivial to get high NO HR by always betting NO.
   Must discount NO HR in these tags heavily.
3. **Earnings is exploitable**: 73% YES base rate means "always buy YES on earnings" is
   a viable base strategy before any trader filtering.
4. **Sports is near-balanced**: 40% YES makes it the least biased category, but edge is
   consumed by spread (previous research finding).

## Data Source

ClickHouse query joining `markets_resolved`, `markets`, `events`, `event_tags`, `tags`.
PG engine views — must pre-materialize for performance.

## Related
- `data/market_base_rates.md` — global base rates (outdated, use tag-specific instead)
- `data/period_base_rate_variance.md` — temporal variance within tags
- `signals/no_pool_contamination.md` — NO contamination driven by tag bias

## Tags
`base-rates`, `tags`, `resolution`, `critical`
