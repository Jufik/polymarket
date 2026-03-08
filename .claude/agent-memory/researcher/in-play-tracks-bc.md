# In-Play Traders: Track B and Track C Results

## Track B: In-Play Consensus Discovery (2026-03-07)

**Hypothesis**: N>=3 traders entering in final 2-4h = predictive consensus. REJECTED as general strategy.

**Key results** (DuckDB sweep, 342K valid markets, 13.1M positions, 192 combos):
- Train best: n=5 hold=2h -> HR=63.5% (+34pp excess) — 238 signals
- Test OOS: n=5 -> HR=29.4% (+0.4pp excess) — COMPLETE COLLAPSE (train n=238 too small)
- Test n=2 hold=2h: HR=46.0% (+17pp) — partial persistence but not enough post-tick-degradation
- Test n=3 hold=2h: HR=39.6% (+11pp) — also insufficient after 20-40pp tick drop
- Hold window: 4h window = noise (HR=28.1%, near base 29%). Signal lives in <=2h.
- Price gate (0.70/0.80/0.85): NO EFFECT — median entry price is always 0.50 (50-50 markets)
- NO direction: train weak, test collapses (52% vs 71% base = -19pp OOS)

**Tag breakdown highlights** (hold=2h, n=3, gate=0.80, YES):
- EPL: 188 sigs, HR=90.4%, med_vol=$2,882 — smart money at scale
- Crypto: 156 sigs, HR=82.1%, med_vol=$5,023
- Bitcoin: 98 sigs, HR=88.8%, med_vol=$22,733
- Earnings: 60 sigs, HR=96.7%, med_vol=$788
- Games/Esports: HR=28-36% — noise (low volume fans/watchers)
- Volume is the discriminator, not the tag

**DuckDB schema gotcha**: markets.status = 'closed' (not 'resolved'). Always use 'closed'.

**Spawned sub-study**: Track B.2 — High-Liquidity In-Play Consensus
- Restrict to: EPL, Crypto, Bitcoin, Earnings, Breaking News, Geopolitics
- Require: signal_time_vol >= $500, hold<=2h, n>=3
- Rationale: these tags show 70-97% train HR with $2K-$22K median volumes

---

## Track C: Scalper Alpha (2026-03-07)

Full results: `research/hypotheses/in-play-traders/discovery/track_c_results.md`
Script: `research/hypotheses/in-play-traders/scripts/track_c_scalpers.py`

### Population (Full 2025, raw trades)
- 230K unique scalpers, 2.48M scalp events, $1.14B total sell volume
- Scalp = BUY then SELL same (condition_id, asset_id) within 24h
- Median scalp time: 43 minutes; median spread: +0.51%
- 39% of scalps at a loss; 35% earn >2% spread profit

### Scalper BUY Entry HR
| Signal | HR | Base Rate | Excess |
|---|---|---|---|
| All YES scalpers | 39.12% | 39% | +0.1pp (flat) |
| All NO scalpers | 67.06% | 62% | +5.1pp |
| YES, price-gated 0.10-0.80 | 44.92% | 39% | +5.9pp |
| YES, high-edge pool (HR>=55%, >=10 scalps, price-gated) | 58.55% | 39% | +19.6pp UB |

### Critical Caveats
- Pool selection used SAME 2025 period — true OOS excess will be materially lower (expect -10 to -15pp degradation from selection bias alone)
- Edge persistence: scalper-class traders show 37.93% held-position YES HR (below base 39%) — edge is market-specific, not trader-generic
- In-play contamination: <1h to resolution has 69.78% HR (contaminated); gate must be >4h to resolution
- Profitable scalpers have LOWER directional HR: scalp profit and directional skill are anti-correlated

### Tag Leaders (scalper BUY entry HR)
- Trump Presidency: 58.99% (3.58h scalp time, very low spread)
- MLB: 57.29% (3.08h scalp time)
- World events: 56.64%
- Politics: 55.47%
- Crypto/1H/15M: 51-56% (market-making activity, not directional)

### Market-Level Alternative Signal
- Scalped markets have +8.7pp YES excess HR (47.80% vs 39.12% baseline)
- Scalper concentration = market quality signal (could be used as consensus trigger instead of individual copy)

### Strategy Design
1. Pool: >=10 price-gated scalps (token price 0.10-0.80, YES), trailing 90d, scalp entry HR >= 55%
2. Signal: pool-member BUYs YES token at price 0.10-0.80
3. Gate: >4h to resolution, buy_amount >= $10
4. Hold to resolution; do NOT copy SELL
5. CS estimate: 0.30-1.50 depending on tick validation outcome

### Verdict
CONDITIONAL GO. Requires tick-by-tick validation. Primary risk: same-period pool selection bias may eliminate most/all excess in OOS test.
