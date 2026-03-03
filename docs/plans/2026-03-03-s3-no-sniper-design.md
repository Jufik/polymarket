# S3 NO Sniper — First-5-Minute Base Rate Strategy

## Context

Per-tag analysis revealed that markets are systematically mispriced at creation.
In the first 5 minutes of a market's life, YES prices don't reflect tag-specific
base rates. Smart money corrects this within 5-15 minutes.

### Discovery

1. **Pure base-rate play (buy NO anytime)**: Negative EV. Market already prices in
   tag-specific base rates when averaged over the full market lifetime.
2. **Entry timing analysis**: First 5 minutes show dramatically higher NO win rates
   and positive EV for specific tags.

### First-5-Minute Edge (per-market, YES 0.15-0.50 zone, post-Jun 2025)

| Tag      | Markets | NO Win Rate | Raw EV   | After 6c spread | USD Volume |
|----------|---------|-------------|----------|-----------------|------------|
| Tech     | 370     | 83.5%       | +$0.185  | +$0.125         | $551K      |
| Trump    | 2,467   | 79.8%       | +$0.179  | +$0.119         | $5.7M      |
| Economy  | 576     | 75.9%       | +$0.122  | +$0.062         | $3.5M      |
| Culture  | 846     | 75.4%       | +$0.030  | -$0.030         | $286K      |
| Crypto   | 74,563  | 52.5%       | -$0.107  | -$0.167         | $9.3M      |

### Edge Decay

| Tag     | 0-5m EV  | 5-15m EV | Decay     |
|---------|----------|----------|-----------|
| Trump   | +0.179   | -0.163   | -34pp     |
| Tech    | +0.185   | -0.042   | -23pp     |
| Economy | +0.122   | -0.036   | -16pp     |

## Strategy Design

### Signal

Buy NO when a trade arrives in an eligible market within 5 minutes of market
creation, with YES price in the 0.15-0.50 zone.

### Decision Flow

```
Trade arrives
  → condition_id in tag_map? (tag must be eligible)
  → market age < max_market_age_minutes?
  → YES price in [min_yes_price, max_yes_price]?
  → not already positioned in this market?
  → BUY NO at max_price = (1 - yes_price) + spread_buffer
```

### Config

```python
@dataclass(frozen=True)
class S3Config:
    eligible_tags: frozenset[str] = frozenset({"Tech", "Trump", "Economy"})
    min_yes_price: float = 0.15
    max_yes_price: float = 0.50
    max_market_age_minutes: int = 5
    spread_buffer: float = 0.02
    position_size_usd: float = 10.0
```

### Position Management

- Fixed position size ($10 default)
- Hold to resolution (binary outcome, no active management)
- One position per market (dedup by condition_id)
- No FeatureProvider needed — pure price + tag + timing check

### Excluded Tags (with reasoning)

- **Crypto**: No timing edge (52.5% NO WR even at 0-5m). Market efficient immediately.
- **Culture**: Edge too thin (+0.030), eaten by spread.
- **Elections**: Negative EV at all timing windows.
- **Finance**: Negative EV, base rate doesn't translate to timing edge.
- **Music, Movies, AI**: Small samples, mostly negative EV.

## Files

| File | Purpose |
|------|---------|
| `research/strategies/s3_no_sniper.py` | Strategy + config |
| `research/strategies/s3_data.py` | Data loading (tag map, market births, trade pre-filter) |
| `research/scripts/s3_no_sniper_validation.py` | Walk-forward sweep |
| `research/tests/test_s3_strategy.py` | Unit tests |

## Risks

1. **First-trade detection in live**: Need CLOB WS market creation events or
   fast Gamma API polling to know when a market opens.
2. **Liquidity at open**: Early markets may have wide spreads. RealisticFillSimulator
   captures this in backtest.
3. **Tag availability**: Tags must be assigned at market creation. If they arrive
   later via Gamma API sync, we miss the window.
4. **Regime change**: Political/economic tags have regime-dependent base rates.
   Tech base rate (87.6% NO) may shift with market conditions.
