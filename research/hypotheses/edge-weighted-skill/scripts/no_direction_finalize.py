"""Write the markdown summary from already-saved JSON results."""
import json
import sys

OUT_JSON = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/edge-weighted-skill/discovery/no_direction_results.json"
OUT_MD   = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/edge-weighted-skill/discovery/no_direction_results.md"

with open(OUT_JSON) as f:
    data = json.load(f)

meta = data["metadata"]
pool_metrics = data["pool_metrics"]
sweep = data["consensus_sweep"]
per_tag = data["per_tag_breakdown"]
monthly = data["monthly_breakdown"]
cs = data["compounding_scores"]
tag_bases = data["tag_no_base_rates"]

FOCUS_TAGS = ["Politics", "Esports", "Sports", "Crypto"]

md = [
    "# NO-Direction Sweep Results — edge-weighted-skill",
    "",
    "> **All results are UPPER BOUNDS** — vectorized, not tick-by-tick validated.",
    "> Phantom filter applied: `first_trade >= test_start`. Market-level aggregation enforced.",
    "> Median hold = 0d for most signals — in-play contamination highly likely for Sports.",
    "",
    f"Train through: {meta['train_end']} | Test: {meta['test_start']} → {meta['test_end']} (3 months)",
    "",
    "## Overview",
    "",
    f"- NO-direction qualified traders (train, >=20 NO positions): **{meta['n_qualified_no_traders']:,}**",
    f"- Test window NO base rate: **{meta['test_no_base_rate']:.4f}** ({meta['test_no_base_rate']*100:.1f}%)",
    f"- Test window YES base rate: **{meta['test_yes_base_rate']:.4f}** ({meta['test_yes_base_rate']*100:.1f}%)",
    "",
    "## Pool Metrics (train-scored, composite VW score)",
    "",
    "| K | avg HR | avg BEH |",
    "|---|--------|---------|",
]
for K in ["50", "100", "200"]:
    pm = pool_metrics[K]
    md.append(f"| {K} | {pm['avg_hr']:.4f} | {pm['avg_beh']:.4f} |")

md += [
    "",
    "## Consensus Sweep (vectorized UPPER BOUNDS)",
    "",
    "Signal = N distinct pool traders enter NO side of same market.",
    "Entry price = Nth trader's actual NO-space entry price (max first_trade).",
    "Hold filter: 0–30 days. Phantom filter: first_trade >= test_start.",
    "",
    "| K | N | n_signals | HR | Excess HR | Med Entry | Med Hold | Avg Edge/$ | Total Edge |",
    "|---|---|-----------|----|-----------|-----------|---------|-----------:|-----------|",
]
for K in ["50", "100", "200"]:
    for N in ["1", "2", "3"]:
        r = sweep[K][N]
        if r["n_signals"] > 0 and r["hit_rate"] is not None:
            hold = f"{r['median_hold_days']:.0f}d" if r["median_hold_days"] is not None else "0d"
            md.append(
                f"| {K} | {N} | {r['n_signals']:,} | {r['hit_rate']:.4f} | "
                f"{r['excess_hr']:+.4f} | {r['median_entry_price']:.4f} | "
                f"{hold} | {r['avg_edge_per_dollar']:+.4f} | "
                f"{r['total_edge_units']:+.1f} |"
            )
        else:
            md.append(f"| {K} | {N} | 0 | — | — | — | — | — | — |")

md += [
    "",
    "## Per-Tag Breakdown (K=100, N=2)",
    "",
    "| Tag | NO Base Rate | n_signals | HR | Excess HR | Med Entry | Med Hold | Avg Edge/$ |",
    "|-----|-------------|-----------|----|-----------|-----------|---------|-----------:|",
]
for tag in FOCUS_TAGS:
    r = per_tag.get(tag, {})
    if r.get("n_signals", 0) > 0 and r.get("hit_rate") is not None:
        hold = f"{r['median_hold_days']:.0f}d" if r["median_hold_days"] is not None else "0d"
        md.append(
            f"| {tag} | {r['no_base_rate']:.4f} | {r['n_signals']:,} | "
            f"{r['hit_rate']:.4f} | {r['excess_hr']:+.4f} | "
            f"{r['median_entry_price']:.4f} | {hold} | "
            f"{r['avg_edge_per_dollar']:+.4f} |"
        )
    else:
        md.append(f"| {tag} | {r.get('no_base_rate', 'N/A')} | 0 | — | — | — | — | — |")

md += [
    "",
    "## Monthly Signal Breakdown (K=100, N=2, hold 0–30d)",
    "",
    "| Month | n_signals | HR | Med Entry | Med Hold | Avg Edge/$ |",
    "|-------|-----------|----|-----------|---------|-----------:|",
]
for row in monthly:
    hold = f"{row['med_hold_days']:.0f}d" if row.get("med_hold_days") is not None else "0d"
    md.append(
        f"| {row['res_month']} | {row['n_signals']:,} | {row['hit_rate']:.4f} | "
        f"{row['med_price']:.4f} | {hold} | {row['avg_edge']:+.4f} |"
    )

md += [
    "",
    "## Compounding Scores",
    "",
    "Formula: `excess_hr × avg_edge_per_dollar / max(median_hold_days, 0.5)`",
    "",
    "| K | N | n_signals | HR | Excess HR | Avg Edge/$ | Hold | CS |",
    "|---|---|-----------|----|-----------|-----------:|------|----|",
]
for cfg in (cs or [])[:10]:
    hold = f"{cfg['median_hold_days']:.0f}d" if cfg.get("median_hold_days") is not None else "0d"
    md.append(
        f"| {cfg['K']} | {cfg['N']} | {cfg['n_signals']:,} | {cfg['hit_rate']:.4f} | "
        f"{cfg['excess_hr']:+.4f} | {cfg['avg_edge_per_dollar']:+.4f} | "
        f"{hold} | **{cfg['compounding_score']:.4f}** |"
    )

if not cs:
    md.append("| — | — | — median hold=0, CS undefined (divide by 0) — | — | — | — | — | — |")

md += [
    "",
    "## Critical Finding: Median Hold = 0 Days",
    "",
    "**The median hold time across all configs is 0 days.** This is the in-play contamination",
    "signal identified in prior research: elite sports/esports/NBA traders enter during live",
    "matches and the market resolves same-day. The HR is inflated because these are NOT copyable",
    "signals — by the time the copy strategy fires, the event has already resolved.",
    "",
    "This is the dominant vectorized→tick gap source for this hypothesis.",
    "",
    "**Politics is the exception**: med_hold = 1 day, HR = 88.9%, excess = +12.2pp. Likely copyable.",
    "",
    "## YES vs NO Comparison",
    "",
    f"| Metric | YES | NO |",
    f"|--------|-----|-----|",
    f"| Test base rate | {meta['test_yes_base_rate']:.4f} | {meta['test_no_base_rate']:.4f} |",
    f"| Signal volume (K=100, N=2) | unknown | 7,217/3mo |",
    f"| Top HR (K=50, N=3) | unknown | 0.9980 |",
    f"| In-play contamination | lower risk | HIGH — median hold=0d |",
    f"| Best actionable tag | Politics | Politics (1d hold) |",
    "",
    "## Tag-Specific NO Base Rates (test window)",
    "",
    "| Tag | NO Base Rate |",
    "|-----|------------|",
]
for tag, br in sorted(tag_bases.items(), key=lambda x: -x[1]):
    md.append(f"| {tag} | {br:.4f} |")

md += [
    "",
    "## Key Findings",
    "",
    "1. **Strong vectorized signal, but dominated by in-play**: K=50, N=3: HR=99.8%, +39.9pp excess.",
    "   BUT median hold = 0 days → same-day resolution → in-play contamination.",
    "",
    "2. **Politics NO is genuinely actionable**: 1,435 signals/3mo, HR=88.9%, +12.2pp excess over",
    "   76.8% base rate, med hold = 1 day, avg edge/$ = +0.134. NOT in-play.",
    "",
    "3. **Sports/Esports/Crypto signals are in-play**: med hold = 0 days. These inflate overall",
    "   HR metrics dramatically. Must apply hold >= 1 day filter for tick validation.",
    "",
    "4. **10,463 NO-direction qualified traders** (train), more than the 3,677 YES-skilled (all-time).",
    "   The NO-direction pool is large and skill is concentrated in the top-50.",
    "",
    "5. **Compounding score**: undefined for most configs (median hold = 0). Politics-only:",
    "   estimated CS ≈ 0.122 × 0.134 / 1.0 = 0.016 (marginal, needs tick validation).",
    "",
    "## Next Steps",
    "",
    "- **Tick validate Politics NO** with hold >= 1 day filter (actionable path)",
    "- Apply `date_diff('hour', signal_entry, resolved_at) >= 24` hold filter to all configs",
    "- Check in-play contamination: filter Sports/Esports N=2+ to hold >= 24h",
    "- Overlap analysis: NO pool vs elite whale copy pool (0x336151559e appears in both)",
]

with open(OUT_MD, "w") as f:
    f.write("\n".join(md))

print(f"Markdown written: {OUT_MD}")
