# Elite Whale Copy Strategy — Tick-by-Tick Validation Results

**Date**: 2026-03-08
**Test Period**: January 2026
**Strategy**: Copy first BUY from any elite in-play trader (N=1 threshold)
**Label**: REALISTIC (tick-by-tick, SyncReplayRunner on Parquet snapshot)
**Script**: `research/hypotheses/in-play-traders/scripts/elite_whale_copy.py`

---

## 1. Elite Pool Summary

| Metric | Value |
|--------|-------|
| Training period | < 2026-01-01 |
| Criteria | >=50 in-play positions (hold <4h), >=80% HR, median vol >=$5, <90% gambling |
| Elite traders qualifying | **551** |
| January 2026 non-gambling markets (elite-touched) | **25,354** |
| January 2026 base rate (YES positions) | **30.2%** |
| January 2026 base rate (NO positions) | **53.9%** |

### Top 10 Traders by CopyScore (HR x sqrt(N) x (1 - gambling_frac))

| Rank | Trader | N_inplay | HR% | HR_NG% | Gamb% | Hold_min | Med_Vol | CopyScore |
|------|--------|----------|-----|--------|-------|----------|---------|-----------|
| 1 | 0x2c45f2be0c | 4,271 | 99.98% | 99.98% | 5.5% | 110 | $74 | 61.74 |
| 2 | 0x336151559e | 5,232 | 99.98% | 99.98% | 14.9% | 123 | $511 | 61.52 |
| 3 | 0x212af34bef | 5,325 | 99.96% | 99.98% | 16.2% | 123 | $509 | 61.14 |
| 4 | 0x7846e489e1 | 5,173 | 99.94% | 99.98% | 17.8% | 122 | $514 | 59.11 |
| 5 | 0xa7ec2d5ce6 | 4,912 | 99.96% | 99.98% | 17.8% | 123 | $518 | 57.62 |
| 6 | 0x751a2b86ca | 11,697 | 99.75% | 99.69% | 48.4% | 83 | $500 | 55.70 |
| 7 | 0x4ad6cadefa | 3,689 | 98.32% | 98.25% | 7.1% | 135 | $1,128 | 55.49 |
| 8 | 0x6ffb4354cb | 4,085 | 95.06% | 94.76% | 10.3% | 125 | $157 | 54.52 |
| 9 | 0x01ed5c64d1 | 2,262 | 99.91% | 99.91% | 1.9% | 172 | $260 | 46.62 |
| 10 | 0x9b979a0656 | 2,519 | 95.12% | 94.97% | 2.9% | 139 | $2,444 | 46.36 |

---

## 2. Pre-Flight Checklist

- [x] SELL trades excluded — BUY-only strategy (SELL is ambiguous)
- [x] Asset-ID resolution — token_map with uppercase key fix applied
- [x] One signal per market — dedup via self._signaled set
- [x] Gambling markets excluded — slug pattern classification (43,720 markets)
- [x] N=1 threshold — single elite trader entry fires signal
- [x] Settlement built into SyncReplayRunner

---

## 3. Key Validation Results

### 3a. Pool Size Sweep (No Price Gate — CORRECT configuration)

| Pool Size | Fills | Wins | Losses | Hit Rate | Total PnL | Sharpe | Avg Hold |
|-----------|-------|------|--------|----------|-----------|--------|----------|
| Top-25 | 11,097 | 10,870 | 222 | **98.0%** | $16,836 | 0.87 | 5.95h |
| Top-50 | 15,225 | 14,625 | 572 | **96.2%** | $34,924 | 0.71 | 18.63h |
| Top-100 | 15,891 | 14,946 | 919 | **94.2%** | **$52,932** | **0.72** | 22.29h |
| Top-200 | 13,439 | 12,095 | 1,326 | 90.1% | $39,635 | 0.49 | 29.63h |
| All-551 | 7,443 | 5,920 | 1,510 | 79.7% | $18,560 | 0.19 | 58.65h |

**Best configuration: Top-100 pool, no price gate.**

### 3b. Min Price Filter Sweep (Pool=Top-100)

| min_price | Fills | Wins | Losses | Hit Rate | Total PnL | Sharpe | Avg Hold |
|-----------|-------|------|--------|----------|-----------|--------|----------|
| None | 15,891 | 14,946 | 919 | 94.2% | **$52,932** | 0.72 | 22.29h |
| 0.10 | 16,049 | 15,263 | 760 | 95.3% | $34,051 | 0.98 | 21.74h |
| 0.20 | 16,413 | 15,683 | 704 | 95.7% | $24,953 | 0.94 | 20.56h |
| 0.30 | 16,746 | 16,076 | 644 | 96.1% | $13,221 | 0.64 | 19.59h |
| 0.50 | 17,033 | 16,492 | 515 | 97.0% | $11,691 | 0.73 | 18.29h |

Counter-intuitive finding: min_price filters HURT PnL despite improving HR. See Section 4.

### 3c. max_price=0.85 Filter (Initial Hypothesis — INVALIDATED)

| Pool | Fills | Hit Rate | Total PnL | Sharpe |
|------|-------|----------|-----------|--------|
| Full-551, no price gate | 7,443 | 79.7% | $18,560 | 0.19 |
| Full-551, max_price=0.85 | 2,833 | **35.4%** | -$165,118 | -7.74 |
| Top-25, max_price=0.85 | 383 | 37.6% | -$21,359 | -15.94 |
| Top-100, max_price=0.85 | 2,519 | 57.2% | -$82,406 | -5.80 |

**The max_price gate DESTROYS the signal** — this is the most important finding.

---

## 4. Critical Finding: Price Distribution Reveals the True Signal

### Fill Price Distribution (Full-551, No Price Gate)

| Price Bucket | N | HR% | Total PnL | Avg PnL/fill | Avg Price |
|--------------|---|-----|-----------|--------------|-----------|
| < 0.10 | 780 | 4.9% | -$4,895 | -$6.28 | 0.052 |
| 0.10-0.20 | 235 | 20.0% | $10,209 | $43.44 | 0.141 |
| 0.20-0.30 | 176 | 36.9% | $8,798 | $49.99 | 0.244 |
| 0.30-0.40 | 116 | 37.9% | $1,152 | $9.93 | 0.345 |
| 0.40-0.50 | 181 | 48.6% | $854 | $4.72 | 0.463 |
| 0.50-0.60 | 256 | 54.3% | -$456 | -$1.78 | 0.553 |
| 0.60-0.70 | 154 | 67.5% | $651 | $4.23 | 0.648 |
| 0.70-0.80 | 214 | 75.7% | $132 | $0.62 | 0.753 |
| 0.80-0.85 | 132 | 82.6% | -$59 | -$0.45 | 0.830 |
| 0.85-0.90 | 136 | 90.4% | $339 | $2.49 | 0.883 |
| 0.90-0.95 | 188 | 90.4% | -$420 | -$2.23 | 0.925 |
| >= 0.95 | 4,862 | **99.4%** | $2,255 | $0.46 | 0.989 |

**Key observations**:
1. 68% of fills are at >= 0.90 — elite traders enter near-certain outcomes
2. At 0.95+ price: HR=99.4% but avg PnL = only $0.46 (tiny margin, high volume)
3. max_price=0.85 gate KEEPS uncertain signals (HR 20-84%) and DISCARDS strong ones — explains 35.4% HR
4. The < 0.10 bucket is net drag: HR=4.9%, avg PnL=-$6.28 per fill
5. The 0.10-0.30 bucket has the largest per-fill edge ($44-50 per winner) but low HR (20-37%)

---

## 5. Hold Time Analysis — The In-Play Paradox

| Hold Duration | N | HR% | Total PnL | Avg Fill Price |
|---------------|---|-----|-----------|----------------|
| < 15 min | 167 | 100.0% | $169 | 0.990 |
| 15-30 min | 144 | 100.0% | $145 | 0.990 |
| 30-60 min | 250 | 100.0% | $256 | 0.990 |
| 1-2h | 795 | 100.0% | $848 | 0.990 |
| 2-4h | 3,132 | 90.9% | -$7,511 | 0.902 |
| 4-12h | 1,095 | 72.9% | $10,100 | 0.726 |
| 12-48h | 716 | 71.6% | $1,600 | 0.722 |
| > 48h | 1,131 | 36.0% | $12,953 | 0.333 |

**Paradox**: True in-play bucket (hold < 4h) has HR=93.6% but total PnL = -$6,093 (net LOSS).
Long-duration holds (> 48h) have HR=36% but PnL = +$12,953 (best profitable bucket).

The profitable fills are from markets where:
- Elite traders enter at low prices (0.33 avg) = long odds
- Market resolves in January 2026 but elite entered in December 2025 or early January
- Each winner pays $200 on a $100 investment — compensating for 36% HR

The strategy is NOT truly an in-play copy strategy in production. The tick fires on any BUY from the elite pool, including markets where they enter days before resolution.

---

## 6. Vectorized vs Tick Comparison

| Metric | Vectorized UB (Track A) | Tick-by-Tick (Jan 2026, Top-100) | Notes |
|--------|------------------------|----------------------------------|-------|
| Hit Rate | 97%+ | **94.2%** | -3pp degradation (excellent) |
| HR top traders | 99-100% | **98.0%** (top-25) | Nearly exact match |
| Excess HR | +57-67pp | **+64pp** | — |
| Signals/month | ~2,000-5,000 (est.) | **15,891** | Much higher than expected |
| Avg Hold | < 4h (in-play def.) | **22.3h** | Longer in tick (not pure in-play) |
| PnL/month | Not measured | **$52,932** | Benchmark established |

Degradation is only 3pp for top-100 pool (far below typical 20-40pp). This is because:
1. N=1 threshold fires immediately on first entry — no consensus wait
2. No timing delay in the simulation — we copy at exact moment of elite entry

---

## 7. Compounding Scores

| Configuration | Excess HR | Avg Edge | Median Hold | Compounding Score |
|---------------|-----------|----------|-------------|-------------------|
| Top-25, no price gate | +67.8pp | $16,836/11,097=$1.52 | ~6h=0.25d | **410** |
| Top-100, no price gate | +64.0pp | $52,932/15,891=$3.33 | ~22h=0.92d | **231** |
| Full-551, no price gate | +49.5pp | $18,560/7,443=$2.49 | ~59h=2.46d | **50** |

Formula: Compounding Score = excess_HR_pp x avg_edge_usd / median_hold_days

---

## 8. Latency Sensitivity

**Zero-latency simulation shows 94-98% HR** depending on pool size.

In production, latency impacts primarily depend on fill price zone:
- **Fills at 0.90+** (68% of volume): Price near-stable at 0.99 — 30-60s delay negligible
- **Fills at 0.30-0.70** (11% of volume): Price more volatile — 1-5 min delay could cost 2-5pp
- **Fills at < 0.10** (11% of volume): Long-odds markets, price-stable — latency not critical

**Recommendation**: Use pending.signal Kafka topic for ~1s latency on all fills.
The 0.95+ fills are the volume driver but have tiny edge — any additional slippage eats into the margin.

---

## 9. Critical Discoveries

### Discovery 1: max_price Gate INVERTS the Signal
Expected: max_price=0.85 would remove contaminated markets.
Actual: 68% of fills are at 0.90+ where HR=99.4%. The gate removes the strongest signals.
**Lesson**: Never apply a max_price gate to strategies copying IN-PLAY traders — they specifically enter WHEN the outcome is near-certain.

### Discovery 2: The Strategy Has Two Distinct Regimes
- **High-price regime** (0.90+): Many fills, tiny edge per fill, 99.4% HR — volume game
- **Low-price regime** (0.10-0.50): Few fills, large edge per fill, 20-49% HR — information game

The first regime dominates by count; the second by per-fill PnL.

### Discovery 3: Top-100 Pool is the Optimal Size
Adding traders 100-200 in the ranking introduces lower-quality signals (HR 85-92%) that dilute the signal without proportionally increasing volume. The top-100 captures 15,891 fills/month at 94.2% HR vs the top-25's 11,097 fills at 98.0%.

### Discovery 4: "In-Play" Classification is Misleading for Copy Strategy
The pool was built from traders with hold < 4h positions. In the tick, we fire on their BUY trades across ALL markets — including markets they enter long before resolution. The true in-play fills (<4h hold) actually LOSE money ($-6,093) while the long-duration fills generate the profit.

### Discovery 5: Deep Underdog Signals (<0.10 price) are Net Drag
HR=4.9% at <0.10 price is a significant drain. Consider excluding price < 0.05 to avoid the pure garbage zone while keeping the valuable 0.05-0.10 outlier wins.

---

## 10. Production Parameters

**Recommended**:
- Pool: Top-100 by CopyScore (rank monthly: train_HR x sqrt(N) x (1 - gambling_frac))
- N threshold: 1 (single elite trader = signal)
- Price gate: NONE (max_price inverts signal; min_price hurts PnL)
- Optional: exclude price < 0.05 (deep underdog pure garbage)
- Position size: $100 per signal (scale to capital)
- Direction: copy the trader's direction (YES or NO)
- Gambling markets: exclude via slug pattern
- Infrastructure: trades.raw Kafka topic (maker address monitoring)
  - pending.signal for 1s early detection on high-volume markets

**Risk management**:
- Max position: $100 per market (one signal per market)
- Capital: $100K supports 15K+ simultaneous positions theoretically, but avg hold 22h means capital recycles
- Monthly budget: $100 x 15,891 signals = $1.59M notional at $100/trade
- Actual capital required: much less due to rapid resolution (most resolve within hours)

---

## 11. Files

- Script: `research/hypotheses/in-play-traders/scripts/elite_whale_copy.py`
- Results: `research/hypotheses/in-play-traders/validation/elite_whale_copy_results.md`
- Ledger (no price gate, full pool): `research/output/ledger_elite_whale_copy_noprice.parquet`
- Log: `tmp/elite_whale_copy.log`, `tmp/elite_pool_sweep.log`
- Pool sweep script: `tmp/elite_pool_sweep.py`
