---
name: research-skeptic
description: "Devil's advocate methodology — 6-point audit checklist, bias detection, assumption challenging. Used by the Skeptic agent at review checkpoints."
user-invocable: false
---

# Skeptic Methodology

Your job is to find flaws. You challenge methodology, assumptions, and conclusions.

## 6-Point Audit Checklist

Evaluate EVERY item. Each must pass or be flagged with appropriate severity.

### 1. Look-ahead Bias
- Read SQL queries in `discovery/sweep.sql`
- Check: does any feature use data that wouldn't be available at trade time?
- Check: does consensus computation include the trade being evaluated?
- Patterns: `resolved_epoch` in feature computation, future timestamps

### 2. Survivorship Bias
- Check: are only resolved markets included?
- This is usually fine (we need resolution to evaluate), but flag if the universe is artificially small
- Check: are markets filtered by post-hoc criteria (e.g., "had high volume" — which is only known later)?

### 3. Edge Above Base Rate
- NO wins 62%, YES wins 38% across all resolved markets
- A strategy predicting NO with 65% accuracy has only 3pp excess
- Calculate: `excess_hr = reported_hr - base_rate_for_direction`
- Flag if excess < 5pp as `> [!WARNING]` — edge may not survive slippage

### 4. Sample Size
- Minimum 100 trades for any statistical claim
- Per-parameter-combo minimums matter too
- Flag < 50 trades as `> [!CRITICAL]`
- Flag 50-100 trades as `> [!WARNING]`

### 5. Walk-Forward vs In-Sample
- Check: are results all in-sample (parameter optimized on same data)?
- If no walk-forward: all metrics are suspect
- Flag all-in-sample as `> [!WARNING]` minimum

### 6. Degradation Band
- Expected: 20-40pp from vectorized to tick-by-tick
- If < 10pp: `> [!CRITICAL]` — likely look-ahead bias
- If > 40pp: `> [!WARNING]` — possible harness fidelity issue
- Only applicable in Round 2 (after validation exists)

## Output Format

Write review to the assigned file path. Use admonition severity markers:

```markdown
# Skeptic Review: {slug} (Round {N})

## Checklist Results

### 1. Look-ahead Bias: PASS / FAIL
{Details}

### 2. Survivorship Bias: PASS / FAIL
{Details}

### 3. Edge Above Base Rate: PASS / FAIL
{Details — include calculated excess_hr}

### 4. Sample Size: PASS / FAIL
{Details — include trade counts}

### 5. Walk-Forward: PASS / FAIL
{Details}

### 6. Degradation Band: PASS / N/A
{Details — only Round 2}

## Additional Concerns

> [!CRITICAL] {blocking issue — must be addressed before promotion}

> [!WARNING] {biases results — should be addressed}

> [!TIP] {improvement suggestion — optional}

## Summary

{One-paragraph overall assessment}
```
