# 1H Qualified Trader Bot Analysis

**Analyst**: architect-v2
**Date**: 2026-03-06
**Fold analyzed**: 2025-07-01 to 2026-01-01 (training window — largest fold, n=17,603 markets)
**Qualified pool**: 119 traders (mt=50, excess_hr=15pp, max_avg_ep=0.75, threshold=65.97%)

---

## Summary Verdict

> [!CRITICAL]
> **The 1H qualified trader pool is overwhelmingly bots.** 91 of 119 traders (76%) exceed 30 trades/day,
> 54 (45%) exceed 100 trades/day, and only 7 (6%) have human-like activity (<10 trades/day). Lifetime
> trade counts range from 16,000 to 1,213,168 — every trader in the top-30 by lifetime volume qualifies
> as a bot by an unambiguous margin. However, the signal is NOT uncopyable on this basis alone — see
> Section 5 (Copyability Window).

**Copyability verdict**: PARTIAL — these are bots, but they enter 1H markets well before resolution.
The median copyability window is 70 minutes after their entry. The constraint is not timing but
**market saturation**: by the time a bot enters and we observe it, the market is already 68% of its
way through its lifetime. The opportunity cost question is whether price has already moved to reflect
the signal.

---

## 1. Trade Frequency (Q1)

**Question**: How many trades/day do qualified traders execute?

| Metric | Value |
|--------|-------|
| Median TPD (all markets, training window) | 80 |
| p25 TPD | 33 |
| p75 TPD | 179 |
| p90 TPD | 286 |
| Max TPD | 5,037 |
| Mean TPD | 220 |
| Traders with TPD > 100 | 54 / 119 (45%) |
| Traders with TPD > 30 | 91 / 119 (76%) |
| Traders with TPD < 10 (human-like) | 7 / 119 (6%) |

**Top traders by TPD (training window):**

| Trader | TPD | Markets/Day |
|--------|-----|-------------|
| 0xefe65207 | 5,037 | 92 |
| 0xb563eb01 | 4,801 | 84 |
| 0xc5e62509 | 1,381 | 55 |
| 0x64bdd60f | 1,028 | 160 |
| 0xba264376 | 922 | 121 |

**Conclusion**: Overwhelmingly bots. The median qualified trader executes 80 trades/day — 4-8x the
upper bound for an active human trader. The maximum (5,037 TPD) is consistent with high-frequency
algorithmic market-making.

---

## 2. Entry Timing (Q2)

**Question**: How quickly do qualified traders enter after a market opens?

Pool-level entry lag (qualified trader's first trade vs. market's first trade by any maker):

| Metric | Minutes |
|--------|---------|
| p5 | 97 |
| p10 | 154 |
| p25 | 334 |
| p50 (median) | 681 |
| p75 | 1,160 |
| p90 | 1,660 |
| Mean | 846 |
| Within 1 minute | 0.5% |
| Within 5 minutes | 0.6% |
| Within 1 hour | 2.8% |

**Key finding**: Qualified traders do NOT enter instantly after market open. The median lag is
11.4 hours — they enter markets roughly mid-way through the market's typical 12-hour lifetime.
This is NOT the behavior of a latency-arb bot (which would enter within seconds). These appear to
be **information-accumulation bots** that wait for the outcome to become clearer before committing.

The fastest individual trader (`0x64bdd60f`) has a median lag of 38 minutes (2,322 seconds) and
322 instant (<1 minute) entries out of 755 markets — suggesting an event-driven trigger strategy
that fires on some markets immediately but waits on others.

**Implication for copyability**: The delayed entry pattern is actually *favorable* for copy-trading.
These traders are not front-running the market open; they are entering based on accumulated
information. Their signal is detectable and non-ephemeral.

---

## 3. Trade Size Distribution (Q3)

**Question**: Do qualified traders use suspiciously uniform (bot-like) sizes?

**Size buckets (BUY trades, 1H markets, training window):**

| Bucket | Count | % |
|--------|-------|---|
| <$1 | 83,074 | 9.7% |
| $1-5 | 308,562 | 35.9% |
| $5-10 | 156,255 | 18.2% |
| $10-25 | 156,628 | 18.2% |
| $25-50 | 76,496 | 8.9% |
| $50-100 | 39,438 | 4.6% |
| $100-500 | 29,367 | 3.4% |
| $500+ | 9,976 | 1.2% |

**Top exact sizes (most frequent):**

| Size ($) | Count | % |
|----------|-------|---|
| 5.00 | 5,214 | 0.61% |
| 4.95 | 4,781 | 0.56% |
| 3.00 | 4,735 | 0.55% |
| 4.00 | 4,266 | 0.50% |
| 2.40 | 4,263 | 0.50% |

**Per-trader size CV (coefficient of variation):**

| Metric | Value |
|--------|-------|
| Median CV | 1.30 |
| p75 CV | 2.19 |
| p90 CV | 2.95 |
| Traders with CV < 0.1 (very uniform) | 0 |
| Traders with CV < 0.3 (uniform) | 0 |

**Conclusion**: These bots are NOT using uniform fixed sizes. Median CV=1.30 is high — trades vary
substantially in size. The top sizes ($5, $4.95, $3, $4, $2.40) each account for <0.6% of total
volume with no dominant round-number clustering. This is consistent with fractional-Kelly or
probability-proportional sizing — characteristic of systematic (not HFT/arb) bots that size
positions based on edge estimates.

---

## 4. Concurrent Market Activity (Q4)

**Question**: How many 1H markets do qualified traders operate in simultaneously?

**Distribution of 1H markets per trader-day:**

| Metric | Value |
|--------|-------|
| Median markets/day | 8 |
| p75 | 18 |
| p90 | 32 |
| p99 | 92 |
| Max | 96 |
| Days with 20+ markets | 1,298 of 5,785 (22%) |
| Days with 50+ markets | 218 of 5,785 (3.8%) |

**Traders routinely operating 20+ 1H markets/day (>50% of active days):**
- 25 of 119 traders (21%) routinely operate across 20+ 1H markets daily
- Top traders (`0xb563eb01`, `0xefe65207`) trade 83-92 1H markets on *every* active day

**Conclusion**: High concurrent market coverage confirms bot operation. Humans rarely trade more
than 5 markets simultaneously; 96 markets in a single day is definitively automated.

---

## 5. Lifetime Trade Volume (Q5)

**Question**: What is the total lifetime trade count — can we confirm bot status?

Top 10 by lifetime trades:

| Trader | Lifetime Trades | 1H Qual Trades | HR |
|--------|----------------|----------------|-----|
| 0xb9fc8078 | 1,213,168 | 1,804 | 87.9% |
| 0xba264376 | 336,554 | 1,178 | 81.6% |
| 0x4a867a1f | 312,737 | 1,670 | 71.1% |
| 0xb563eb01 | 294,273 | 107 | 72.0% |
| 0x0dfae545 | 282,293 | 863 | 87.1% |
| 0xefe65207 | 199,225 | 121 | 93.4% |
| 0x94f4205e | 141,095 | 71 | 83.1% |
| 0x76070efd | 122,128 | 261 | 88.9% |
| 0xf1fbeee8 | 110,524 | 200 | 79.0% |
| 0x64bdd60f | 98,415 | 97 | 69.1% |

Every trader in the top-30 has >5,000 lifetime trades. A human trader with 1.2 million lifetime
trades would need to execute ~800 trades/day, every day, for 4 years.

**Notable pattern**: Hit rates are strikingly high (70-93%). This is not luck — these bots have
genuine edge and have been refining their strategies over millions of trades. The 87.9% HR for
`0xb9fc8078` across 1,804 1H trades is not a small-sample artifact.

---

## 6. Copyability Window Analysis

**Key question**: After a qualified bot enters, is there time to copy the trade before the market closes?

**Market lifetime** (first trade to last trade for 1H tag markets):
- Median: 12 hours
- p90: 27 hours
- Only 20 markets (<0.1%) have lifetime <1 hour

**Copyability window** (time from qualified trader's first entry to market close):

| Metric | Minutes |
|--------|---------|
| p5 | 19 |
| p10 | 30 |
| p25 | 51 |
| p50 (median) | **70** |
| p75 | 96 |
| p90 | 117 |
| Mean | 81 |
| < 5 min remaining | 1.5% |
| < 30 min remaining | 9.7% |
| < 60 min remaining | 34.8% |
| > 2 hr remaining | 7.1% |

**Interpretation**: The median copyability window is **70 minutes** — enough time to observe the
signal, compute consensus, and submit a CLOB order. Only 1.5% of entries leave <5 minutes to
react.

However, there is an important price impact concern: if the bot enters mid-market and the market
is 12 hours old at entry, the price has likely already partially reflected the eventual outcome.
The copy-trader is not getting the bot's entry price; they are getting the post-signal price.

> [!WARNING]
> **The 1H signal faces a price-impact copyability problem that is distinct from timing.**
> The median bot entry is ~681 minutes after market open. On a 12-hour market, this is 57% of
> the way through the market's life. By this point, prices may already be at 0.70-0.85 (near
> certainty). Buying YES at 0.80 when YES wins yields only 0.20 USD per unit — the fee structure
> makes many of these signals economically unviable for copying even though there is time to copy.
> The max_avg_entry_price=0.75 filter partially addresses this, but the price at copy time
> (post-signal) will be higher than the bot's actual entry price.

---

## Conclusion

### Are they bots?
**Yes, definitively.** 76% of qualified traders are bots by trade frequency (>30 TPD), and 100%
of the top-30 by volume are bots by lifetime trade count. The pool is dominated by sophisticated
algorithmic traders, not humans.

### Does this make the signal uncopyable?

**No, but the bot nature changes the edge source:**
- These are NOT latency-arb bots (they don't enter within seconds of market open)
- They appear to be **information-aggregation or calibrated-probability bots** that enter
  mid-market when their models reach confidence thresholds
- The median 70-minute copyability window is sufficient for practical copy execution
- The real constraint is **price drift**: bots enter early enough that copying post-signal
  incurs adverse selection relative to the bot's own entry price

### What this means for tick-by-tick validation
1. The RealisticFillSimulator will need to fill at market price at signal-detection time,
   NOT at the bot's historical entry price. The sweep used bot entry prices — tick-by-tick
   will use signal-detection prices. Expect fill prices 5-15pp worse than sweep assumed.
2. The 48h hold window is more than adequate — 1H markets close in median 12 hours.
3. The `first_trade >= test_start` fix is critical because bot strategies may have positions
   in ongoing markets from before the test window — those entries are from training-period
   analysis and not copyable.

### Recommendation
Proceed with tick-by-tick validation. Bot nature does not disqualify the signal — it reframes
it as "follow informed algorithmic positioning" rather than "copy human insiders." The price-drift
concern should manifest as reduced HR in tick-by-tick vs vectorized, contributing to the expected
20-40pp degradation. If degradation exceeds 40pp, investigate fill price assumptions.
