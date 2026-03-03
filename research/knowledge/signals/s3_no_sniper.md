# S3 NO Sniper — First-5-Minute Base Rate Strategy

> [!TIP] Economy+Tech at max_yes_price=0.50 is the optimal config. Tighter price zones
> increase HR but kill payoff ratio — counterproductive.

## Signal

Buy NO on newly created markets in high-NO-bias tags during the first 5 minutes of
market life. The edge comes from initial mispricing: YES prices at creation don't
reflect tag-specific NO base rates. Smart money corrects within 5-15 minutes.

## Discovery Data

### First-5-minute timing edge (vectorized, per-market EV)

| Tag | 0-5 min NO WR | 5-15 min NO WR | Decay |
|-----|---------------|-----------------|-------|
| Tech | 83.5% | 67.1% | -16.4pp |
| Trump | 79.8% | 45.9% | -33.9pp |
| Economy | 75.9% | 56.3% | -19.6pp |
| Crypto | 48.9% | 47.2% | -1.7pp (no edge) |

### Price zone analysis (vectorized, per-market)

| Tag | YES 0.15-0.20 | YES 0.20-0.30 | YES 0.30-0.40 | YES 0.40-0.50 |
|-----|:---:|:---:|:---:|:---:|
| Economy NO WR | 74.6% | 77.0% | 62.5% | 54.3% |
| Economy EV/$10 | $3.95 | $3.99 | $1.60 | $0.38 |
| Tech NO WR | 83.8% | 72.6% | 61.1% | 74.5% |
| Tech EV/$10 | $5.46 | $3.37 | $1.64 | $2.48 |

> [!WARNING] Vectorized per-market EV overstates tick-by-tick performance. Lower YES price
> buckets look best in isolation but in tick-by-tick, tighter zones reduce avg win payout
> more than they improve HR.

## Tick-by-Tick Validation Results (3 OOS periods: Jul-25, Oct-25, Jan-26)

### Config comparison

| Config | Fills | HR | PnL | Avg/bet | Sharpe | PF |
|--------|------:|----:|----:|--------:|-------:|---:|
| **Economy+Tech mp0.50** | **366** | **77.0%** | **+$213** | **$0.77** | **0.43** | **1.33** |
| Economy mp0.50 (baseline) | 289 | 77.3% | +$136 | $0.62 | 0.38 | 1.27 |
| Economy+Tech mp0.30 | 273 | 81.2% | +$1 | $0.00 | 0.00 | 1.00 |
| Economy mp0.35 | 247 | 79.8% | -$2 | -$0.01 | -0.01 | 1.00 |

### Best config detailed stats (Economy+Tech, mp0.50)

- **Per-period**: Jul +$84 (79.7% HR), Oct +$104 (75.6% HR), Jan +$26 (76.7% HR)
- **Avg win**: $4.04, **Avg loss**: -$10.17
- **NO entry price**: p10=0.520, p50=0.740, p90=0.850
- **Hold duration**: p10=2.0d, p50=27.4d, p90=70.4d
- **Max drawdown**: $96.98
- **Equity curve**: steady upward, peaked +$245

### Per-bet economics

- $0.77 profit per $10 bet ($0.58 after fees)
- ~122 bets/month across 3 periods
- ~$70/month on $1,000 capital (8.4% monthly)
- Profit factor 1.33 (every $1 lost generates $1.33 in wins)

## Key Findings

> [!CRITICAL] Tighter price zones are COUNTERPRODUCTIVE in tick-by-tick. Restricting
> max_yes_price from 0.50 to 0.30 increases HR (81.2% vs 77.0%) but reduces avg win
> from $4.04 to $2.37 — a 41% payoff cut that wipes out the HR gain. The edge is in
> the timing (first 5 minutes), not price selectivity.

> [!WARNING] Trump tag collapses in tick-by-tick despite 79.8% vectorized NO WR.
> Tick-by-tick shows 53-60% HR, deeply negative PnL. Excluded from final config.

> [!TIP] Adding Tech to Economy increases fills by 27% (+77 fills) with identical HR
> (77.0%). Tech markets have slightly better payoffs (avg win $4.04 vs $3.79).

### Stop loss analysis

Stop losses are counterproductive for this strategy:
- Both winners (96.8%) and losers (100%) reach YES > 0.50 at some point
- Timing of adverse moves is identical for winners and losers
- Any price-based or time-based stop loss cuts winners and losers at the same rate

### Activity signal

Markets with 4-10 early trades have 78.3% NO WR vs 68.3% for 1-3 trades,
but only 60 markets qualify (vs 375). Too restrictive to be useful.

### Resolution speed

No clear win-rate pattern across resolution speed buckets (62-77%).
Cannot predict or filter by expected resolution time.

## Recommended Config

```python
S3Config(
    eligible_tags=frozenset({"Economy", "Tech"}),
    min_yes_price=0.15,
    max_yes_price=0.50,
    max_market_age_s=300,  # 5 minutes
    spread_buffer=0.02,
    position_size_usd=10.0,
)
```

## Excluded Tags (with reasoning)

- **Trump**: 79.8% vectorized → 53-60% tick. Deeply negative PnL.
- **Crypto**: 48.9% NO WR in first 5 min. No timing edge (efficient from start).
- **Sports/Weather/Games**: Not tested, different dynamics (event-based, not opinion-based).

## Related

- `pitfalls/vectorized_vs_tick.md` — general vectorized optimism
- `data/market_base_rates.md` — overall 62% NO base rate
- `execution/hold_time_capital.md` — median 27d hold blocks capital
- `signals/tag_edge_analysis.md` — per-tag base rate analysis

## Tags

`strategy` `base-rate` `timing` `NO` `Economy` `Tech` `first-5-min` `tick-validated`
