# Politics NO Active Exit — Discovery Notes

## Classification Status

No classifications used. This sweep uses the existing tick-validated ledger
(`research/output/ledger_politics_no_v3_k100_n2.parquet`) as input — pool qualification
and consensus filtering were already applied during the tick-validation phase.

## SELL Dual-Test Status

Not applicable. This sweep concerns exit policy (when to close long NO positions),
not entry signal direction. All positions are BUY NO entries from the Politics NO v3
K=100 N=2 strategy. SELL handling affects entry pool construction (upstream), not exit.

## Key Surprising Findings

### 1. Exit@20% is not the P=20 winner despite best ROC/day

Exit@20% has ROC/day = 0.08056 (best) but generates P=20 PnL = $33,696 — LESS than
Exit@50% at $37,229. The reason: at P=20 with Exit@20%, we accept 341 fills at $97.61
avg PnL. With Exit@50%, we accept 322 fills at $115.62 avg PnL. The higher per-position
PnL of Exit@50% compensates for 19 fewer fills.

This creates a decision fork: ROC/day maximization (for portfolio compounding) vs
total PnL at fixed capital.

### 2. Very expensive bucket (0.90+) has NEGATIVE hold PnL

190 positions entered at 0.90+ price have -$1,368 total hold PnL (-$7.20 avg).
At 0.90 fill, tokens = $100/0.90 = 111 tokens. Win payout = $11.11. Loss = $100.
Break-even HR = 90%. Actual HR for this bucket = 89.5% → net negative even though
the "win rate" looks high.

Early exit at 80% threshold rescues these: exit at 0.90 + 0.80*(1-0.90) = 0.98.
These markets almost always reach 0.98 before resolution (monotonic price path).

### 3. Price trajectory allows all thresholds to fire on winning positions

From the trade tape, 100% of WON positions reach 50% of max payout (per existing
analysis). This confirms the oracle assumption — every exit threshold is reachable
on winners, limited only by timing (how fast price moves).

### 4. Adaptive (bucket-conditional) rules add no value

The best bucket-conditional config (adaptive_v1: Longshot@50%, mid@30%, fav@25%)
generates $38,381 unconstrained vs uniform Exit@50% at $38,354. Difference = $27.
The complexity of maintaining multiple thresholds is not justified.

### 5. Time-gating (min hold) hurts capital efficiency

Exit@50%_min3d: P=20 PnL = $34,883 vs $37,229 for min0d. Adding a 3-day minimum
hold reduces accepted signals (312 vs 322) because positions held longer occupy slots
longer, causing more rejections. The per-position PnL slightly improves (3d wait
avoids some early misidentified exits) but the capacity loss dominates.

## Methodology Notes

### Price Oracle Quality

Used hourly VWAP from `trades` table (YES side), converted to NO price as `1 - YES_price`.
This is a strong approximation because:
- Hourly granularity may miss sub-hour exit opportunities (slightly pessimistic)
- VWAP may differ from best_bid (actual exit trigger uses best_bid, not last trade)
- In practice, best_bid ≈ last trade price for liquid politics markets

The hourly oracle likely UNDERSTATES exit opportunities slightly, making these
results conservative relative to the true vectorized upper bound.

### Capital Constraint Simulation

The heapq-based P=20 simulation is time-ordered and accurate. Positions are accepted
if `len(open_positions) < P` at signal_time, and positions close at
`signal_time + hold_days`.

One limitation: simultaneous signals at the same timestamp may be ordered arbitrarily.
In production, the strategy fires on the Nth qualified trader's trade, which is
already serialized in time.

## Spawned Ideas

1. **Trailing stop on high-exit positions**: After early exit at 20-30%, re-enter if
   price reverses below fill_price + 10%*(1-fill_price). Capture the full move
   in two legs.

2. **Dynamic P based on exit strategy**: If using Exit@20%, run P=10 instead of P=20
   (same capital utilization as Hold@P=20). Frees $1,000 for Esports YES track.

3. **Exit threshold by market age**: Use Exit@30% within first 7 days (avoid early
   reversals), then tighten to Exit@20% as market ages (price path increasingly
   monotonic near resolution).

4. **Portfolio-aware exit**: Monitor total open positions across all strategies.
   When portfolio is full, trigger early exits at looser threshold (Exit@20%) to
   accept incoming signals. When slots are free, allow higher threshold.

5. **Tick-validate Exit@20% vs Exit@50%**: The current analysis is vectorized (upper
   bounds). Tick validation with RealisticFillSimulator would measure actual fill
   feasibility for exit orders and true PnL impact.

## Architectural Parameters for Executor

The Executor should implement active exit as a configurable policy:

```python
@dataclass
class ExitPolicy:
    exit_pct: float = 0.0          # 0.0 = disabled (hold to resolution)
    min_hold_days: float = 0.0     # minimum hold before exit activates
    hold_when_close_days: float = 0.0  # hold to resolution if resolves within X days
    # price_bucket_rules: dict[tuple, float] = None  # NOT RECOMMENDED
```

Trigger: `best_bid_NO >= fill_price + exit_pct * (1.0 - fill_price)`
Price source: CLOB WS orderbook (best_bid for NO token)
Order type: Market sell (or limit sell at target for maker rebate)

## Files

- `exit_sweep_analysis.md` — full markdown report (UPPER BOUNDS)
- `exit_sweep_results.json` — machine-readable results
- `/mnt/nvme/git/polymarket/polymarket/tmp/exit_sweep.py` — sweep script
- `/mnt/nvme/git/polymarket/polymarket/tmp/exit_sweep.log` — execution log
