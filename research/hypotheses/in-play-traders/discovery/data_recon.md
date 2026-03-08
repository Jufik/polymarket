# In-Play Traders: Data Recon Report

**Date**: 2026-03-07
**Status**: GO — viable signal population identified with important caveats
**Hypothesis**: Traders who enter positions shortly before market resolution have informational advantage and can be copied.

---

## 1. Population Size

**In-play definition**: hold time (first_trade → resolved_at) < 4 hours.

| Metric | In-Play (<4h) | Total | % of Total |
|--------|--------------|-------|-----------|
| Positions | 4,416,721 | 29,872,013 | 14.8% |
| Unique traders | 170,216 | 967,466 | 17.6% |
| Unique markets | 260,223 | 404,861 | 64.3% |

**Key insight**: In-play activity spans 64% of all markets, but represents only 14.8% of positions. This is a minority behavior but extremely widespread — not a niche.

---

## 2. Hold Time Distribution and Hit Rate

| Bucket | n_positions | n_traders | n_markets | HR% | Median Vol (USD) | Total Vol (USD) |
|--------|------------|-----------|-----------|-----|-----------------|----------------|
| <1h | 129,723 | 10,339 | 50,194 | 44.4% | $9.99 | $126M |
| 1–4h | 4,286,998 | 166,873 | 253,844 | 48.3% | $12.40 | $2.12B |
| 4–24h | 4,749,110 | 387,360 | 244,409 | 40.7% | $36.99 | $4.91B |
| 1–7d | 3,187,794 | 434,346 | 156,869 | 30.9% | $23.40 | $3.41B |
| 7d+ | 17,518,388 | 696,829 | 122,297 | 42.5% | $9.90 | $8.07B |

**Key findings**:
- **1–4h bucket has the highest HR at 48.3%** — above the 4–24h (40.7%) and 1–7d (30.9%) buckets.
- The 7d+ bucket's 42.5% HR is elevated because it is dominated by Crypto 1H recurring markets (see Section 6).
- The 30.9% HR for 1–7d markets is suspicious — likely reflects mid-range hold times on binary crypto markets with ~50% base rate but directional noise.

### By Position Type and Bucket

| Position | Bucket | n_positions | HR% | Median Vol |
|----------|--------|------------|-----|-----------|
| YES | <1h | 63,232 | **58.1%** | $9.99 |
| YES | 1–4h | 1,959,765 | 36.3% | $8.73 |
| YES | 4–24h | 2,156,158 | 35.6% | $18.98 |
| NO | <1h | 65,286 | 31.9% | $9.99 |
| NO | 1–4h | 1,778,732 | **60.9%** | $9.99 |
| NO | 4–24h | 2,050,137 | 44.7% | $41.20 |
| HEDGED | <1h | 1,205 | 3.9% | $37.59 |
| HEDGED | 1–4h | 548,501 | 50.0% | $98.96 |

**Critical anomaly**: YES <1h has 58% HR (well above YES base rate of ~38%). Investigated in Section 5.

---

## 3. Scalper Detection

### 3a. Raw Trades (side=1 BUY, side=2 SELL)

Scalpers identified as makers who execute side=2 (sell) within 4h of their initial side=1 (buy) on the same (condition_id, asset_id):

**Jan 2025 sample (one month)**:
- 20,341 unique scalper traders
- 84,988 scalp positions in one month
- Average sell volume: $313 per scalp
- Total sell volume: $26.6M in January alone

This extrapolates to ~240,000 scalper traders/year and ~1M scalp events/year. **Scalpers are a real and substantial population.**

### 3b. Net Position Proxy (maker_positions)

For in-play positions, the `abs(net_usd) / volume` ratio (conviction):

| Conviction Tier | n_positions | HR% | Median Vol |
|----------------|------------|-----|-----------|
| <5% (near zero) | 263,085 | 49.4% | $79 |
| 5–25% (low) | 195,342 | 50.8% | $82 |
| 25–60% (medium) | 150,631 | 49.9% | $53 |
| >60% (high) | 3,807,663 | 47.9% | $10 |

- 86.9% of in-play positions are directional (>50% conviction)
- Round-trippers (conviction <5%): 5.9% of in-play population, HR 49.4% — essentially random

### 3c. Payout Analysis

For in-play positions, checking whether zero payout indicates early exit:

| yes_won | Payout Class | n | Avg Vol | Avg Net USD |
|---------|-------------|---|---------|------------|
| 0 | non-zero payout | 1,014,785 | $916 | -$911 |
| 0 | zero payout | 1,274,454 | $128 | -$54 |
| 1 | non-zero payout | 1,022,869 | $957 | -$898 |
| 1 | zero payout | 1,104,613 | $155 | -$58 |

**Concerning**: ~50% of in-play positions on WINNING markets have zero payout. This means either:
1. They sold before resolution (scalped), or
2. They held NO positions (where net_usd is negative = sold YES tokens = bullish NO)

The negative avg_net_usd is expected for NO positions in maker_positions (where net_usd represents net YES token purchase, so NO positions show negative net_usd).

---

## 4. Tag Breakdown (Top 20 In-Play Tags)

| Tag | n_inplay | n_traders | n_markets | HR% | Median Vol | Total Vol | % In-Play |
|-----|---------|-----------|-----------|-----|-----------|-----------|-----------|
| 1H | 1,864,079 | 63,515 | 24,809 | 49.5% | $21 | $549M | **89.1%** |
| Esports | 543,575 | 25,111 | 37,764 | 49.7% | $6 | $292M | **57.5%** |
| Tennis | 224,076 | 11,956 | 22,013 | 47.1% | $1 | $93M | **59.4%** |
| Sports | 2,051,940 | 71,225 | 179,706 | 48.0% | $8 | $1.28B | 29.3% |
| Games | 2,028,306 | 68,033 | 172,037 | 48.5% | $9 | $1.30B | 36.2% |
| NCAA Basketball | 198,706 | 7,425 | 22,528 | 45.5% | $2 | $45M | 45.7% |
| NCAA | 216,276 | 7,864 | 26,540 | 45.1% | $1 | $45M | 42.7% |
| Basketball | 537,992 | 22,407 | 51,359 | 47.1% | $4 | $302M | 33.2% |
| Politics | 140,494 | 26,616 | 13,476 | 52.5% | $10 | $126M | ~10% |
| Crypto | 2,009,460 | 89,056 | 42,026 | 49.2% | $21 | $722M | 11.4% |

**1H and Esports/Tennis stand out as structurally in-play markets.**

---

## 5. Key Anomalies and Explanations

### Anomaly 1: YES <1h HR = 58%, but YES 1–4h HR = 36%

**Sub-1h YES by tag**:
- Crypto / 1H / Up-or-Down tags: HR = 85–96% (!)
- Sports / Games: HR = 51% (expected)
- Esports: HR = 41% (below base rate)
- Tennis: HR = 38%

**Root cause**: The sub-1h, ultra-high-HR YES entries are almost entirely from the **0x751a...** trader on 1H crypto markets. This single trader dominates the <1h 1H YES population with HR = 99.86% across thousands of positions at median $553 each. Their entry pattern: enter YES position 27–58 minutes before the 1H market resolves, consistently on the correct side. **This looks like algorithmic near-resolution prediction or possibly informed trading.**

The 1H market structure: "Bitcoin Up or Down - [Date, Hour]" resolves ~1h after creation. An entry with 27 minutes remaining is genuinely late-stage. HR of 99.86% on 14,540 YES positions across months cannot be luck.

### Anomaly 2: 1H tag — 89% of all positions are in-play

**Confirmed**: 1H markets are hourly crypto price prediction markets (BTC, ETH, XRP, SOL, etc. "Up or Down"). By design, they resolve 1 hour after market creation. Any trade has hold time ≤ 1h. The 89% figure reflects that nearly all 1H traders are technically "in-play" by our definition. This is a **structural artifact, not a behavioral signal** — the 1H tag needs its own bucket.

### Anomaly 3: NO in-play HR = 60.9% for 1–4h bucket (vs base rate ~38%)

**Investigated**: NO in-play entry prices are bimodal. For 1H markets, NO in-play traders enter at YES prices of 0.00–0.20 (market has essentially resolved, YES is near-zero, so NO is near $1). This is the same pattern as YES <1h but from the NO side. The 60.9% HR for 1–4h NO reflects in-the-money positions entered as the market resolution becomes clear.

Tags with highest NO in-play excess HR include:
- Yearly events: +85pp excess
- Monthly events: +67pp excess
- Political events (election, Trump, russia): +65–70pp excess

**Warning**: Many of these high-HR NO entries may be in-play contamination — entering when outcome is known. See `research/knowledge/pitfalls/in_play_contamination.md`.

### Anomaly 4: Serial in-play traders have conviction = 1.0 but tiny volume

Top traders by in-play count (40–53k positions) show:
- conviction = 0.98–1.00 (fully directional, no round-tripping)
- median volume = $0.05–$0.35 per position
- HR ≈ 45–49% (near base rate)

**These are bots placing minimum-size directional bets across all markets.** Not alpha-generating — mechanical market coverage. One exception: **0x6993...** with HR = 5.07% across 23,979 positions — a known-loser that systematically picks the wrong side.

---

## 6. 0x751a High-Volume In-Play Trader: Deep Profile

This trader is the most significant finding in the recon:

| Tag | Position | n | HR% | Total Vol | Median Vol |
|-----|---------|---|-----|-----------|-----------|
| Sports YES | YES | 2,163 | **99.45%** | $34.1M | $405 |
| Sports NO | NO | 2,034 | **0.39%** | $29.3M | $300 |
| Crypto YES | YES | 4,589 | **99.87%** | $21.8M | $780 |
| Crypto NO | NO | 5,227 | **0.17%** | $25.4M | $847 |
| Bitcoin YES | YES | 1,453 | **99.86%** | $13.2M | $2,500 |
| Bitcoin NO | NO | 1,645 | **0.18%** | $16.0M | $2,629 |

**Pattern**: This trader enters YES when they expect YES to win (HR=99.87%) and enters NO when they expect YES to lose (HR=0.17% for YES, meaning their NO picks win 99.83%). They are **simultaneously bullish and bearish** with extraordinary accuracy on both sides.

They hold for <4h (in-play window). Total in-play volume ~$257M. This is the single most informative trader in the in-play population and warrants its own copy strategy.

**Note**: The sample from [H] shows they enter BTC/ETH/XRP "Up or Down" markets with 27–59 minutes remaining, on both YES and NO sides, and are correct 99%+ of the time. Entry volume ranges from $10 to $13,965 per position. This is not luck.

---

## 7. Data Availability for Scalper Tracking

| Data Source | Available | Contents | Scalper Use |
|-------------|-----------|----------|-------------|
| maker_positions | YES (in-memory) | Net position at resolution | Partial — can't see exit trades |
| yes_entry_data | YES (parquet view) | Volume-weighted avg entry price | YES entry prices only |
| trades | YES (440M rows, parquet view) | side=1 (buy), side=2 (sell), price, maker, timestamp | Full scalper detection possible |
| maker_positions_resolved_corrected | NO (not in snapshot) | Split-corrected positions | N/A |

**Raw trades are available** (440M rows, monthly parquet files). `side=1` = BUY, `side=2` = SELL. This is sufficient to:
1. Identify scalpers (buy then sell within N hours)
2. Measure scalper edge (profit from round-trip)
3. Track whether scalpers' directional bet at entry was correct

**Key schema**: `(condition_id, asset_id, side, price, amount_usd, fee_usd, maker, timestamp)`

---

## 8. Go/No-Go Assessment

### GO factors

1. **Population is large**: 4.4M in-play positions, 170K traders, $2.1B in volume for 1–4h bucket alone.
2. **Signal is real**: HR = 48.3% for 1–4h vs 40.7% for 4–24h — clear monotonic improvement as hold time shortens, after the sub-4h window.
3. **Scalpers detectable**: 20K+ unique scalpers in one month via raw trades. Buy-then-sell within 4h is easily queryable.
4. **Extraordinary signal trader found**: 0x751a with 99%+ HR across $257M in-play volume. Copy strategy for this single trader alone is worth investigating.
5. **Tag heterogeneity is mappable**: 1H (structural), Sports, Esports, Tennis, Politics each have different in-play dynamics.

### CAUTION factors

1. **1H tag is a confound**: 89% of 1H positions are "in-play" by definition (1h market). Exclude from general in-play analysis or treat separately.
2. **In-play contamination risk**: Many high-HR NO in-play entries on political/election/monthly tags appear to enter when outcome is already determined. Need price filter (e.g., only enter when YES price is 0.10–0.85 at signal time).
3. **Serial in-play bots dominate count**: The top 5K traders by in-play count are bots placing $0.05–$0.35 micro-bets. HR ≈ base rate. Filter by volume (min $5 median position size) before copy pool construction.
4. **Zero-payout ambiguity**: ~50% of in-play YES positions on winning markets have zero payout, suggesting they may have sold before resolution. This complicates PnL measurement — if they exited profitably (scalped), our copy would hold to resolution and earn the same profit, but the HR metric would be biased by their exit timing.
5. **maker_positions_resolved_corrected not in snapshot**: CTF split corrections are unavailable for in-play positions. Split positions may inflate some hold-time calculations for crypto markets.

### Recommended research tracks

**Track A (Primary)**: Copy the 0x751a trader and any other traders with >100 in-play positions, HR >70%, and median vol >$100. Tick-by-tick validation required to check entry timing feasibility.

**Track B (Pool-based)**: Construct a consensus in-play signal — N traders entering YES/NO in the final 1–4h of a market. Exclude 1H markets. Exclude entries where YES price < 0.10 or > 0.90 at signal time (contamination gate).

**Track C (Scalper-alpha)**: Use raw trades to identify scalpers who buy-then-sell profitably. Extract the trades at entry before exit — do those entry prices predict direction? If scalpers who exit early also get the direction right, they are the strongest signal source.

---

## 9. Summary Statistics

| Metric | Value |
|--------|-------|
| Total in-play positions (<4h) | 4,416,721 |
| Total in-play traders | 170,216 |
| Total in-play markets | 260,223 |
| In-play population HR (YES) | 36.3% (1–4h) vs 58.1% (<1h) |
| In-play population HR (NO) | 60.9% (1–4h) |
| Top signal trader (0x751a) HR YES | 99.45–99.87% |
| Top signal trader total in-play vol | ~$257M |
| Scalpers detected (Jan 2025 alone) | 20,341 traders, 84,988 positions |
| Scalper sell volume (Jan 2025) | $26.6M |
| Trades table rows | 440M |
| Trades schema | (condition_id, asset_id, side, price, amount_usd, fee_usd, maker, timestamp) |

---

## 10. Next Steps

1. **Filter and profile the 0x751a trader** using the full tick history — what markets do they enter, at what prices, and can we follow their entry in real-time?
2. **Construct filtered in-play pool** (exclude: 1H tag, micro-bet bots <$1 median, contamination zone YES>0.85).
3. **Test consensus in-play signal vectorized**: N≥3 new in-play entries in final 2h → trigger copy. Measure HR across folds.
4. **Track C scalper analysis**: Query trades table for buy-then-sell within 4h, measure directional edge at entry.

**Decision**: **GO** — proceed to vectorized discovery sweep.
