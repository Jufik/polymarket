# S2 Insider Copy Strategy

Copy trades from traders with abnormally high hit rates on insider-susceptible markets.

## Quick Start

```bash
# 1. Apply CH migration (if not already)
cat docker/clickhouse/migrations/007_market_susceptibility.sql | \
  clickhouse-client --host 192.168.0.148 --port 19000 -d polymarket --multiquery

# 2. Run paper trading
uv run pm-strategy run --config configs/s2_insider_copy.toml --log-dir logs/paper
```

## Required Dependencies

| Dependency | Source | Migration |
|-----------|--------|-----------|
| `market_susceptibility` VIEW | Tag chain classification | `007_market_susceptibility.sql` |
| `trader_positions_resolved` VIEW | Trader PnL + resolution | `005` + `006` migrations |
| `trader_market_positions` table | SummingMergeTree | `005` + `006` migrations |
| `markets`, `events`, `event_tags`, `tags` | PG-replicated | init.sql (PG engine) |
| Kafka `trades.raw` | Live pipeline | pm-live |
| Kafka `orderbooks.raw` | CLOB WS | pm-live |

## Validated Results

| Config | HR | PnL/3mo | Kelly EV | Compounding |
|--------|-----|---------|----------|-------------|
| C>=3, price<0.65 | 57.3% | +$784K | +$1.57/$1 | 12.37 |
| C>=5, price<0.65 | 59.0% | +$698K | +$1.81/$1 | 12.14 |
| C>=2, no filter | 66.8% | +$254K | **NEGATIVE** | 7.01 |

**Entry price filter is mandatory.** Without it, Kelly expectation is negative.

## Two-Pool Deployment

The TOML config runs two instances sharing one provider:

| Pool | Categories | Hold Strategy | Capital |
|------|-----------|---------------|---------|
| `s2_insider_fast` | esports | Hold to resolution | 30% |
| `s2_insider_slow` | politics, culture, finance | Take-profit at 3%/day | 70% |

## Sizing (Quarter-Kelly)

| Bankroll | Size/position | Max concurrent | Expected PnL/mo |
|----------|--------------|----------------|-----------------|
| $500 | $56 | 8 | ~$850 |
| $1,000 | $113 | 8 | ~$1,700 |
| $5,000 | $563 | 8 | ~$8,500 |

Bottleneck is concurrent positions (8 slots, ~25d avg hold), not signal frequency.

## Future Work

- **Gliding stop-loss**: trailing stop that tightens as position profits
- **Exit on insider reversal**: sell if pool members start SELLing
- **Feature simplification**: F1 (HR excess) alone has 3x correlation vs other features
