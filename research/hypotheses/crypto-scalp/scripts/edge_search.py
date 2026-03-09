"""Comprehensive edge search: Binance 180d + Polymarket trade data.

Combines:
  1. Binance 1m candles (180 days) -> construct 5m windows, compute features
  2. Polymarket resolved Up/Down crypto markets -> actual market outcomes + pricing

Usage:
  PYTHONPATH=. uv run python research/hypotheses/crypto-scalp/scripts/edge_search.py
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl
from scipy.stats import chi2_contingency

BASE = Path("/mnt/nvme/git/polymarket/polymarket")
WORKTREE = BASE / ".claude/worktrees/crypto-signal"
KLINES_DIR = WORKTREE / "research/hypotheses/crypto-scalp/data/klines"
RESEARCH_DIR = BASE / "data/research"
MARKETS_FILE = RESEARCH_DIR / "metadata/markets.parquet"
TRADES_DIR = RESEARCH_DIR / "trades"

TAKER_FEE = 0.03
BREAK_EVEN = 0.50 + TAKER_FEE / 2


def ph(title: str) -> None:
    print(f"\n{'=' * 76}\n  {title}\n{'=' * 76}\n")


def ps(title: str) -> None:
    print(f"\n{'─' * 76}\n  {title}\n{'─' * 76}\n")


# ═══════════════════════════════════════════════════════════════════════════
# PART 1: BINANCE 180-DAY EDGE SEARCH
# ═══════════════════════════════════════════════════════════════════════════


def load_klines(symbol: str) -> pl.DataFrame:
    for days in [180, 30]:
        path = KLINES_DIR / f"{symbol}_1m_{days}d.parquet"
        if path.exists():
            df = pl.read_parquet(path)
            return df.with_columns(
                (pl.col("open_time") / 1000).cast(pl.Int64).alias("ts"),
                pl.lit(symbol.replace("USDT", "")).alias("asset"),
            )
    raise FileNotFoundError(f"No klines for {symbol}")


def build_5m_windows(klines: pl.DataFrame) -> pl.DataFrame:
    n = len(klines)
    closes = klines["close"].to_numpy()
    highs = klines["high"].to_numpy()
    lows = klines["low"].to_numpy()
    volumes = klines["volume"].to_numpy()
    taker_buys = klines["taker_buy_vol"].to_numpy()
    num_trades_arr = klines["num_trades"].to_numpy()
    timestamps = klines["ts"].to_numpy()
    asset = klines["asset"][0]
    log_rets = np.diff(np.log(closes))
    log_rets = np.insert(log_rets, 0, 0.0)

    rows = []
    for i in range(n // 5):
        s, e = i * 5, i * 5 + 5
        if e > n:
            break
        w_open, w_close = closes[s], closes[e - 1]
        ret = (w_close - w_open) / w_open

        # Prior 5m momentum
        mom5 = (closes[s - 1] - closes[s - 5]) / closes[s - 5] if s >= 5 else 0.0
        # Prior 15m
        mom15 = (closes[s - 1] - closes[s - 15]) / closes[s - 15] if s >= 15 else 0.0
        # Prior 1h
        mom1h = (closes[s - 1] - closes[s - 60]) / closes[s - 60] if s >= 60 else 0.0
        # Realized vol 30min
        rvol = np.std(log_rets[s - 30:s]) * np.sqrt(525600) if s >= 30 else 0.0
        # Volume ratio
        if s >= 120:
            avg_v = volumes[s - 120:s].reshape(-1, 5).sum(axis=1).mean()
            vr = volumes[s:e].sum() / max(avg_v, 1e-10)
        else:
            vr = 1.0
        # Prev taker ratio
        if s >= 5:
            pv = volumes[s - 5:s].sum()
            pt = taker_buys[s - 5:s].sum()
            ptr = pt / max(pv, 1e-10)
        else:
            ptr = 0.5
        # Acceleration
        if s >= 10:
            mom5_prev = (closes[s - 6] - closes[s - 10]) / closes[s - 10]
            accel = mom5 - mom5_prev
        else:
            accel = 0.0
        # Range ratio
        if s >= 20:
            avg_rng = np.mean(highs[s - 20:s] - lows[s - 20:s])
            crng = highs[s:e].max() - lows[s:e].min()
            rr = crng / max(avg_rng, 1e-10)
        else:
            rr = 1.0

        hour = int((timestamps[s] % 86400) / 3600)
        dt = datetime.fromtimestamp(int(timestamps[s]), tz=timezone.utc)

        rows.append({
            "asset": asset, "ts": int(timestamps[s]), "hour_utc": hour, "dow": dt.weekday(),
            "open": w_open, "close": w_close,
            "ret_5m": ret, "is_up": int(w_close >= w_open),
            "mom_5m": mom5, "mom_15m": mom15, "mom_1h": mom1h,
            "rvol_30m": rvol, "vol_ratio": vr, "prev_taker_ratio": ptr,
            "acceleration": accel, "range_ratio": rr,
            "volume": float(volumes[s:e].sum()), "trades": int(num_trades_arr[s:e].sum()),
        })
    return pl.DataFrame(rows)


def test_feature(df: pl.DataFrame, feat: str, nq: int = 4) -> dict:
    valid = df.filter(pl.col(feat).is_not_null() & pl.col(feat).is_finite())
    if len(valid) < nq * 100:
        return {"feature": feat, "significant": False, "max_acc": 0.5}
    base = valid["is_up"].mean()
    try:
        q = valid.with_columns(pl.col(feat).qcut(nq, labels=[f"Q{i+1}" for i in range(nq)]).alias("q"))
    except Exception:
        return {"feature": feat, "significant": False, "max_acc": 0.5}
    res = []
    for ql in [f"Q{i+1}" for i in range(nq)]:
        sub = q.filter(pl.col("q") == ql)
        if len(sub) < 50:
            continue
        ur = sub["is_up"].mean()
        res.append({"quantile": ql, "n": len(sub), "up_rate": ur, "vs_base": ur - base})
    if not res:
        return {"feature": feat, "significant": False, "max_acc": 0.5}
    ups = [int(r["up_rate"] * r["n"]) for r in res]
    dns = [r["n"] - u for u, r in zip(ups, res)]
    try:
        chi2, pval, _, _ = chi2_contingency([ups, dns])
    except Exception:
        chi2, pval = 0.0, 1.0
    ma = max(max(r["up_rate"] for r in res), max(1 - r["up_rate"] for r in res))
    return {"feature": feat, "significant": pval < 0.01, "pval": pval, "chi2": chi2,
            "max_acc": ma, "base_rate": base, "quantile_results": res}


def walk_forward(df: pl.DataFrame, feat: str, train_d: int = 30, test_d: int = 7) -> list[dict]:
    df = df.sort("ts")
    ts0, ts1 = df["ts"].min(), df["ts"].max()
    tr_s, te_s = train_d * 86400, test_d * 86400
    results, cur = [], ts0 + tr_s
    while cur + te_s <= ts1:
        train = df.filter((pl.col("ts") >= cur - tr_s) & (pl.col("ts") < cur))
        test = df.filter((pl.col("ts") >= cur) & (pl.col("ts") < cur + te_s))
        if len(train) < 200 or len(test) < 50:
            cur += te_s
            continue
        th_hi, th_lo = train[feat].quantile(0.75), train[feat].quantile(0.25)
        t_hi = test.filter(pl.col(feat) > th_hi)
        t_lo = test.filter(pl.col(feat) < th_lo)
        a_up = t_hi["is_up"].mean() if len(t_hi) > 20 else 0.5
        a_dn = (1 - t_lo["is_up"].mean()) if len(t_lo) > 20 else 0.5
        results.append({
            "period": datetime.fromtimestamp(cur, tz=timezone.utc).strftime("%Y-%m-%d"),
            "n_test": len(test), "n_up": len(t_hi), "n_dn": len(t_lo),
            "acc_up": a_up, "acc_dn": a_dn, "best": max(a_up, a_dn),
        })
        cur += te_s
    return results


def run_binance(combined: list[pl.DataFrame]) -> pl.DataFrame:
    ph("PART 1: BINANCE 180-DAY EDGE SEARCH")
    all_w = []
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        k = load_klines(sym)
        w = build_5m_windows(k)
        all_w.append(w)
        print(f"  {sym}: {len(k):,} candles -> {len(w):,} windows, Up: {w['is_up'].mean():.3f}")
    comb = pl.concat(all_w)
    print(f"  Combined: {len(comb):,} windows\n")

    # Base rates
    ps("1. BASE RATES (180 days)")
    for a in ["BTC", "ETH", "SOL"]:
        s = comb.filter(pl.col("asset") == a)
        print(f"  {a}: {len(s):>7,} windows | Up: {s['is_up'].mean():.3f} | "
              f"ret: {s['ret_5m'].mean() * 100:+.4f}% +/- {s['ret_5m'].std() * 100:.4f}%")

    # Feature sweep
    ps("2. FEATURE SWEEP")
    feats = ["mom_5m", "mom_15m", "mom_1h", "rvol_30m", "vol_ratio",
             "prev_taker_ratio", "acceleration", "range_ratio"]
    feat_res = []
    for f in feats:
        r = test_feature(comb, f)
        feat_res.append(r)
        sig = "***" if r.get("pval", 1) < 0.001 else "ns"
        flag = " <-- EDGE" if r["max_acc"] > 0.53 else ""
        print(f"  {f:>22s} | max_acc={r['max_acc']:.3f} | chi2={r.get('chi2', 0):>8.1f} | "
              f"p={r.get('pval', 1):.6f} [{sig}]{flag}")

    # Top feature detail
    ps("3. TOP FEATURES DETAIL")
    for r in sorted(feat_res, key=lambda x: -x["max_acc"])[:4]:
        print(f"\n  {r['feature']}:")
        for q in r.get("quantile_results", []):
            flag = " <-- profit" if abs(q["up_rate"] - 0.5) > 0.03 else ""
            print(f"    {q['quantile']} | n={q['n']:>6,} | Up={q['up_rate']:.3f} | "
                  f"edge={q['vs_base']:+.3f}{flag}")

    # Per-asset stability
    ps("4. PER-ASSET STABILITY (mom_5m)")
    for a in ["BTC", "ETH", "SOL"]:
        s = comb.filter(pl.col("asset") == a)
        r = test_feature(s, "mom_5m")
        q4 = [q for q in r.get("quantile_results", []) if q["quantile"] == "Q4"]
        q1 = [q for q in r.get("quantile_results", []) if q["quantile"] == "Q1"]
        q4u = q4[0]["up_rate"] if q4 else 0
        q1u = q1[0]["up_rate"] if q1 else 0
        print(f"  {a}: Q4 Up={q4u:.3f}, Q1 Up={q1u:.3f} (Q1 Dn={1 - q1u:.3f}), "
              f"max_acc={r['max_acc']:.3f}, p={r.get('pval', 1):.6f}")

    # Walk-forward
    ps("5. WALK-FORWARD (30d train / 7d test, BTC)")
    btc = comb.filter(pl.col("asset") == "BTC")
    wf = walk_forward(btc, "mom_5m")
    if wf:
        print(f"  {'Period':>12} | {'n':>5} | {'SigUp':>5} | {'AccUp':>6} | "
              f"{'SigDn':>5} | {'AccDn':>6} | {'Best':>6} | Status")
        print(f"  {'-' * 72}")
        wins = 0
        for r in wf:
            ok = r["best"] > 0.53
            if ok:
                wins += 1
            print(f"  {r['period']:>12} | {r['n_test']:>5} | {r['n_up']:>5} | {r['acc_up']:>6.3f} | "
                  f"{r['n_dn']:>5} | {r['acc_dn']:>6.3f} | {r['best']:>6.3f} | "
                  f"{'PROFIT' if ok else 'flat'}")
        print(f"\n  Win rate: {wins}/{len(wf)} ({wins / len(wf) * 100:.0f}%)")
        print(f"  Avg best: {np.mean([r['best'] for r in wf]):.3f}")
        print(f"  Avg acc_up: {np.mean([r['acc_up'] for r in wf]):.3f}")
        print(f"  Avg acc_dn: {np.mean([r['acc_dn'] for r in wf]):.3f}")

    # Combined signals
    ps("6. COMBINED SIGNALS")
    v = comb.filter((pl.col("rvol_30m") > 0) & (pl.col("mom_5m") != 0))
    base = v["is_up"].mean()

    conds = [
        ("Strong UP mom5 (Q4)", v.filter(pl.col("mom_5m") > v["mom_5m"].quantile(0.75)), "UP"),
        ("Strong DN mom5 (Q1)", v.filter(pl.col("mom_5m") < v["mom_5m"].quantile(0.25)), "DN"),
        ("UP mom + hi taker", v.filter(
            (pl.col("mom_5m") > v["mom_5m"].quantile(0.75)) &
            (pl.col("prev_taker_ratio") > v["prev_taker_ratio"].quantile(0.75))), "UP"),
        ("DN mom + lo taker", v.filter(
            (pl.col("mom_5m") < v["mom_5m"].quantile(0.25)) &
            (pl.col("prev_taker_ratio") < v["prev_taker_ratio"].quantile(0.25))), "DN"),
        ("UP mom + hi vol", v.filter(
            (pl.col("mom_5m") > v["mom_5m"].quantile(0.75)) &
            (pl.col("vol_ratio") > v["vol_ratio"].quantile(0.75))), "UP"),
        ("UP mom + accel", v.filter(
            (pl.col("mom_5m") > v["mom_5m"].quantile(0.75)) &
            (pl.col("acceleration") > v["acceleration"].quantile(0.75))), "UP"),
        ("DN mom + decel", v.filter(
            (pl.col("mom_5m") < v["mom_5m"].quantile(0.25)) &
            (pl.col("acceleration") < v["acceleration"].quantile(0.25))), "DN"),
        ("Extreme UP (top 10%)", v.filter(pl.col("mom_5m") > v["mom_5m"].quantile(0.90)), "UP"),
        ("Extreme DN (bot 10%)", v.filter(pl.col("mom_5m") < v["mom_5m"].quantile(0.10)), "DN"),
        ("UP 1h + UP 5m", v.filter(
            (pl.col("mom_1h") > v["mom_1h"].quantile(0.75)) &
            (pl.col("mom_5m") > v["mom_5m"].quantile(0.75))), "UP"),
        ("Weekend UP mom", v.filter(
            (pl.col("mom_5m") > v["mom_5m"].quantile(0.75)) & pl.col("dow").is_in([5, 6])), "UP"),
        ("Night UP mom (0-8 UTC)", v.filter(
            (pl.col("mom_5m") > v["mom_5m"].quantile(0.75)) & (pl.col("hour_utc") < 8)), "UP"),
    ]

    print(f"  Base: {base:.3f} | Break-even: >0.530 | n={len(v):,}\n")
    print(f"  {'Condition':>35} | {'Dir':>3} | {'n':>7} | {'Hit%':>6} | {'Edge':>7} | {'n/d':>5}")
    print(f"  {'-' * 75}")
    for name, sub, d in conds:
        n = len(sub)
        if n < 50:
            continue
        hr = sub["is_up"].mean() if d == "UP" else 1 - sub["is_up"].mean()
        edge = hr - BREAK_EVEN
        nd = n / 180
        f = " <-" if hr > 0.53 else ""
        print(f"  {name:>35} | {d:>3} | {n:>7,} | {hr:>6.3f} | {edge:>+7.3f} | {nd:>5.1f}{f}")

    return comb


# ═══════════════════════════════════════════════════════════════════════════
# PART 2: POLYMARKET PRICING
# ═══════════════════════════════════════════════════════════════════════════


def parse_window(q: str) -> dict | None:
    amap = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "xrp": "XRP"}
    asset = next((t for n, t in amap.items() if n in q.lower()), None)
    if not asset:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})(AM|PM)-(\d{1,2}):(\d{2})(AM|PM)\s*ET", q, re.I)
    if not m:
        return None
    h1, m1, a1 = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    h2, m2, a2 = int(m.group(4)), int(m.group(5)), m.group(6).upper()
    if a1 == "PM" and h1 != 12: h1 += 12
    if a1 == "AM" and h1 == 12: h1 = 0
    if a2 == "PM" and h2 != 12: h2 += 12
    if a2 == "AM" and h2 == 12: h2 = 0
    dur = (h2 * 60 + m2) - (h1 * 60 + m1)
    if dur < 0: dur += 1440
    return {"asset": asset, "duration_min": dur}


def run_polymarket() -> None:
    ph("PART 2: POLYMARKET PRICING -- IS MOMENTUM PRICED IN?")

    markets = pl.read_parquet(str(MARKETS_FILE))
    updown = markets.filter(
        pl.col("question").str.to_lowercase().str.contains("up or down")
        & (pl.col("question").str.to_lowercase().str.contains("bitcoin")
           | pl.col("question").str.to_lowercase().str.contains("ethereum")
           | pl.col("question").str.to_lowercase().str.contains("solana")
           | pl.col("question").str.to_lowercase().str.contains("xrp"))
        & pl.col("resolved_at").is_not_null()
        & pl.col("winner_outcome").is_in(["Up", "Down"])
    )

    parsed = []
    for row in updown.iter_rows(named=True):
        info = parse_window(row["question"])
        if info and info["duration_min"] in (5, 15):
            parsed.append({
                "condition_id": row["condition_id"],
                "asset": info["asset"],
                "duration_min": info["duration_min"],
                "winner": row["winner_outcome"],
                "is_up": 1 if row["winner_outcome"] == "Up" else 0,
                "resolved_at": row["resolved_at"],
            })

    pm = pl.DataFrame(parsed)
    print(f"  Resolved 5m/15m markets: {len(pm):,}")
    print(f"  By asset: {pm.group_by('asset').len().sort('len', descending=True)}")
    print(f"  By TF:    {pm.group_by('duration_min').agg(pl.len(), pl.col('is_up').mean().alias('up_rate'))}")

    if len(pm) == 0:
        return

    cids = set(pm["condition_id"].to_list())
    print(f"\n  Loading trades for {len(cids):,} markets...")

    tf_list = sorted(TRADES_DIR.glob("trades_2025*.parquet")) + sorted(TRADES_DIR.glob("trades_2026*.parquet"))
    chunks = []
    for f in tf_list:
        try:
            c = pl.scan_parquet(str(f)).filter(pl.col("condition_id").is_in(list(cids))).collect()
            if len(c) > 0:
                chunks.append(c)
                print(f"    {f.name}: {len(c):,} trades")
        except Exception as e:
            print(f"    {f.name}: skip ({e})")

    if not chunks:
        print("  No trades found")
        return

    trades = pl.concat(chunks)
    print(f"  Total: {len(trades):,} trades, {trades['condition_id'].n_unique()} markets")

    # Build price profiles
    ps("2a. PRICE AT OPEN / MID / CLOSE")
    pm_lk = {r["condition_id"]: r for r in pm.iter_rows(named=True)}
    prices = []
    for cid in pm["condition_id"].to_list():
        mt = trades.filter(pl.col("condition_id") == cid).sort("timestamp")
        if len(mt) < 5:
            continue
        row = pm_lk[cid]
        no = max(3, len(mt) // 10)

        def vwap(sub: pl.DataFrame) -> float:
            a = sub["amount_usd"].sum()
            return float((sub["price"] * sub["amount_usd"]).sum() / a) if a > 0 else 0.5

        prices.append({
            "condition_id": cid, "asset": row["asset"], "tf": row["duration_min"],
            "is_up": row["is_up"],
            "open_p": vwap(mt.head(no)),
            "mid_p": vwap(mt.slice(len(mt) * 4 // 10, max(1, len(mt) * 2 // 10))),
            "close_p": vwap(mt.tail(no)),
            "n_trades": len(mt),
            "vol_usd": float(mt["amount_usd"].sum()),
        })

    pdf = pl.DataFrame(prices)
    print(f"  Markets with prices: {len(pdf):,}")

    # The critical test
    ps("2b. KEY TEST: open price by outcome")
    for tf in [5, 15]:
        sub = pdf.filter(pl.col("tf") == tf)
        if len(sub) < 50:
            continue
        up = sub.filter(pl.col("is_up") == 1)
        dn = sub.filter(pl.col("is_up") == 0)
        print(f"\n  {tf}-min markets (n={len(sub)}, Up={len(up)}, Dn={len(dn)}):")
        for lbl, col in [("Open", "open_p"), ("Mid", "mid_p"), ("Close", "close_p")]:
            u, d = up[col].mean(), dn[col].mean()
            diff = u - d
            verdict = ("UNINFORMATIVE -> EDGE EXISTS" if abs(diff) < 0.02
                       else ("partially priced" if abs(diff) < 0.05 else "PRICED IN"))
            print(f"    {lbl:>6}: Up={u:.3f} Dn={d:.3f} diff={diff:+.3f}  {verdict}")

    # Calibration
    ps("2c. PM PRICE CALIBRATION")
    for tf in [5, 15]:
        sub = pdf.filter(pl.col("tf") == tf)
        if len(sub) < 100:
            continue
        print(f"\n  {tf}-min: open price vs actual outcome")
        print(f"  {'Bucket':>14} | {'n':>5} | {'Act Up%':>8} | {'Implied':>8} | {'Bias':>7}")
        print(f"  {'-' * 50}")
        for lo, hi in [(0, .35), (.35, .42), (.42, .48), (.48, .52), (.52, .58), (.58, .65), (.65, 1.01)]:
            b = sub.filter((pl.col("open_p") >= lo) & (pl.col("open_p") < hi))
            if len(b) < 10:
                continue
            act = b["is_up"].mean()
            imp = (lo + hi) / 2
            print(f"  [{lo:.2f}-{hi:.2f}){' ' * 3} | {len(b):>5} | {act:>8.3f} | {imp:>8.3f} | {act - imp:>+7.3f}")

    # Liquidity
    ps("2d. LIQUIDITY")
    for tf in [5, 15]:
        sub = pdf.filter(pl.col("tf") == tf)
        if len(sub) < 10:
            continue
        print(f"\n  {tf}-min:")
        print(f"    Median trades: {sub['n_trades'].median():,.0f}")
        print(f"    Median vol:    ${sub['vol_usd'].median():,.0f}")
        print(f"    Mean vol:      ${sub['vol_usd'].mean():,.0f}")
        viable = sub.filter(pl.col("vol_usd") > 1000)
        print(f"    >$1K vol:      {len(viable)} ({len(viable) / len(sub) * 100:.0f}%)")


# ═══════════════════════════════════════════════════════════════════════════
# PART 3: CROSS-ASSET SIGNALS
# ═══════════════════════════════════════════════════════════════════════════


def run_cross(comb: pl.DataFrame) -> None:
    ph("PART 3: CROSS-ASSET SIGNALS")
    btc = comb.filter(pl.col("asset") == "BTC").select("ts", "is_up", "mom_5m").rename(
        {"is_up": "btc_up", "mom_5m": "btc_m5"})
    eth = comb.filter(pl.col("asset") == "ETH").select("ts", "is_up", "mom_5m").rename(
        {"is_up": "eth_up", "mom_5m": "eth_m5"})
    sol = comb.filter(pl.col("asset") == "SOL").select("ts", "is_up", "mom_5m").rename(
        {"is_up": "sol_up", "mom_5m": "sol_m5"})
    j = btc.join(eth, on="ts", how="inner").join(sol, on="ts", how="inner")
    print(f"  Aligned: {len(j):,}\n")

    sigs = [
        ("ETH mom -> BTC", "eth_m5", "btc_up"), ("SOL mom -> BTC", "sol_m5", "btc_up"),
        ("BTC mom -> ETH", "btc_m5", "eth_up"), ("BTC mom -> SOL", "btc_m5", "sol_up"),
        ("ETH mom -> SOL", "eth_m5", "sol_up"), ("SOL mom -> ETH", "sol_m5", "eth_up"),
    ]
    print(f"  {'Signal':>20} | {'HiQ Up%':>8} | {'LoQ Up%':>8} | {'Spread':>8} | Note")
    print(f"  {'-' * 60}")
    for name, feat, tgt in sigs:
        hi = j.filter(pl.col(feat) > j[feat].quantile(0.75))
        lo = j.filter(pl.col(feat) < j[feat].quantile(0.25))
        if len(hi) > 50 and len(lo) > 50:
            uh, ul = hi[tgt].mean(), lo[tgt].mean()
            sp = uh - ul
            note = "CROSS SIGNAL" if sp > 0.06 else ""
            print(f"  {name:>20} | {uh:>8.3f} | {ul:>8.3f} | {sp:>+8.3f} | {note}")


def main() -> None:
    print("=" * 76)
    print("  COMPREHENSIVE EDGE SEARCH: Binance 180d + Polymarket Trade Data")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 76)

    comb = run_binance([])
    run_polymarket()
    run_cross(comb)

    ph("FINAL VERDICT")
    print("""  Key metric: open_price(Up winners) - open_price(Down winners)
    < 0.02 -> STRONG edge (PM doesn't know) -> BUILD IT
    0.02-0.05 -> MODERATE edge (partially priced) -> REFINE
    > 0.05 -> NO edge (already priced in) -> KILL""")


if __name__ == "__main__":
    main()
