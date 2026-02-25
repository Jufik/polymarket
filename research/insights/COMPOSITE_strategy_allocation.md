# Composite Strategy: Capital Allocation & Interactions

**Source insights**: copy/15 (composite next steps), copy/05 (strategy synthesis)

---

## Active Strategies

| # | Strategy | Direction | Signal | Confidence | Capital |
|---|----------|-----------|--------|:----------:|--------:|
| S1 | Proportional Copy | Follows trader | Sizing alpha from graded pool | HIGH | $1,000 |
| S2a | Will NO | Always NO | Market structure (fav-longshot) | HIGH | $300 |
| S2b | Crypto OTM NO | Always NO | Structural OTM premium | HIGH | (shares w/ S2a) |
| S3 | Consensus Copy | NO-only | Crowd direction from pool | MEDIUM | $200 |
| MR | Mean Reversion | Reversal | 2-streak cross-window | HIGH (stat.) | $200-500 |

---

## How They Interact

| | S1 Proportional | S2a Will NO | S2b Crypto OTM | S3 Consensus | MR Mean Rev |
|---|:---:|:---:|:---:|:---:|:---:|
| Signal source | Individual ROI | Market structure | OTM premium | Crowd NO direction | Price pattern |
| Markets | All pool trades | "Will" binary | Crypto checkpoints | Consensus-NO mkts | Up/Down markets |
| Overlap | LOW w/ S3 | NONE w/ S1 | NONE w/ S2a | LOW w/ S1 | NONE w/ all |

All strategies are **largely orthogonal**. S2a and S2b target completely different market types than S1/S3. Mean reversion targets Up/Down markets (none of the others touch these).

---

## Capital Allocation ($1,500 initial)

| Priority | Strategy | Capital | Rationale |
|:--------:|----------|--------:|-----------|
| 1 | S1: Proportional copy | $1,000 | Highest confidence, compounds |
| 2 | S2a/S2b: Fav-longshot + Crypto OTM NO | $300 | Independent edge, fast rotation |
| 3 | S3: Consensus NO | $200 | Building validation data |

### As Capital Grows

- **$3K+**: Scale S2a/S2b to $1K-2K (still linear scaling regime)
- **$5K+**: Add mean reversion at $200-500 (validate liquidity first)
- **$10K+**: S2a/S2b near saturation ($6,700/mo ceiling). Redirect surplus to S1.

---

## Strategy Ranking by Risk-Adjusted Returns

| Strategy | HR | $/bet | $/month ($1K) | Lockup | Data period |
|----------|:---:|:---:|:---:|:---:|:---:|
| S2b Crypto OTM NO | **98.9%** | $12.72 | $802 | 4-8 hours | 7 months |
| S2a Will NO | 75.3% | $4.96 | $968 (upper) | 6.5 days | 14 months |
| MR 15-min | 53.1% | $3.13 | $13,430 (upper) | 15 min | 5 months |
| S1 Proportional Copy | ~55% | varies | ~$700 (50% haircut) | days | 9 months |
| S3 Consensus NO | ~50% | ~$50 | ~$50-100 | days | 2 months |

S2b has the best risk profile (98.9% HR). Mean reversion has the highest raw throughput but needs liquidity validation. S1 is the most battle-tested with the strongest theoretical foundation.

---

## Validation Backlog

### Before Scaling
1. **MR liquidity validation**: Do 15-min markets have $100+ volume at ~50c?
2. **S1+S2a overlap**: When S1's pool trades "Will" binary markets, does S2a add independent edge?
3. **S3 extended holdout**: Validate across 6+ windows (currently only 2)
4. **Combined equity curve**: Simulate all strategies running simultaneously

### Live Execution Prep
5. **Execution price validation**: Compare backtest entry prices vs CLOB API achievable prices
6. **Trader detection latency**: How fast can we detect pool trader positions? (WS vs polling)
7. **Market identification for S2a**: Automate "Will" question detection from market text
8. **Capacity testing**: Does $1,500 move prices in S1's markets?

---

## Key Numbers to Remember

- **S1**: $1,500 → $7,695 in 9 months (+413%), 8/9 months positive, 8.1% max DD
- **S2a**: $300 → ~$690/mo (realistic), ceiling $6,700/mo at $10K
- **S2b**: $1K → $802/mo, 98.9% HR, zero losing months
- **MR**: $200 → potentially $4K-13K/mo (upper bounds, needs validation)
- **S3**: $200, building data, ~$50-100/mo

---

## What Does NOT Work (Summary of Dead Ends)

| Strategy | Why Killed |
|----------|-----------|
| S4 Anti-YES | Zero capacity (0-2 bets/month, $6.50/bet) |
| S5 Informed MM | Signal too weak (49% HR), spread can't rescue |
| Up/Down directional | All 7 strategies fail — market is efficient |
| Kelly allocation | Underperforms equal-weight for pre-filtered pools |
| YES-only consensus | Strongly anti-predictive (18.5% HR vs 38.1% base) |
| NO on neg_risk | Perfectly calibrated at 1/N, no excess edge |
