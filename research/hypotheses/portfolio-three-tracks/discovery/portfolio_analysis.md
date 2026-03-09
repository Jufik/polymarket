# Portfolio of Three Tracks: Sports YES + Politics NO + InPlay

> **TL;DR**: Drop InPlay. Run Sports YES + Politics NO as a 2-track portfolio.
> Combined Sharpe 7.76, max drawdown $4,339, return 35x on $5K budget over 10 months.

> [!CRITICAL]
> Sports YES and InPlay share 99.5% of markets with identical direction (YES).
> InPlay adds **negative incremental value** (-$482 on exclusive markets, degrading).
> Including InPlay creates unintended double exposure, not diversification.

## 1. Correlation Matrix

Daily PnL correlations (overlap period 2025-05-21 to 2026-02-28, 284 days):

|              | Sports YES | Politics NO | InPlay  |
|--------------|-----------|-------------|---------|
| Sports YES   | 1.000     | +0.089      | -0.059  |
| Politics NO  | +0.089    | 1.000       | -0.012  |
| InPlay       | -0.059    | -0.012      | 1.000   |

Near-zero correlations across all pairs. The low Sports/InPlay correlation (-0.06) is
misleading -- they trade the same markets but InPlay has many extra fills on exclusive
markets that drag down its PnL independently.

## 2. Per-Track Summary

| Metric              | Sports YES  | Politics NO | InPlay      |
|---------------------|-------------|-------------|-------------|
| Fills               | 2,023       | 347         | 5,936       |
| HR                  | 63.3%       | 82.9%       | 60.2%       |
| Total PnL           | $162,157    | $33,942     | $9,992      |
| Avg PnL/fill        | $80.16      | $98.10      | $1.68       |
| Median hold (days)  | 0.2         | 7.5         | 0.2         |
| Avg open positions  | 6.1         | 40.6        | 20.9        |
| Avg capital deployed| $611        | $4,062      | $2,088      |
| ROC (PnL/$1)        | $265.40     | $7.93       | $4.79       |
| Ann. Sharpe (overlap)| 7.00       | 4.04        | 0.88        |

## 3. Critical Finding: Sports/InPlay Double Exposure

- 99.5% of Sports YES markets also appear in InPlay (2,013 of 2,023)
- Both buy YES on the same condition_id
- InPlay enters 9h earlier on 70% of shared markets (weaker n>=1 signal vs Sports n>=2)
- Same HR (59.4%) but Sports has 12x higher avg PnL ($107 vs $9) on shared markets
- InPlay's exclusive markets (3,917 fills): HR 58.7%, PnL = **-$482**, degrading fast
  - Jan 2026: -$4,180
  - Feb 2026: -$7,350

**InPlay's $10K total PnL comes entirely from shared markets that Sports also captures.**
Its true incremental contribution is -$482.

## 4. Marginal Sharpe Contribution

| Portfolio             | Sharpe |
|-----------------------|--------|
| Sports + Politics + InPlay | 7.57   |
| Sports + Politics     | 7.76   |
| Sports + InPlay       | 6.84   |
| Sports only           | 7.00   |
| Politics + InPlay     | 3.06   |

- Sports contributes +4.51 Sharpe points
- Politics contributes +0.73 Sharpe points
- InPlay contributes **-0.15** Sharpe points (hurts the portfolio)

## 5. Monthly PnL Overlap

| Month   | Sports   | Politics | InPlay   | Combined | Note          |
|---------|----------|----------|----------|----------|---------------|
| 2025-06 | -$100    | $0       | -$521    | -$621    | 2 neg         |
| 2025-07 | $4,432   | $7,122   | $3,716   | $15,270  |               |
| 2025-08 | $7,768   | $3,828   | $7,931   | $19,527  |               |
| 2025-09 | -$2,193  | $1,062   | $4,344   | $3,212   |               |
| 2025-10 | $7,042   | $1,258   | $2,105   | $10,405  |               |
| 2025-11 | $18,195  | $3,902   | $3,735   | $25,832  |               |
| 2025-12 | $24,305  | -$167    | $857     | $24,994  |               |
| 2026-01 | $52,150  | $15,732  | -$6,727  | $61,155  |               |
| 2026-02 | $50,491  | -$521    | -$5,513  | $44,458  | 2 neg         |

- **Zero months where all 3 are negative**
- **Zero months where Sports+Politics are both negative**
- Sports and Politics compensate each other (Sep, Dec, Feb)

## 6. Position Concurrency

| Month   | Sports | Politics | InPlay | Total | Capital  |
|---------|--------|----------|--------|-------|----------|
| 2025-07 | 11.9   | 50.3     | 25.5   | 87.7  | $8,768   |
| 2025-08 | 17.8   | 50.5     | 31.2   | 99.5  | $9,948   |
| 2025-09 | 17.7   | 50.3     | 41.4   | 109.4 | $10,937  |
| 2025-12 | 11.0   | 50.1     | 48.9   | 110.0 | $10,997  |

Politics dominates capital usage due to long hold times (median 7.5 days, up to 57 open).
The 3-track portfolio needs $11K+ to avoid capital constraints.

## 7. Constrained Simulation Results

### 2-Track (Sports + Politics)

| Config          | S fills | P fills | Total PnL  | Sharpe | Max DD    |
|-----------------|---------|---------|------------|--------|-----------|
| $5K S=20 P=20   | 2,023   | 197     | $174,803   | 7.76   | -$4,339   |
| $5K S=25 P=5    | 2,023   | 65      | $169,270   | 7.74   | -$4,378   |
| $10K S=50 P=50  | 2,023   | 346     | $196,099   | 7.88   | -$3,758   |

Key observations:
- Sports never hits position limit (max 20 open, peaks at ~39 but unconstrained already)
- **Sports never gets rejected** -- all 2,023 fills accepted even at $5K budget
- Politics is the constrained track: 197 of 346 accepted at P=20
- Increasing Politics limits improves PnL but marginally affects Sharpe

### 3-Track (with InPlay)

| Config              | Total PnL  | Sharpe | Note                |
|---------------------|------------|--------|---------------------|
| $5K S=10 P=20 I=20  | $190,342   | 7.71   | Sports restricted   |
| $5K S=20 P=10 I=20  | $186,524   | 7.65   |                     |
| Unconstrained       | $206,091   | 7.66   | Needs $16K+ capital |

The 3-track configs that look better ($190K) actually restrict Sports to make room for InPlay,
trading the best signal for more of the worst one.

## 8. Capital Allocation

### Kelly Weights (mean/variance)
- Sports YES: 34%
- Politics NO: 57%
- InPlay: 9%

### Risk Parity Weights (inverse volatility)
- Sports YES: 17%
- Politics NO: 49%
- InPlay: 34%

### Practical Recommendation
With $5K budget:
- $3K headroom for Sports (uses $600 avg, $2K peak)
- $2K headroom for Politics (20 positions x $100)
- $0 for InPlay

## 9. Recommended Portfolio Config

```toml
[portfolio]
total_budget_usd = 5000

[sports_yes_v3]
enabled = true
max_open_positions = 20
position_size_usd = 100
# Never capital-constrained. All 2,023 signals filled at $5K.

[politics_no_v3]
enabled = true
max_open_positions = 20
position_size_usd = 100
# 197 of 346 signals filled. Long hold times (7.5d median) are the bottleneck.

[sports_inplay_v3]
enabled = false
# EXCLUDED: 99.5% market overlap with Sports YES, negative incremental PnL,
# degrading performance on exclusive markets (Jan: -$4K, Feb: -$7K).
```

### Expected Performance
- PnL: ~$175K over 10 months ($17.5K/month avg)
- Sharpe: 7.76 (annualized)
- Max drawdown: $4,339 (0.87x budget)
- Return on capital: 35x budget
- Win streaks: up to 16 days, avg 2.6 days
- Loss streaks: up to 6 days, avg 1.8 days

### Scaling Path
| Budget | Config          | PnL    | Sharpe | Notes                          |
|--------|-----------------|--------|--------|--------------------------------|
| $5K    | S=20 P=20       | $175K  | 7.76   | Baseline                       |
| $10K   | S=50 P=50       | $196K  | 7.88   | +12% PnL, all fills accepted   |
| $15K   | Unconstrained   | $196K  | 7.88   | Same as $10K (no more signals) |

**Bottleneck is signal generation (2,023 + 346 fills), not capital.**
Beyond $10K, capital is idle. Better to find new uncorrelated tracks.

## 10. Future Ideas

1. **InPlay as early trigger for Sports**: Enter when InPlay fires AND Sports consensus >= 2.
   Could capture InPlay's 9h timing advantage with Sports quality filter.

2. **New uncorrelated tracks**: Culture, Weather, Finance -- need separate validation.
   Politics already provides the diversification benefit.

3. **Position sizing**: Variable sizing by conviction (consensus count, fill price).
   Current $100 flat is suboptimal for high-HR Sports positions.

4. **Politics hold time reduction**: Active exit on price movement could free capital faster
   and allow more Politics fills (currently rejecting 43% due to position limits).
