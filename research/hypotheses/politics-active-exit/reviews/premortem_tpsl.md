# Skeptic Review: politics-active-exit — Pre-mortem TP/SL Design (Round 1)

Reviewer: Skeptic agent
Date: 2026-03-09
Scope: Pre-mortem of the two-layer TP/SL / demand-driven eviction design for
Politics NO v3 high-price entries (>0.80).

---

## 6-Point Checklist

### 1. Look-ahead Bias: PARTIAL FAIL

The exit sweep (`discovery/exit_sweep_analysis.md`) explicitly uses hourly VWAP
from the trade tape as the price oracle for exit triggers. The notes document
(`discovery/notes.md`, "Price Oracle Quality") acknowledges this:

> "Used hourly VWAP from `trades` table (YES side), converted to NO price as
> `1 - YES_price`."

The oracle is not look-ahead in the traditional sense (no future resolution data
feeds into the entry signal). However there is a structural look-ahead embedded
in the exit trigger logic:

- The trigger `best_bid_NO >= fill_price + X * (1 - fill_price)` is tested
  against **hourly VWAP**, which is the average of trades over the entire hour.
  The exit could only trigger at the *start* of the hour if the price was at
  the VWAP throughout. In reality, the target price might be touched intra-hour
  but the VWAP falls short, or vice versa.
- The notes say hourly granularity "may miss sub-hour exit opportunities
  (slightly pessimistic)" — but the inverse is also true: an hour where the
  price briefly spikes to the target, then retreats, would trigger an exit in
  the backtest that is not executable in production.

> [!WARNING]
> The hourly VWAP oracle introduces micro-look-ahead: if the target is touched
> intra-hour but VWAP falls short, the backtest misses exits (conservative), but
> if VWAP meets the threshold via a brief spike that is then mean-reverted, exits
> are counted that would not fill cleanly at that price in production. Tick-by-tick
> validation with real orderbook best_bid is required to measure the true false-
> trigger rate. `discovery/notes.md` line 61-65.

The second concern is more pointed: in the `analysis.md` table (section 3),
"WON Positions — % Reached" shows 100% of winning positions reach 25%/50%/75%
of max payout. This statistic is computed on the full trade tape including trades
that occur AFTER the point in time the exit would fire. It confirms the path
exists, but does not confirm the bid was available at the exact trigger moment
(liquidity could be absent even when a trade at that price eventually occurs).

### 2. Survivorship Bias: PASS (with caveat)

The 346-position universe is drawn from the tick-validated ledger for Politics NO
v3 K=100 N=2, covering 2025-07-01 to 2026-03-01. Only resolved markets are
included, which is necessary and correct for PnL evaluation. No post-hoc volume
or quality filters are noted.

> [!TIP]
> Confirm that the ledger excludes markets that are still open as of 2026-03-01
> and not merely omitted because they are "unresolved." If any politically
> significant markets resolved late (post-snapshot) and were quietly excluded,
> the -$1,368 drag from 0.90+ entries could be understated.

### 3. Edge Above Base Rate: WARNING

From `exit_sweep_results.json` → `base_rate_politics_no: 0.736`. The strategy
holds NO positions.

```
Strategy (hold) HR:  82.9%
Politics NO base:    73.6%
Excess HR (hold):    +9.3pp
```

9.3pp excess is positive and passes the 5pp threshold from the methodology.
However the proposed design adds TP exits. TP exits transform the HR arithmetic:
a position is counted as a "win" when it reaches the target price, not when the
market resolves NO. This inflates the reported HR artificially.

From `exit_sweep_analysis.md`, Exit@20% reports 92.8% HR, but the footnote
states: "Exit HR counts early exits as wins (position was profitable at exit).
Positions not reaching threshold fall back to resolution HR." This HR is
*definitionally* higher than the base-rate excess because a price appreciation
to +20% of remaining value is far more common than actual resolution, and the
exit locks in what would otherwise be an open position.

> [!WARNING]
> The TP-inflated HR (92.8% for Exit@20%) cannot be compared against the 73.6%
> Politics NO base rate to claim 19.2pp excess. The two HRs measure different
> things: one measures "price moved favorably before resolution," the other
> measures "market resolved correctly." The strategy's true edge above base rate
> remains ~9.3pp (hold-to-resolution excess), unchanged by exit policy.
> Reporting TP HR as an indicator of predictive accuracy is misleading.

### 4. Sample Size: WARNING (for 0.90+ subgroup)

Overall sample: 346 positions — passes the 100-trade minimum.

The TP/SL design targets specifically the 0.90+ price bucket. Per
`analysis.md` and `exit_sweep_analysis.md` (Section 6):

```
very_expensive (0.90+):  190 positions, Hold PnL = -$1,368
expensive (0.80-0.90):    53 positions, Hold PnL = -$68
```

The 190-position 0.90+ subgroup is sufficient for analysis. However the
proposed design also involves a stop-loss trigger. Stop-loss fires only on losers:
19 losers out of 190 positions in the 0.90+ bucket (10% loss rate, 190 * 0.10
≈ 19). From `analysis.md` section 3, LOST positions at high fill prices: only
1 of 19 LOST positions ever reached the 50% exit threshold.

> [!WARNING]
> The SL analysis rests on approximately 19 loser events at 0.90+ fill. This is
> below the 50-trade minimum for statistically reliable parameter tuning of SL
> thresholds. The -Y% threshold cannot be robustly calibrated with 19 data points.
> Even a ±3-event change in the count (sampling noise over 8 months) moves the
> empirical loss rate between 8% and 13%, changing whether the subgroup is
> profitable or not. `analysis.md`, section 3, LOST Positions table.

### 5. Walk-Forward vs In-Sample: FAIL

> [!CRITICAL]
> All sweep results are in-sample on the same 346-position ledger from which the
> problem (0.90+ drag) was diagnosed. There is no walk-forward split: the exit
> thresholds (20%, 25%, 50%, 80%) were chosen after observing the full 2025-07 to
> 2026-03 period. The fact that "Exit@80% best for very_expensive bucket"
> (`exit_sweep_analysis.md` Section 6) was discovered by sweeping the same data
> means the 80% threshold for high-price entries is optimized in-sample.
>
> Walk-forward validation is required before deploying any bucket-conditional
> threshold. Split the 346 positions chronologically: train on first 6 months
> (2025-07 to 2025-12), select thresholds, validate on last 2 months
> (2026-01 to 2026-03).

Note: The sweep correctly labels all results as "UPPER BOUNDS" and flags the
vectorized nature, which is the right disposition. But the walk-forward issue
is distinct from the vectorized vs tick-by-tick gap — both must be addressed.

### 6. Degradation Band: N/A

No tick-by-tick validation has been run for the exit policy. The sweep
explicitly acknowledges this in `notes.md` ("Tick-validate Exit@20% vs
Exit@50%"). The 6-point check on degradation band applies only in Round 2.

---

## Additional Concerns

### Question 1: Is TP/SL Fundamentally Sound for Prediction Markets?

> [!WARNING]
> Prediction market prices are Bayesian probability estimates, not asset prices
> driven by supply/demand equilibrium. A NO token at 0.90 says "the market
> believes 90% probability of NO resolution." When the price drifts to 0.95,
> it means new information has shifted the consensus — NOT that the position has
> "made money" in the same sense as a stock gaining 5%. The TP/SL framing
> imports equity-market mental models that do not map cleanly onto this structure.
>
> Specifically: a take-profit at "+X% move" competes against the market's
> Bayesian update. If the NO price moves from 0.90 to 0.95 because new evidence
> confirms the NO outcome, exiting at 0.95 means selling to someone paying 0.95
> for a token that will resolve to 1.00 — a 5-cent gift to the buyer. The
> seller (TP exit) gives up the remaining 5 cents of value to free capital.
> Whether this is economically rational depends entirely on the opportunity cost
> of the capital, not on any price-level logic borrowed from equity TP/SL.
>
> The correct framing is: "Is the capital freed by early exit worth more
> deployed elsewhere than the residual value surrendered?" The sweep answers
> this question correctly (capital recycling thesis), but the TP/SL label
> obscures it. Naming it "TP/SL" risks causing implementation choices — SL
> in particular — that are economically unjustified.

### Question 2: Whipsaw Risk on Stop-Loss

> [!CRITICAL]
> The stop-loss component of Layer 1 has no empirical grounding in this sweep.
> The sweep does NOT model a stop-loss (cutting at -Y% move). It models only
> take-profit exits on favorable price movement.
>
> For high-price entries (0.90+ NO), a "stop-loss" would fire when the NO price
> drops — i.e., when the YES price rises, meaning the market is updating toward
> a YES resolution. In prediction markets, the price path of a losing position
> is typically monotonic toward zero (the market incorporates news). Whipsaw
> (temporary dip then recovery) does exist but is structurally rarer than in
> equities because there is no noise trader feedback loop forcing mean reversion
> at an equity's fundamental value.
>
> Critically: from `analysis.md` section 3, LOST Positions data shows that of
> 59 losers, only 49% ever reach even the 25% of max payout exit target. This
> means 51% of losing positions NEVER produce a favorable exit window — the SL
> would need to fire on adverse price movement (NO price falling below fill
> price). For the 0.90+ bucket: 1 of 19 losers reached the 50% target. The
> other 18 walked straight to zero with no recovery.
>
> A stop-loss cutting at "NO price falls to fill_price - Y%" would fire on
> virtually all 18 of those "straight-to-zero" losers (correct action) but
> would also fire on any temporary price dip in the 171 winning positions.
> Without tick-level price path data for winning positions (not present in
> the sweep), the false stop-out rate is completely unknown. The sweep cannot
> validate the SL component.

### Question 3: Adverse Selection on TP

> [!WARNING]
> The asymmetric payoff structure at 0.90+ entries makes TP exits
> systematically harmful in a subtle way.
>
> At a 0.90 fill price:
>   - Max gain (hold to resolution): $100 * (1/0.90 - 1) = $11.11 per $100 bet
>   - Break-even loss rate: 1 - 0.90 = 10% (i.e., must win >90% to be profitable)
>   - Actual loss rate in data: ~10.5% (190 positions, 20 losers)
>
> A TP exit at "+X% of remaining value" reduces the gain further. For Exit@50%:
>   - Exit price = 0.90 + 0.50 * (1 - 0.90) = 0.95
>   - Gain per $100 bet = $100 * (1/0.90 - 1/0.95) ≈ $5.55 (half the max gain)
>
> Every TP exit at 0.90+ gives up roughly half the already-tiny $11.11 gain.
> The 20 losers still lose the full $100 (unless a SL fires, which the sweep
> does not model). So the TP makes the payoff distribution even more asymmetric:
> wins are capped at $5.55, losses remain at $100. This is the opposite of
> what a TP is supposed to do in risk management.
>
> The only justification is capital recycling. But if the recycled capital
> goes into another 0.90+ NO entry, the same adverse payoff applies. The
> proposed design does NOT restrict what new signals fill the freed slots.
> If demand-driven eviction (Layer 2) and TP (Layer 1) together free slots
> that are then filled by more 0.90+ entries, the capital recycling benefit
> is null — you are churning through more of the same losing segment.

### Question 4: Interaction Between Layer 1 (TP/SL) and Layer 2 (Eviction)

> [!WARNING]
> The two layers interact in a way that may not be additive:
>
> Scenario: Portfolio has 20 open slots (all full). Layer 2 eviction fires,
> removes the worst-PnL position (which may be a 0.90+ entry that has moved
> against). A new signal arrives and fills the slot. Simultaneously, a 0.90+
> position that was NOT evicted triggers its SL (price moved against it
> independently). Now the portfolio has freed two slots in short succession
> from the worst segment.
>
> More concerning: SL exits create "emergency capital" that will be consumed
> by the next incoming signal regardless of quality. The demand-driven eviction
> logic (Layer 2) has a quality check ("worst PnL/day position, bleeding
> positions first") but SL exits (Layer 1) have no such selectivity — they
> simply free capital when the price moves adversely. The freed capital from
> SL exits may flow into signals of similar or worse quality.
>
> There is also a counter-directional interaction: if Layer 1 TP fires on a
> position that Layer 2 would have evicted next week anyway (because it is
> stagnating), TP accelerates the natural eviction. This is benign. But if
> TP fires on a position that Layer 2 would have KEPT (because it has the
> best PnL/day), the TP removes a good position and the eviction logic loses
> a reference point for "best in portfolio."
>
> No modeling of the two-layer interaction exists. The sweep is purely Layer 1
> TP-only (no SL, no eviction layer).

### Question 5: Is the Real Problem Entry Filtering, Not Exit Management?

> [!CRITICAL]
> The 0.90+ subgroup has negative PnL with an empirical HR of ~89.5% versus
> a break-even HR of ~90% (at 0.90 fill, you need >90% to be profitable).
> The subgroup is marginally unprofitable not because of exit management but
> because the ENTRY is structurally near-breakeven by construction.
>
> From `analysis.md`, section 2:
>   "Break-even HR at 0.93 fill price = 93%. Actual HR for this bucket = 89.5%"
>
> A consensus strategy (Politics NO v3 K=100 N=2) that enters at 0.90+ is
> betting that the market has underpriced NO by a fraction of a cent. Given
> that Politics median half-spread is 0.001 (`spread_microstructure.md`), the
> theoretical edge at 0.90+ entries is essentially within the noise of the
> bid-ask spread itself. These entries have no identifiable alpha — they are
> breakeven entries where the consensus signal is chasing markets that are
> already efficiently priced near certainty.
>
> **The correct intervention is entry filtering: do not enter NO at >0.85
> (or whatever threshold tests as breakeven), not exit management on positions
> already entered.** Exit management cannot recover edge that was never there
> at entry. The capital freed by TP exits at 0.90+ is being recycled into a
> universe that — if 0.90+ entries remain allowed — will include more 0.90+
> entries.
>
> Evidence: the sweep's own data shows that Exit@80% is "best" for the
> very_expensive bucket (`exit_sweep_analysis.md` Section 6) but its ROC/day
> (-0.00175) is still NEGATIVE. No exit threshold rescues the 0.90+ bucket
> from negative ROC/day. The TP/SL design cannot solve the entry problem.

### Question 6: Knowledge Base Entries That Contradict This Approach

> [!WARNING]
> `pitfalls/vectorized_vs_tick.md` (CRITICAL entries): "Never trust vectorized
> PnL as a deployment estimate. Multiply by 0.3-0.5 for realistic expectation."
> The exit sweep is vectorized (uses trade tape as oracle). The 3-5x capital
> efficiency improvement claimed for Exit@25% must be discounted. A 0.3x factor
> applied to the $22,766 P=20 improvement gives ~$6,800 realistic improvement.
> This is still positive, but the signal-to-noise ratio narrows considerably.
>
> `pitfalls/vectorized_vs_tick.md` (HIGH): "Capital constraint — Vectorized
> assumes unlimited positions. Reality: N max concurrent." The P=20 constrained
> simulation partially addresses this but uses a heapq simulation, not
> tick-by-tick replay with actual arrival times. The notes acknowledge
> "simultaneous signals at the same timestamp may be ordered arbitrarily."
>
> `execution/spread_microstructure.md`: "Last 10% of market life has 53% wider
> average spread." 0.90+ entries are near the end of market life by definition
> (the market has already priced NO near certainty). The exit trade (SELL NO
> at 0.95-0.98) occurs in the last 5-10% of market life where spreads are
> widest. The $0.10 slippage estimate ($0.001 half-spread * $100) may be
> significantly underestimated for this segment. The sweep's slippage estimate
> uses the global Politics MAC of 0.001, not the end-of-life spread.

> [!WARNING]
> `pitfalls/excess_hr_vs_absolute_hr.md`: "Positive PnL despite negative excess
> HR can come from entry at very low prices (mean 0.20-0.35), creating asymmetric
> payoffs." The Politics NO v3 strategy derives 106% of its PnL from <0.50
> entries. This is the asymmetric payoff effect. The 0.90+ entries have the
> OPPOSITE asymmetry: large downside, tiny upside. The overall strategy is
> profitable because of longshot entries masking the 0.90+ drag. TP/SL on
> 0.90+ positions is a patch on a structurally broken subgroup; the real
> fix is to not enter that subgroup.

---

## Summary

The active exit hypothesis has a sound core thesis (capital recycling via TP
frees slots for better longshot entries) and is correctly framed as vectorized
upper bounds. The critical flaws are:

1. **The stop-loss has no data support.** The sweep models only TP (favorable
   price exit). The SL design is an undocumented addition with zero empirical
   grounding — the false stop-out rate on the ~171 winning 0.90+ positions is
   unknown, and the LOST position data shows most losses go straight to zero
   with no recovery dip that would allow a "catch it early" SL to help.

2. **Entry filtering dominates exit management.** The 0.90+ bucket has
   negative ROC/day under ALL exit thresholds tested. No exit design can
   rescue a near-breakeven entry. The correct action is to add an entry gate:
   skip signals where entry price > 0.85 (or the empirically determined
   breakeven threshold). This is simpler, has no execution complexity, and
   removes the problem at the source.

3. **Walk-forward required before deploying bucket-conditional thresholds.**
   The Exit@80% recommendation for very_expensive bucket is in-sample on 190
   positions. Chronological split validation is required.

4. **The two-layer interaction is unmodeled.** Layer 1 (TP/SL) and Layer 2
   (demand-driven eviction) have not been co-simulated. Their interaction may
   be additive, neutral, or adversarial depending on which positions each layer
   targets.

5. **Slippage for end-of-life exits is underestimated.** 0.90+ entries exit
   near market resolution when spreads are 53% wider (microstructure data).

Recommended path: (a) Add entry filter for >0.85 entries as the primary fix.
(b) Validate active TP (no SL) via tick-by-tick SyncReplayRunner. (c) If
tick-by-tick confirms the capital recycling benefit, then design Layer 2 eviction
independently. (d) Only revisit SL if tick-level price path data shows meaningful
false-stop-out rates are manageable.
