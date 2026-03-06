# /// script
# requires-python = ">=3.11"
# dependencies = ["marimo", "clickhouse-connect", "pandas", "plotly"]
# ///
"""
Tag-HR-Copy Discovery Notebook — Round 2
Vectorized sweep results — all figures are UPPER BOUNDS
New in Round 2: max_avg_entry_price filter, corrected CS formula (median_pnl, days denominator)
"""
import marimo as mo

# ─── Cell 0: Imports ──────────────────────────────────────────────────────────
import clickhouse_connect
import json
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

ch = clickhouse_connect.get_client(host="192.168.0.148", port=18123, database="polymarket")

with open("/Users/kiefferjulien/git/polymarket/research/hypotheses/tag-hr-copy/discovery/results.json") as f:
    results = json.load(f)

mo.md("## Tag-HR-Copy Discovery (Round 2) — UPPER BOUNDS ONLY")

# ─── Cell 1: Summary Table ────────────────────────────────────────────────────
summary_rows = []
for tag, s in results["summary"].items():
    summary_rows.append({
        "Tag": tag,
        "HR": f"{s['avg_hr']*100:.1f}%",
        "Base": f"{s['base_rate']*100:.1f}%",
        "Excess": f"+{s['excess_hr_pp']:.1f}pp",
        "Med PnL": f"${s['median_pnl_usd']:.2f}",
        "Hold (h)": f"{s['avg_hold_hours']:.1f}",
        "Sigs/fold": f"{s['signals_per_fold']:.0f}",
        "CS": f"{s['compounding_score']:.2f}",
        "Verdict": "VALIDATE" if tag in results["promoted_tags"] else ("PARKED" if tag in results["parked_tags"] else "DROPPED"),
    })
df_summary = pd.DataFrame(summary_rows)
mo.ui.table(df_summary)

# ─── Cell 2: Price Ceiling Impact Chart (Esports) ─────────────────────────────
pc_data = results["price_ceiling_impact"]["pc_sweep"]
df_pc = pd.DataFrame(pc_data)

fig_pc = go.Figure()
fig_pc.add_trace(go.Scatter(
    x=df_pc["pc"], y=df_pc["avg_hr"] * 100,
    mode="lines+markers", name="HR (%)", line=dict(color="royalblue", width=2)
))
fig_pc.add_trace(go.Bar(
    x=df_pc["pc"], y=df_pc["cs"],
    name="Compounding Score", yaxis="y2", opacity=0.4,
    marker_color="orange"
))
fig_pc.update_layout(
    title="Esports BUY (mt=50, ep=15pp): Price Ceiling Impact",
    xaxis_title="max_avg_entry_price",
    yaxis=dict(title="Hit Rate (%)", range=[70, 85]),
    yaxis2=dict(title="Compounding Score", overlaying="y", side="right", range=[60, 80]),
    legend=dict(x=0.7, y=0.9)
)
fig_pc

# ─── Cell 3: Per-Fold HR Breakdown (Esports) ─────────────────────────────────
folds = results["esports_perfold_breakdown"]["folds"]
df_folds = pd.DataFrame(folds)

fig_folds = go.Figure()
for pc_col, label, color in [
    ("hr_pc075", "pc=0.75", "royalblue"),
    ("hr_pc085", "pc=0.85", "green"),
    ("hr_pc100", "pc=1.00", "red"),
]:
    fig_folds.add_trace(go.Scatter(
        x=df_folds["test_start"],
        y=df_folds[pc_col] * 100,
        mode="lines+markers",
        name=label,
        line=dict(color=color)
    ))
fig_folds.add_trace(go.Scatter(
    x=df_folds["test_start"],
    y=df_folds["base_rate"] * 100,
    mode="lines", name="Base Rate",
    line=dict(color="gray", dash="dash")
))
fig_folds.update_layout(
    title="Esports BUY — Per-Fold Hit Rate by Price Ceiling (mt=50, ep=15pp)",
    xaxis_title="Test Fold Start",
    yaxis_title="Hit Rate (%)",
    yaxis=dict(range=[0, 100])
)
fig_folds

# ─── Cell 4: Signal Count per Price Ceiling ───────────────────────────────────
fig_sigs = go.Figure()
fig_sigs.add_trace(go.Bar(
    x=[str(f["test_start"]) for f in folds if f["n_sigs_pc075"] > 2],
    y=[f["n_sigs_pc075"] for f in folds if f["n_sigs_pc075"] > 2],
    name="pc=0.75", marker_color="royalblue"
))
fig_sigs.add_trace(go.Bar(
    x=[str(f["test_start"]) for f in folds if f["n_sigs_pc100"] > 2],
    y=[f["n_sigs_pc100"] for f in folds if f["n_sigs_pc100"] > 2],
    name="pc=1.00", marker_color="lightcoral"
))
fig_sigs.update_layout(
    title="Esports: Signal Count per Fold (pc=0.75 vs pc=1.0)",
    xaxis_title="Test Fold",
    yaxis_title="Signal Count",
    barmode="group"
)
fig_sigs

# ─── Cell 5: Top 5 by CS per Tag ──────────────────────────────────────────────
for tag in ["Esports", "1H", "Tennis"]:
    rows = results["top5_per_tag"][tag]["buy_only"]
    df_t = pd.DataFrame(rows)
    df_t["label"] = df_t.apply(
        lambda r: f"mt={int(r['mt'])} ep={int(r['ep'])}pp pc={r['pc']}", axis=1
    )
    fig_tag = px.bar(
        df_t, x="label", y="cs",
        color="hr", color_continuous_scale="RdYlGn",
        title=f"{tag} BUY-only: Top 5 Combos by Compounding Score (UB)",
        labels={"cs": "Compounding Score", "label": "Params", "hr": "Hit Rate"},
    )
    fig_tag.update_layout(xaxis_tickangle=30)
    fig_tag

# ─── Cell 6: BUY vs Directional Comparison ────────────────────────────────────
comparison_data = []
for tag in ["Esports", "1H", "Tennis"]:
    buy_best = results["top5_per_tag"][tag]["buy_only"][0]
    dir_best = results["top5_per_tag"][tag]["directional"][0]
    comparison_data.append({
        "Tag": tag, "Variant": "BUY-only",
        "CS": buy_best["cs"], "HR": buy_best["hr"] * 100,
        "Med PnL": buy_best["med_pnl"]
    })
    comparison_data.append({
        "Tag": tag, "Variant": "Directional",
        "CS": dir_best["cs"], "HR": dir_best["hr"] * 100,
        "Med PnL": dir_best["med_pnl"]
    })

df_cmp = pd.DataFrame(comparison_data)
fig_cmp = px.bar(
    df_cmp, x="Tag", y="CS", color="Variant", barmode="group",
    title="BUY-only vs Directional: Best CS per Tag",
    color_discrete_map={"BUY-only": "royalblue", "Directional": "salmon"},
    log_y=True
)
fig_cmp

# ─── Cell 7: Sensitivity Heatmap ──────────────────────────────────────────────
with open("/Users/kiefferjulien/git/polymarket/research/hypotheses/tag-hr-copy/discovery/sensitivity.json") as f:
    sens = json.load(f)

sens_rows = []
for key, s in sens.items():
    if "note" in key: continue
    if isinstance(s, dict) and "perturbations" in s:
        for pert, pr in s["perturbations"].items():
            if pr.get("avg_hr") is not None:
                sens_rows.append({
                    "combo": key,
                    "perturbation": pert,
                    "avg_hr": pr["avg_hr"] * 100,
                    "baseline_hr": s.get("baseline_hr", 0) * 100,
                    "hr_delta": (pr["avg_hr"] - s.get("baseline_hr", pr["avg_hr"])) * 100,
                })

df_sens = pd.DataFrame(sens_rows)
if not df_sens.empty:
    fig_sens = px.density_heatmap(
        df_sens, x="perturbation", y="combo", z="hr_delta",
        color_continuous_scale="RdYlGn", color_continuous_midpoint=0,
        title="Sensitivity: HR Delta from Baseline (pp)",
        labels={"hr_delta": "HR Delta (pp)", "perturbation": "Perturbation", "combo": "Combo"}
    )
    fig_sens

# ─── Cell 8: Spawned Ideas ────────────────────────────────────────────────────
ideas = results["spawned_ideas"]
for idea in ideas:
    mo.md(f"**{idea['slug']}** [{idea['priority']}]: {idea['description']}")
