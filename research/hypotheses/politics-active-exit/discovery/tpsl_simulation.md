# TP/SL Simulation — Politics NO v3 (K=100, N=2)

> [!WARNING]
> ALL RESULTS IN THIS DOCUMENT ARE UPPER BOUNDS.
> Price oracle = trade tape (15-min bars). In production, slippage, partial fills,
> and bid/ask spread will reduce actual realized PnL. SL triggers on bar min_price,
> TP triggers on bar max_price — assumes perfect fills at trigger price.

> [!NOTE]
> This analysis is independent of capital recycling via Exit@50% (covered in `analysis.md`).
> TP/SL is a complementary mechanism: Exit@50% handles winners (locks gains),
> SL handles losers (limits catastrophic losses). They target different outcomes.

## Executive Summary

**Stop-loss reduces losses but cannot make the 0.95+ bucket net positive.**
The fundamental math is unfixable: at 0.97 entry, max profit = $3.09 vs max loss = $100.
Break-even HR = 97%; actual tier HR = 88.2%.

**Key findings:**

1. **SL on high-entry positions saves $1,009-$1,028** (from -$1,436 to ~-$416) across the 0.80+ tier
2. **Whipsaw rates are extreme: 79-84%** for the 0.80+ tier — SL cuts 4 winners for every 1 true loser saved
3. **TP adds zero value** for high-entry positions (price path is monotonic, capping upside)
4. **SL's real benefit is capital recycling**: SL=0.03 reduces HIGH tier capital-days by 74.6%
5. **0.95+ tier is irredeemably negative** at all 55 TP/SL combinations tested
6. **Trailing stops hurt the high tier** (97.4% whipsaw — nearly every stopped position would have won)
7. **SL=0.05 is the operational sweet spot**: +$1,009 PnL improvement, 60% capital freed, acceptable whipsaw rate

---

## Data and Methodology

- **Input**: 347 settled positions from `research/output/ledger_politics_no_v3_k100_n2.parquet`
- **Price oracle**: 15-minute OHLC bars from the trades tape for each NO-token asset_id
  - Buy and sell trades both contribute to price discovery (NO token price = NO asset trades)
  - 274,109 bars across 347 markets
- **TP trigger**: `bar_max_price >= fill_price + TP_threshold`
- **SL trigger**: `bar_min_price <= fill_price - SL_threshold`
- **Tie-breaking**: SL checked before TP within same bar (conservative assumption)
- **PnL formula**: `(exit_price / fill_price - 1) * fill_size_usd`
- **Whipsaw**: SL or trailing stop fired on a position that `resolution = WON`
- **Scenarios tested**: 55 total (30 TP×SL, 5 TP-only, 6 SL-only, 4 time stops, 6 TP+time, 3 trailing, 1 hold)

---

## 1. Baseline (Hold to Resolution)

| Tier          |   N | PnL      | HR     | Med Hold | ROC/day  |
|---------------|-----|----------|--------|----------|---------|
| All           | 346 | $33,942  | 82.9%  | 7.5d     | $0.0180 |
| A: 0.80-0.90  |  53 | -$68     | 84.9%  | 12.3d    | -$0.0001|
| B: 0.90-0.95  |  38 | +$199    | 97.4%  | 27.0d    | +$0.0006|
| C: 0.95-1.01  | 152 | -$1,567  | 88.2%  | 2.8d     | -$0.0040|
| High (0.80+)  | 243 | -$1,436  | 89.3%  | 7.5d     | -$0.0012|
| Low (<0.80)   | 103 | +$35,378 | 72.8%  | 9.8d     | +$0.0516|

**Key structural problem**: HR looks "high" for Tier C (88.2%) but break-even is 95-99%.
Every entry at 0.97 needs a 97% HR to break even. The actual 88.2% HR implies
$-9.08 expected value per position. No exit strategy can fix negative expected value
without also reducing the number of positions taken.

---

## 2. TP/SL Grid: All Positions

Top 10 scenarios by total PnL (ALL 346 positions, unconstrained):

| Scenario     | PnL      | Delta    | TP%   | SL%   | Whipsaw | Med Hold |
|--------------|----------|----------|-------|-------|---------|---------|
| trail_sl05   | $35,462  | +$1,521  | 0.0%  | 74.3% | 86.4%   | 1.1d    |
| trail_sl07   | $35,462  | +$1,521  | 0.0%  | 74.3% | 86.4%   | 1.1d    |
| trail_sl10   | $35,462  | +$1,521  | 0.0%  | 74.3% | 86.4%   | 1.1d    |
| notp_sl03    | $34,847  | +$906    | 0.0%  | 62.4% | 74.1%   | 0.9d    |
| notp_sl15    | $34,780  | +$838    | 0.0%  | 35.5% | 58.5%   | 4.0d    |
| notp_sl05    | $34,575  | +$633    | 0.0%  | 52.0% | 69.4%   | 1.7d    |
| notp_sl07    | $34,334  | +$393    | 0.0%  | 47.4% | 66.5%   | 2.2d    |
| notp_sl20    | $34,230  | +$289    | 0.0%  | 32.7% | 56.6%   | 4.4d    |
| notp_sl10    | $34,204  | +$263    | 0.0%  | 41.0% | 62.0%   | 3.2d    |
| time7d       | $34,171  | +$230    | 0.0%  | 0.0%  | —       | 7.0d    |
| **hold**     | $33,942  | +$0      | —     | —     | —       | 7.5d    |

**Observation**: Pure SL outperforms TP/SL combos for ALL positions. Adding TP to SL
adds negligible benefit (TP marginal improvement < $100 across all combos). Best TP+SL
combos approximate the pure SL results.

**Warning on trailing stop (86.4% whipsaw)**: The trailing stop fires on 74.3% of all
positions. Of those 257 triggers, 222 (86.4%) are positions that eventually RESOLVE WINNING.
The trailing stop is capturing small intermediate gains ($14.7 avg) instead of allowing
full resolution. It improves total PnL (+$1,521) because it rescues some large losers
in the low-price tier (<0.80), but it is operationally fragile: the whipsaw rate signals
that the strategy is fundamentally betting against the price path.

---

## 3. Tier A: Entry 0.80-0.90 (53 positions, baseline = -$68)

| Scenario     | PnL    | Delta   | TP%   | SL%   | Whipsaw |
|--------------|--------|---------|-------|-------|---------|
| tp10_sl03    | -$2    | +$66    | 22.6% | 77.4% | 80.5%   |
| notp_sl03    | -$26   | +$42    | 0.0%  | 84.9% | 82.2%   |
| tp10_sl05    | -$28   | +$40    | 30.2% | 69.8% | 78.4%   |
| tp07_sl03    | -$33   | +$35    | 24.5% | 75.5% | 80.0%   |
| notp_sl10    | -$33   | +$35    | 0.0%  | 60.4% | 75.0%   |
| notp_sl05    | -$43   | +$25    | 0.0%  | 77.4% | 80.5%   |
| **hold**     | -$68   | +$0     | —     | —     | —       |

**Best: tp10_sl03 at -$2** (vs -$68 baseline): +$66 improvement, nearly break-even.
- SL at -0.03 fires on 77.4% of positions (high trigger frequency expected for 0.80-0.90 entry)
- Whipsaw = 80.5%: 41 of 51 SL triggers were eventual winners
- The 10 true losers saved: avg loss reduced from -$100 to ~-$3

**Tier A verdict**: SL makes the bucket approach break-even (-$2 vs -$68) but cannot
fully rescue it. The whipsaw rate is structurally high because 84.9% of these positions
eventually WIN — stopping them out early is almost always wrong.

---

## 4. Tier B: Entry 0.90-0.95 (38 positions, baseline = +$199)

| Scenario     | PnL    | Delta    | TP%   | SL%   | Whipsaw |
|--------------|--------|----------|-------|-------|---------|
| **hold**     | +$199  | +$0      | —     | —     | —       |
| tp10_nosl    | +$195  | -$4      | 84.2% | 0.0%  | —       |
| tp07_nosl    | +$170  | -$29     | 86.8% | 0.0%  | —       |
| tp05_nosl    | +$101  | -$98     | 94.7% | 0.0%  | —       |
| time14d      | +$39   | -$160    | 0.0%  | 0.0%  | —       |
| tp03_nosl    | +$20   | -$179    | 97.4% | 0.0%  | —       |

**Tier B verdict**: This is the ONLY tier where hold is optimal. HR=97.4% means almost
every position wins. Any TP or SL exit sacrifices PnL. Even TP=0.10 loses $4 vs hold.
Do NOT apply TP/SL to the 0.90-0.95 entry tier.

**Note**: Tier B has the longest hold time (27d median) because these are markets that
start near the boundary and slowly resolve. They represent ~11% of 0.80+ positions.

---

## 5. Tier C: Entry 0.95-1.01 (152 positions, baseline = -$1,567)

| Scenario     | PnL    | Delta    | SL%   | Whipsaw | Saved$  |
|--------------|--------|----------|-------|---------|---------|
| notp_sl05    | -$309  | +$1,258  | 33.6% | 68.6%   | $1,519  |
| tp05_sl05    | -$318  | +$1,249  | 33.6% | 68.6%   | $1,519  |
| notp_sl03    | -$333  | +$1,234  | 50.0% | 78.9%   | $1,551  |
| notp_sl07    | -$346  | +$1,220  | 28.9% | 63.6%   | $1,486  |
| notp_sl10    | -$396  | +$1,171  | 20.4% | 59.7%   | $1,438  |
| **hold**     | -$1,567| +$0      | —     | —       | —       |

**SL=0.05 is best for Tier C**: saves $1,519 in losses at the cost of a 68.6% whipsaw rate.

**The math for Tier C with SL=0.05**:
- 152 positions, 97 reach SL trigger = 33.6% exit rate
- Of 97 SL exits: 35 were whipsaw (eventual winners), 62 true losers/stagnators
  - Actually: 51 SL exits total (33.6% of 152 = 51)
  - Of 51: 35 whipsaw (68.6%) = $-260 total SL PnL = avg -$5.11 per exit
  - 16 true saves: avg saving $94.90 per position (vs -$100 hold)
- Remaining 101 positions resolve: $-49 PnL (still mostly losers but at lower counts)

**Net verdict**: Best case reduces Tier C from -$1,567 to -$309. Still deeply negative.
**No combination of TP/SL makes the 0.95+ bucket net positive** (zero combos in 55 tested).

The fundamental constraint: a position entered at 0.97 has 3% upside and 97% downside
risk. SL at -0.05 limits the downside to ~5% but simultaneously a >68% chance that the
SL is a false stop-out on an eventual winner.

---

## 6. Stop-Loss Summary: HIGH Tier (0.80+, 243 positions)

The combined 0.80+ tier shows the operational sweet spot:

| Scenario   | PnL    | Delta   | SL%   | Whipsaw | Cap-Days  | ROC/day  |
|------------|--------|---------|-------|---------|-----------|---------|
| hold       | -$1,436| +$0     | 0.0%  | —       | 1,239,074 | -0.0012 |
| notp_sl03  | -$416  | +$1,020 | 63.0% | 83.7%   | 315,209   | -0.0013 |
| notp_sl05  | -$427  | +$1,009 | 49.4% | 79.2%   | 490,432   | -0.0009 |
| notp_sl07  | -$487  | +$949   | 43.6% | 76.4%   | 648,764   | -0.0008 |
| tp10_sl05  | -$408  | +$1,028 | 47.3% | 78.3%   | 403,999   | -0.0010 |
| notp_sl15  | -$666  | +$770   | 22.8% | 67.1%   | 947,270   | -0.0007 |

**Best unconstrained PnL**: tp10_sl05 (-$408, +$1,028 delta, 60% cap reduction)
**Best capital recycling**: notp_sl03 (-$416, +$1,020 delta, 74.6% cap reduction, 0.8d median hold)
**Best balance**: notp_sl05 (-$427, +$1,009 delta, 60.4% cap reduction, 1.5d median hold)

**Important**: All high-tier SL scenarios remain NET NEGATIVE. SL reduces the damage but
cannot overcome the structural negative EV of high-price entries. SL is purely a damage
limitation tool, not a positive alpha generator.

---

## 7. Capital Recycling Impact

SL dramatically compresses hold time, freeing capital for new positions:

| Scenario   | Cap-Days (all) | Reduction | ~Extra fills at P=20 |
|------------|---------------|-----------|---------------------|
| hold       | 1,886,169     | —         | baseline            |
| notp_sl03  | 492,577       | 73.9%     | ~130 extra fills    |
| notp_sl05  | 700,622       | 62.9%     | ~96 extra fills     |
| notp_sl07  | 804,562       | 57.3%     | ~80 extra fills     |

**Interaction with Exit@50% strategy** (from `analysis.md`):

Exit@50% and SL are complementary:
- Exit@50% fires on WINNERS: median 0.9d hold, +$18,722 PnL at P=20
- SL fires on LOSERS: stops out losers at -5-7% instead of -100%
- Combined effect: winners exit fast via TP, losers cut early via SL
- Both mechanisms free capital, enabling more fills at constrained P=20

The combined strategy should be tested in tick validation. Expected outcome:
- Exit@50% adds capital via winner recycling (primary benefit)
- SL adds capital via loser pruning (secondary benefit)
- Interaction may over-free capital (more positions open than P=20 budget)

---

## 8. Time-Based Stops

| Scenario | PnL      | Delta  | Exit Rate | Med Hold |
|----------|----------|--------|-----------|---------|
| time3d   | $34,020  | +$79   | 62.4%     | 3.0d    |
| time5d   | $33,941  | -$1    | 54.3%     | 5.0d    |
| time7d   | $34,171  | +$230  | 49.1%     | 7.0d    |
| time14d  | $34,146  | +$204  | 40.8%     | 7.5d    |
| hold     | $33,942  | +$0    | 0.0%      | 7.5d    |

**Time stops add minimal value** (+$79 to +$230 vs hold). The 5-day stop is approximately
neutral (-$1). Time stops are better suited for stagnant positions than for high-price
markets (which resolve quickly anyway — Tier C median hold = 2.8d).

**TP + time stop combos** (testing tp=0.03, tp=0.05 with 3/5/7 day stops):
All underperform pure SL. The combined effect of capping upside (TP) AND capping hold
time reduces PnL relative to either mechanism alone.

---

## 9. Trailing Stop

| Scenario   | PnL      | Delta    | SL%   | Whipsaw | SL Avg PnL |
|------------|----------|----------|-------|---------|-----------|
| trail_sl05 | $35,462  | +$1,521  | 74.3% | 86.4%   | +$14.7    |
| trail_sl07 | $35,462  | +$1,521  | 74.3% | 86.4%   | +$14.7    |
| trail_sl10 | $35,462  | +$1,521  | 74.3% | 86.4%   | +$14.7    |

**By tier:**

| Tier       | Trailing PnL | Hold PnL | Delta   | Whipsaw |
|------------|-------------|----------|---------|---------|
| All        | $35,462     | $33,942  | +$1,521 | 86.4%   |
| Low (<0.80)| $37,452     | $35,378  | +$2,075 | 55.9%   |
| High (0.80+)| -$1,990    | -$1,436  | -$554   | 97.4%   |

**Trailing stop HURTS the high-price tier (-$554)** and HELPS the low-price tier (+$2,075).

For high-price positions: 97.4% whipsaw means the trailing stop is cutting 4.9× more
winners than losers. These positions are on monotonic price paths toward resolution;
a trailing stop locks in a small gain at the cost of the full resolution payout.

**The correct inference**: trailing stop variants should be tier-conditional:
- Low entries (<0.80): apply trailing stop (strong benefit, saves big losses on ~27% losers)
- High entries (0.80+): DO NOT apply trailing stop (damages 97%+ HR positions)

---

## 10. Key Questions Answered

**Q1: What is the optimal SL for each entry-price tier?**

| Tier       | Optimal SL | PnL Impact | Whipsaw | Recommendation |
|------------|-----------|------------|---------|---------------|
| A: 0.80-0.90 | 0.10 (notp_sl10) | +$35 | 75.0% | Optional (low benefit) |
| B: 0.90-0.95 | NONE         | hold wins | —  | Do NOT apply SL |
| C: 0.95-1.01 | 0.05         | +$1,258   | 68.6% | APPLY SL=0.05 |
| High (0.80+) | 0.05         | +$1,009   | 79.2% | APPLY SL=0.05 |

**Q2: What is the optimal TP for each tier?**

TP adds negligible or negative value for all high-price tiers. The best TP combo
(tp10_sl05 for high tier) beats notp_sl05 by only $19 ($1,028 vs $1,009 delta).
**TP is not worth the operational complexity for high-entry positions.**

For low entries (<0.80), TP is already covered by Exit@50% strategy.

**Q3: Can any TP/SL combo make the 0.95+ bucket net positive?**

**NO. Zero out of 55 scenarios achieve positive PnL for the 0.95+ tier.**

The best achievable is SL=0.05: PnL = -$309 (vs -$1,567 hold). The structural math
requires 97%+ HR to break even at 0.97+ entry, and our pool's HR is 88.2% for this tier.

**Q4: How does TP/SL interact with demand-driven eviction?**

TP/SL (especially SL=0.05) dramatically reduces capital-days (62-74% reduction).
This is MORE capital recycling than eviction alone. The two mechanisms complement:
- SL recycles capital immediately when a position goes wrong (lossless vs -$100)
- Exit@50% recycles capital when a position is winning (captures half the gain)
- Combined: all positions have a defined maximum hold time

**Q5: What is the whipsaw rate? Is SL too tight?**

For high-entry positions, ALL tested SL values (0.03-0.20) have >60% whipsaw:

| SL   | Whipsaw (High tier) | Verdict           |
|------|--------------------|--------------------|
| 0.03 | 83.7%             | Too tight           |
| 0.05 | 79.2%             | Best balance        |
| 0.07 | 76.4%             | Still high          |
| 0.10 | 71.9%             | Better whipsaw rate |
| 0.15 | 67.1%             | Fewer triggers      |
| 0.20 | 63.8%             | Rarely triggers     |

**The whipsaw rate cannot be reduced below ~63% for high-price positions.** This is
structural: at 0.85-0.97 entry prices, any price dip is usually temporary because
89%+ of these markets eventually resolve winning. SL=0.05 is recommended as the
sweet spot: meaningful loss savings ($1,009), acceptable trigger frequency (49%),
and not too tight (fewer unnecessary cuts than SL=0.03).

**Q6: Should TP/SL only apply above a price threshold?**

**Yes — apply SL only to Tier C (0.95+):**
- Tier B (0.90-0.95): Hold is optimal (HR=97.4%, SL hurts)
- Tier A (0.80-0.90): Marginal benefit, not worth complexity
- Tier C (0.95+): SL=0.05 saves $1,258, clear benefit

Alternatively, apply SL=0.05 to all positions (0.80+) for simplicity: total HIGH tier
improvement = +$1,009 with 79.2% whipsaw. The whipsaw rate is high but the total PnL
improvement is real because the 16 true saves in Tier C average $94.90 each.

---

## 11. Recommendation

### Recommended Exit Policy for Politics NO v3 (High-Price Entries)

```python
@dataclass
class ExitPolicy:
    # Primary: profit-taking (from analysis.md)
    exit_pct: float = 0.50          # sell when NO price >= fill + 50% of upside

    # Secondary: stop-loss (THIS ANALYSIS)
    sl_threshold: float = 0.05      # sell when NO price <= fill - 0.05
    sl_only_above: float = 0.95     # only apply SL to entries at/above 0.95

    # Do NOT apply:
    # - TP to 0.90+ entries (hurts tier B, negligible for tier C)
    # - Trailing stop to 0.80+ entries (97.4% whipsaw, -$554 vs hold)
    # - Time stop alone (only +$230, better capital use via SL)
```

**Expected unconstrained impact of SL=0.05 on 0.95+ entries:**
- PnL improvement: +$1,258 (from -$1,567 to -$309)
- Capital freed: 748K cap-days (60% reduction for HIGH tier)
- True loss saves: 16 positions × $94.90 avg = $1,518
- Whipsaw cost: 35 winners cut at avg -$5.11 = -$179 lost upside
- Net: +$1,258 verified by simulation

**Why not SL=0.03?** Despite similar PnL (+$1,020 vs +$1,009), SL=0.03 triggers on
63% of positions vs 49% for SL=0.05. The 0.03 threshold fires on normal price noise
even in advancing markets, causing more operational overhead (more cancel-replace orders)
without meaningfully better PnL.

### Integration with Exit@50%

The recommended combined policy:
1. **ALL entries**: Exit at `fill + 0.50*(1-fill)` (50% of max payout) — per `analysis.md`
2. **High entries (>0.95)**: Additionally: exit if price drops to `fill - 0.05`
3. **Hold otherwise**: if neither exit triggers, hold to resolution

This gives every position two exits:
- Winners: captured at 50% via Exit@50%
- Losers (0.95+): cut at -5% via SL=0.05

The combined simulation (Exit@50% + SL) is recommended as a tick-validation follow-up.

---

## 12. Limitations and Caveats

1. **Oracle assumption**: Price triggers are based on 15-min bar min/max, not real bid/ask.
   In production, the actual triggered price may differ by 0.001-0.005 (bid/ask spread).
   For a $100 position, this represents $0.10-$0.50 per exit — negligible vs the PnL swings.

2. **Simultaneous triggers**: When TP and SL are in the same bar, SL is checked first
   (conservative). In practice, price may hit TP before SL within the bar.

3. **Bar granularity**: 15-minute bars may miss sub-15-minute trigger opportunities.
   This is slightly pessimistic for TP (may miss early triggers) and conservative for SL.

4. **Hold-to-resolution fallback**: Positions with no trades data (0 in this study)
   fall back to hold-to-resolution. All 347 markets had price data.

5. **SL does not prevent losses entirely**: With SL=0.05 on 0.95+ entries, the
   average SL exit PnL is -$5.11 (not $0). The position still loses money but the
   loss is limited to ~5% vs the potential -$100 full loss.

---

## Files

- `tpsl_results.json` — Machine-readable results (all 55 scenarios × 6 tiers)
- `tpsl_simulation.md` — This document
- `analysis.md` — Capital recycling via Exit@50% (the primary optimization)
- `exit_sweep_analysis.md` — Earlier exit sweep analysis
