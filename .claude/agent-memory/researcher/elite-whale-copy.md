# Elite Whale Copy — Tick-by-Tick Validation (2026-03-08)

## Configuration
- Pool: Top-551 elite traders (train <2026-01-01, >=50 in-play pos, >=80% HR, >=5 med vol, <90% gambling)
- Strategy: N=1, copy first BUY from pool trader in non-gambling market, no price gate
- Test: January 2026, 25,354 markets

## Pool Size Sweep Results (No Price Gate)

| K | Fills | HR% | PnL | Sharpe | Avg Hold |
|---|-------|-----|-----|--------|----------|
| 25 | 11,097 | 98.0% | $16,836 | 0.87 | 5.95h |
| 50 | 15,225 | 96.2% | $34,924 | 0.71 | 18.63h |
| **100** | **15,891** | **94.2%** | **$52,932** | **0.72** | **22.29h** |
| 200 | 13,439 | 90.1% | $39,635 | 0.49 | 29.63h |
| 551 | 7,443 | 79.7% | $18,560 | 0.19 | 58.65h |

**Best: Top-100 pool, no price gate.**

## CRITICAL: max_price Gate DESTROYS the Signal

max_price=0.85 drops HR from 79.7% to 35.4% (below base rate).
Root cause: 68% of fills are at price >=0.90 where HR=99.4%.
The gate REMOVES the strongest signals and KEEPS the weakest.

**NEVER apply max_price gate to strategies copying in-play traders.**

## Fill Price Distribution

- 68% fills at >=0.90: HR=99.4%, avg PnL=$0.46/fill (tiny edge, high volume)
- 11% fills at 0.10-0.30: HR=20-37%, avg PnL=$44-50/fill (large edge, low volume)
- 11% fills at <0.10: HR=4.9%, avg PnL=-$6.28/fill (net drag)

## The In-Play Paradox

- Pool classified as "in-play" (hold <4h) but tick hold is 22h avg
- True in-play fills (hold <4h): HR=93.6% but PnL=-$6,093 (net LOSS)
- Long-duration fills (hold >48h): HR=36% but PnL=+$12,953 (profitable due to 3x payoffs)
- Explanation: tick fires on ALL trades from pool, not just in-play-classified ones

## Ledger Schema Note

The `outcome` column in LedgerRecord contains the WINNING OUTCOME LABEL (team name, YES, NO),
NOT "won"/"lost". Use `pnl_net > 0` to determine wins and `pnl_net < 0` for losses.

## Compounding Scores

- Top-100, no gate: excess_hr=+64pp, avg_edge=$3.33, median_hold=0.92d → CS=231
- Top-25, no gate: excess_hr=+68pp, avg_edge=$1.52, median_hold=0.25d → CS=410 (but tiny signals)

## Production Parameters

- Pool: Top-100 by CopyScore, re-rank monthly
- N threshold: 1
- Price gate: NONE (or optionally exclude <0.05 for deep garbage)
- Infrastructure: trades.raw Kafka, maker address monitoring
- Position size: $100/signal

## Vectorized vs Tick Degradation

Only 3pp degradation (94.2% tick vs ~97% vectorized).
Very low because N=1 fires immediately — no consensus wait penalty.
