# Multi-Outcome Event NO Base Rate Trap

> **TL;DR**: Events with N markets have N-1 NO resolutions. NO base rate is 72.4% (not 62%) for multi-market events. Naive excess HR calculations overestimate edge by ~10pp.

> [!WARNING]
> When computing excess HR for strategies on multi-market events (same event_id, multiple condition_ids), use the 72.4% multi-outcome NO base rate, NOT the global 62%. Failing to adjust inflates apparent edge by ~10pp.

## Finding

Multi-market events (e.g., "Who wins the election?" with candidates A, B, C, ...) have a structural NO bias: if there are N candidates, exactly 1 resolves YES and N-1 resolve NO.

- Global NO base rate: 62.1% (across all 390K markets)
- Multi-outcome event NO base rate: **72.4%** (across 172K markets in 32K events)
- Delta: **+10.3pp** — strategies that don't adjust will appear to have 10pp more edge than they actually do

Example: A strategy showing 77.5% NO HR on multi-market events appears to have +15.4pp excess (vs 62.1% global). Actual excess is only **+5.1pp** (vs 72.4% multi-outcome base).

## Evidence

H6 Cross-Market Flow analysis (2026-03-04):
- 762K aggressive events across 3,639 events, 15,583 markets (Jun 2024 - Jun 2025)
- Naive NO HR: 77.5% → apparent excess +15.4pp vs global base
- Price-adjusted NO base rate for these markets: 72.4% → actual excess +5.1pp

## Impact

- Always check if strategy universe is dominated by multi-outcome events
- Compute event-specific NO base rate: `(N_markets - 1) / N_markets` per event, then average
- Applies to: cross-market strategies, event-level analysis, any signal on multi-outcome events
- Does NOT apply to: binary YES/NO markets (single condition_id per event)

## Related

- `data/market_base_rates.md` — global base rates (38.1% YES / 61.9% NO)
- `data/price_efficiency_meta.md` — Polymarket efficiency meta-finding
- `pitfalls/excess_hr_vs_absolute_hr.md` — related base rate trap

## Tags

`base-rate`, `multi-outcome`, `pitfall`, `excess-hr`
