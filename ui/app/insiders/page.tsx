"use client";

import { useEffect, useState } from "react";
import {
  fetchInsiderPool,
  fetchInsiderSignals,
  fetchPoolHealth,
  InsiderTrader,
  InsiderSignal,
  PoolHealth,
} from "@/lib/api";

type Tab = "signals" | "candidates" | "pool";
type SortKey =
  | "effective_hr"
  | "hr_excess"
  | "total_positions"
  | "high_pct"
  | "total_pnl"
  | "avg_volume";

function timeAgo(iso: string | null) {
  if (!iso) return "never";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 0) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function hrColor(hr: number) {
  if (hr >= 0.85) return "text-green-400";
  if (hr >= 0.75) return "text-emerald-400";
  return "text-yellow-400";
}

function pnlColor(v: number) {
  return v >= 0 ? "text-green-400" : "text-red-400";
}

function dispositionBadge(d: string) {
  const colors: Record<string, string> = {
    filled: "bg-green-900/50 text-green-400",
    risk_rejected: "bg-yellow-900/50 text-yellow-400",
    rejected: "bg-red-900/50 text-red-400",
  };
  return (
    <span
      className={`inline-block px-1.5 py-0.5 text-xs rounded font-medium ${colors[d] || "bg-gray-800 text-gray-400"}`}
    >
      {d}
    </span>
  );
}

const CATEGORY_COLORS: Record<string, string> = {
  sports: "bg-blue-900/50 text-blue-300 border-blue-700/50",
  politics: "bg-purple-900/50 text-purple-300 border-purple-700/50",
  other: "bg-purple-900/50 text-purple-300 border-purple-700/50",
  culture: "bg-amber-900/50 text-amber-300 border-amber-700/50",
  finance: "bg-emerald-900/50 text-emerald-300 border-emerald-700/50",
  weather: "bg-cyan-900/50 text-cyan-300 border-cyan-700/50",
  crypto: "bg-red-900/50 text-red-300 border-red-700/50",
  esports: "bg-red-900/50 text-red-300 border-red-700/50",
};

function categoryBadge(category: string) {
  const cls = CATEGORY_COLORS[category] || "bg-gray-800 text-gray-400 border-gray-700/50";
  return (
    <span className={`inline-block px-1.5 py-0.5 text-xs rounded border font-medium ${cls}`}>
      {category}
    </span>
  );
}

const POOL_DISPLAY: Record<string, { label: string; color: string; border: string }> = {
  s2_insider_sports: {
    label: "Sports",
    color: "text-blue-400",
    border: "border-blue-500/30",
  },
  s2_insider_politics: {
    label: "Politics",
    color: "text-purple-400",
    border: "border-purple-500/30",
  },
  s2_insider_misc: {
    label: "Misc",
    color: "text-amber-400",
    border: "border-amber-500/30",
  },
};

/* ======================================================================== */
/* Pool Health Cards                                                         */
/* ======================================================================== */

/** Thin progress bar: fraction 0–1, color class. */
function ProgressBar({ pct, colorClass }: { pct: number; colorClass: string }) {
  const clamped = Math.min(1, Math.max(0, pct));
  return (
    <div className="w-full h-1.5 bg-gray-800 rounded-full overflow-hidden">
      <div
        className={`h-full rounded-full ${colorClass}`}
        style={{ width: `${(clamped * 100).toFixed(1)}%` }}
      />
    </div>
  );
}

function PoolHealthCards({ pools }: { pools: PoolHealth[] | null }) {
  if (!pools) {
    return (
      <div className="flex gap-4 mb-8">
        {[...Array(3)].map((_, i) => (
          <div
            key={i}
            className="bg-gray-900 rounded-lg p-4 flex-1 animate-pulse h-40"
          />
        ))}
      </div>
    );
  }

  return (
    <div className="flex gap-4 mb-8">
      {pools.map((p) => {
        const display = POOL_DISPLAY[p.pool] || {
          label: p.pool,
          color: "text-gray-400",
          border: "border-gray-700/50",
        };

        // Health dot: green=active&OK, yellow=no data, red=HR<40%
        let healthDot = "text-yellow-400";
        if (p.fills_7d > 0) {
          healthDot =
            p.hr_7d !== null && p.hr_7d < 0.4
              ? "text-red-400"
              : "text-green-400";
        }

        const deployedUsd = p.capital_pct * p.capital_usd;
        const availableUsd = p.capital_usd - deployedUsd;
        const capitalBarColor =
          p.capital_pct > 0.9
            ? "bg-red-500"
            : p.capital_pct > 0.6
            ? "bg-yellow-500"
            : "bg-blue-500";

        // Expected ROI: HR × (1/avg_entry - 1) approximation.
        // For NO at 0.35 avg: 74% HR → edge = 0.74 × (1/0.35 - 1) - 0.26 × 1 ≈ +0.6 → simplify
        // Show win_rate × avg_return hint instead.
        const roiHint =
          p.hr_7d !== null && p.resolved_7d > 0
            ? `~${((p.hr_7d - 0.5) * 100).toFixed(0)}pp edge`
            : null;

        return (
          <div
            key={p.pool}
            className={`bg-gray-900 rounded-lg p-4 flex-1 border ${display.border} space-y-3`}
          >
            {/* Header */}
            <div className="flex items-center gap-2">
              <span className={`text-base ${healthDot}`}>{"\u25CF"}</span>
              <span className={`text-sm font-bold ${display.color}`}>
                {display.label}
              </span>
              <span className="text-xs text-gray-500 ml-auto font-mono">
                c≥{p.consensus_threshold}
              </span>
            </div>

            {/* Budget allocation bar */}
            <div>
              <div className="flex justify-between text-xs mb-1">
                <span className="text-gray-400">Capital Budget</span>
                <span className="font-mono text-gray-200">
                  ${deployedUsd.toFixed(0)} / ${p.capital_usd}
                </span>
              </div>
              <ProgressBar pct={p.capital_pct} colorClass={capitalBarColor} />
              <div className="flex justify-between text-xs mt-0.5 text-gray-500">
                <span>{p.open_positions} open pos</span>
                <span>${availableUsd.toFixed(0)} free</span>
              </div>
            </div>

            {/* Stats grid */}
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <div className="text-gray-400">Fills (7d)</div>
              <div className="font-mono text-gray-200 text-right">
                {p.fills_7d}
                {p.fill_rate !== null && (
                  <span className="text-gray-500 ml-1">
                    ({(p.fill_rate * 100).toFixed(0)}% rate)
                  </span>
                )}
              </div>

              <div className="text-gray-400">Hit Rate (7d)</div>
              <div
                className={`font-mono text-right ${
                  p.hr_7d !== null
                    ? p.hr_7d >= 0.5
                      ? "text-green-400"
                      : "text-red-400"
                    : "text-gray-500"
                }`}
              >
                {p.hr_7d !== null
                  ? `${(p.hr_7d * 100).toFixed(0)}% (${p.wins_7d}W/${p.resolved_7d - p.wins_7d}L)`
                  : "—"}
              </div>

              <div className="text-gray-400">Realized PnL (7d)</div>
              <div
                className={`font-mono text-right font-semibold ${pnlColor(p.pnl_7d)}`}
              >
                {p.pnl_7d >= 0 ? "+" : ""}${p.pnl_7d.toFixed(2)}
              </div>

              <div className="text-gray-400">Edge estimate</div>
              <div className="font-mono text-right text-amber-400">
                {roiHint ?? "—"}
              </div>

              <div className="text-gray-400">Candidates</div>
              <div className="font-mono text-right text-purple-400">
                {p.candidates}
              </div>

              <div className="text-gray-400">Rejected</div>
              <div className="font-mono text-right text-gray-500">
                {p.risk_rejected_7d} risk / {p.rejected_7d} other
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ======================================================================== */
/* Pool Table                                                                */
/* ======================================================================== */

function PoolTable({ traders }: { traders: InsiderTrader[] }) {
  const [sortKey, setSortKey] = useState<SortKey>("hr_excess");
  const [sortAsc, setSortAsc] = useState(false);

  const sorted = [...traders].sort((a, b) => {
    const av = a[sortKey] as number;
    const bv = b[sortKey] as number;
    return sortAsc ? av - bv : bv - av;
  });

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(key);
      setSortAsc(false);
    }
  };

  const sortIcon = (key: SortKey) => {
    if (sortKey !== key) return "";
    return sortAsc ? " \u25B2" : " \u25BC";
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-gray-800/50 text-gray-400">
            <th className="p-3 text-left">Trader</th>
            <th className="p-3 text-center">Dir</th>
            <th className="p-3 text-center">Live</th>
            <th
              className="p-3 text-right cursor-pointer hover:text-gray-200"
              onClick={() => handleSort("effective_hr")}
            >
              Bayesian HR{sortIcon("effective_hr")}
            </th>
            <th
              className="p-3 text-right cursor-pointer hover:text-gray-200"
              onClick={() => handleSort("hr_excess")}
            >
              HR Excess{sortIcon("hr_excess")}
            </th>
            <th
              className="p-3 text-right cursor-pointer hover:text-gray-200"
              onClick={() => handleSort("total_positions")}
            >
              Positions{sortIcon("total_positions")}
            </th>
            <th className="p-3 text-right">W/L (YES)</th>
            <th className="p-3 text-right">W/L (NO)</th>
            <th
              className="p-3 text-right cursor-pointer hover:text-gray-200"
              onClick={() => handleSort("high_pct")}
            >
              HIGH %{sortIcon("high_pct")}
            </th>
            <th
              className="p-3 text-right cursor-pointer hover:text-gray-200"
              onClick={() => handleSort("total_pnl")}
            >
              Total PnL{sortIcon("total_pnl")}
            </th>
            <th
              className="p-3 text-right cursor-pointer hover:text-gray-200"
              onClick={() => handleSort("avg_volume")}
            >
              Avg Vol{sortIcon("avg_volume")}
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((t) => (
            <tr
              key={t.trader}
              className="border-t border-gray-800 hover:bg-gray-800/30"
            >
              <td className="p-3 font-mono text-xs text-gray-300">
                <a
                  href={`https://polygonscan.com/address/${t.trader}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-blue-400 hover:underline"
                >
                  {t.trader.slice(0, 8)}...{t.trader.slice(-6)}
                </a>
              </td>
              <td className="p-3 text-center">
                <span
                  className={`inline-block px-1.5 py-0.5 text-xs rounded font-medium ${
                    t.best_direction === "YES"
                      ? "bg-green-900/50 text-green-400"
                      : "bg-red-900/50 text-red-400"
                  }`}
                >
                  {t.best_direction}
                </span>
              </td>
              <td className="p-3 text-center">
                {t.in_live_pool ? (
                  <span className="text-green-400 text-lg">{"\u25CF"}</span>
                ) : (
                  <span className="text-gray-600 text-lg">{"\u25CB"}</span>
                )}
              </td>
              <td className={`p-3 text-right font-mono ${hrColor(t.effective_hr)}`}>
                {(t.effective_hr * 100).toFixed(1)}%
              </td>
              <td className="p-3 text-right font-mono text-emerald-400">
                +{(t.hr_excess * 100).toFixed(1)}pp
              </td>
              <td className="p-3 text-right font-mono text-gray-200">
                {t.total_positions}
              </td>
              <td className="p-3 text-right font-mono text-gray-400 text-xs">
                {t.yes_wins}/{t.yes_total}
              </td>
              <td className="p-3 text-right font-mono text-gray-400 text-xs">
                {t.no_wins}/{t.no_total}
              </td>
              <td className="p-3 text-right font-mono text-gray-200">
                {(t.high_pct * 100).toFixed(0)}%
              </td>
              <td className={`p-3 text-right font-mono ${pnlColor(t.total_pnl)}`}>
                {t.total_pnl >= 0 ? "+" : ""}${t.total_pnl.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </td>
              <td className="p-3 text-right font-mono text-gray-300">
                ${t.avg_volume.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </td>
            </tr>
          ))}
          {sorted.length === 0 && (
            <tr>
              <td colSpan={11} className="p-8 text-center text-gray-500">
                No insiders found. Check ClickHouse connectivity and pool parameters.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

/* ======================================================================== */
/* Signals Table (enhanced with category + consensus progress)               */
/* ======================================================================== */

function SignalsTable({
  signals,
  showExcluded = true,
}: {
  signals: InsiderSignal[];
  showExcluded?: boolean;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);

  const filtered = showExcluded
    ? signals
    : signals.filter((s) => s.pool !== null);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-gray-800/50 text-gray-400">
            <th className="p-3 text-left">Market</th>
            <th className="p-3 text-center">Category</th>
            <th className="p-3 text-center">Pool</th>
            <th className="p-3 text-center">Consensus</th>
            <th className="p-3 text-right">Trades</th>
            <th className="p-3 text-right">Total USD</th>
            <th className="p-3 text-left">Last Trade</th>
            <th className="p-3 text-center">Triggered</th>
            <th className="p-3 text-left">Intents</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((s) => (
            <>
              <tr
                key={s.condition_id}
                className={`border-t border-gray-800 hover:bg-gray-800/30 cursor-pointer ${
                  expanded === s.condition_id ? "bg-gray-800/40" : ""
                }`}
                onClick={() =>
                  setExpanded(
                    expanded === s.condition_id ? null : s.condition_id
                  )
                }
              >
                <td className="p-3 text-xs max-w-[280px]">
                  {s.polymarket_url ? (
                    <a
                      href={s.polymarket_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-400 hover:text-blue-300 hover:underline"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {s.question || s.condition_id.slice(0, 16) + "..."}
                    </a>
                  ) : (
                    <span className="text-gray-300 truncate block">
                      {s.question || s.condition_id.slice(0, 16) + "..."}
                    </span>
                  )}
                </td>
                <td className="p-3 text-center">
                  {categoryBadge(s.category)}
                </td>
                <td className="p-3 text-center text-xs">
                  {s.pool ? (
                    <span className={POOL_DISPLAY[s.pool]?.color || "text-gray-400"}>
                      {POOL_DISPLAY[s.pool]?.label || s.pool}
                    </span>
                  ) : (
                    <span className="text-red-400/60">excluded</span>
                  )}
                </td>
                <td className="p-3 text-center">
                  {(s.consensus_threshold ?? 0) > 0 ? (
                    <span
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded font-mono font-bold text-sm ${
                        s.consensus_met
                          ? "bg-green-900/50 text-green-300"
                          : (s.consensus_count ?? 0) >= (s.consensus_threshold ?? 1) - 1
                            ? "bg-yellow-900/50 text-yellow-300"
                            : "bg-gray-800 text-gray-400"
                      }`}
                    >
                      {s.consensus_count ?? 0}/{s.consensus_threshold ?? 0}
                    </span>
                  ) : (
                    <span className="text-gray-500 font-mono text-sm">
                      {s.consensus_count ?? 0}
                    </span>
                  )}
                </td>
                <td className="p-3 text-right font-mono text-gray-300">
                  {s.trade_count ?? 0}
                </td>
                <td className="p-3 text-right font-mono text-gray-200">
                  ${(s.total_usd ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </td>
                <td className="p-3 text-xs text-gray-400">
                  {timeAgo(s.last_trade)}
                </td>
                <td className="p-3 text-center">
                  {s.triggered ? (
                    <span className="text-green-400 font-bold">YES</span>
                  ) : (
                    <span className="text-gray-600">-</span>
                  )}
                </td>
                <td className="p-3">
                  <div className="flex gap-1 flex-wrap">
                    {(s.intents ?? []).slice(0, 3).map((intent, i) => (
                      <span key={i}>{dispositionBadge(intent.disposition)}</span>
                    ))}
                    {(s.intents ?? []).length > 3 && (
                      <span className="text-xs text-gray-500">
                        +{(s.intents ?? []).length - 3}
                      </span>
                    )}
                  </div>
                </td>
              </tr>

              {/* Expanded detail row */}
              {expanded === s.condition_id && (
                <tr key={`${s.condition_id}-detail`}>
                  <td colSpan={9} className="p-0">
                    <div className="bg-gray-900/80 border-y border-gray-700 px-6 py-4">
                      {/* Insider addresses */}
                      <div className="mb-3">
                        <div className="text-xs text-gray-500 mb-1 uppercase tracking-wider">
                          Insider Addresses ({(s.insider_addresses ?? []).length})
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {(s.insider_addresses ?? []).map((addr) => (
                            <span
                              key={addr}
                              className="font-mono text-xs bg-gray-800 px-2 py-1 rounded text-gray-300"
                            >
                              {addr}
                            </span>
                          ))}
                        </div>
                      </div>

                      {/* Intent history */}
                      {(s.intents ?? []).length > 0 && (
                        <div>
                          <div className="text-xs text-gray-500 mb-1 uppercase tracking-wider">
                            Strategy Intents
                          </div>
                          <table className="w-full text-xs">
                            <thead>
                              <tr className="text-gray-500">
                                <th className="py-1 pr-3 text-left">Strategy</th>
                                <th className="py-1 pr-3 text-left">Action</th>
                                <th className="py-1 pr-3 text-right">Size</th>
                                <th className="py-1 pr-3 text-right">
                                  Fill Price
                                </th>
                                <th className="py-1 pr-3 text-left">Status</th>
                                <th className="py-1 pr-3 text-left">Time</th>
                                <th className="py-1 text-left">Reason</th>
                              </tr>
                            </thead>
                            <tbody>
                              {(s.intents ?? []).map((intent, i) => (
                                <tr
                                  key={i}
                                  className="border-t border-gray-800/50"
                                >
                                  <td className="py-1 pr-3 font-mono">
                                    {intent.strategy}
                                  </td>
                                  <td className="py-1 pr-3">
                                    <span
                                      className={
                                        intent.side === "BUY"
                                          ? "text-green-400"
                                          : "text-red-400"
                                      }
                                    >
                                      {intent.side}
                                    </span>{" "}
                                    {intent.outcome}
                                  </td>
                                  <td className="py-1 pr-3 text-right font-mono">
                                    ${(intent.size_usd ?? 0).toFixed(2)}
                                  </td>
                                  <td className="py-1 pr-3 text-right font-mono">
                                    {intent.filled_price != null
                                      ? `$${(intent.filled_price ?? 0).toFixed(4)}`
                                      : "-"}
                                    {intent.filled_price != null && (
                                      <span className="text-gray-500 ml-1">({intent.outcome})</span>
                                    )}
                                  </td>
                                  <td className="py-1 pr-3">
                                    {dispositionBadge(intent.disposition)}
                                  </td>
                                  <td className="py-1 pr-3 text-gray-400">
                                    {timeAgo(intent.captured_at)}
                                  </td>
                                  <td className="py-1 text-gray-400 max-w-xs truncate">
                                    {intent.reason}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  </td>
                </tr>
              )}
            </>
          ))}
          {filtered.length === 0 && (
            <tr>
              <td colSpan={9} className="p-8 text-center text-gray-500">
                No signals found.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

/* ======================================================================== */
/* Main Page                                                                 */
/* ======================================================================== */

export default function InsidersPage() {
  const [tab, setTab] = useState<Tab>("signals");
  const [poolHealthData, setPoolHealthData] = useState<PoolHealth[] | null>(null);
  const [traders, setTraders] = useState<InsiderTrader[]>([]);
  const [signals, setSignals] = useState<InsiderSignal[]>([]);
  const [poolLoading, setPoolLoading] = useState(true);
  const [signalsLoading, setSignalsLoading] = useState(true);

  const candidates = signals.filter(
    (s) => s.pool !== null && !s.consensus_met
  );
  const triggeredSignals = signals.filter(
    (s) => s.consensus_met || s.triggered
  );

  const refresh = async () => {
    // Always fetch pool health + signals
    try {
      const h = await fetchPoolHealth();
      setPoolHealthData(h.pools);
    } catch {
      /* ignore */
    }

    if (tab === "pool") {
      try {
        const d = await fetchInsiderPool();
        setTraders(d.traders);
      } catch {
        /* ignore */
      }
      setPoolLoading(false);
    } else {
      try {
        const d = await fetchInsiderSignals(48);
        setSignals(d.signals ?? []);
      } catch {
        /* ignore */
      }
      setSignalsLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 15000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  return (
    <main className="min-h-screen bg-gray-950 text-gray-100 p-8">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-3xl font-bold mb-6">Insider Strategy</h1>

        <PoolHealthCards pools={poolHealthData} />

        {/* Tab bar */}
        <div className="flex gap-1 mb-4 border-b border-gray-800">
          {(
            [
              ["signals", `Live Signals (${triggeredSignals.length})`],
              ["candidates", `Candidates (${candidates.length})`],
              ["pool", "Insider Pool"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                tab === key
                  ? "border-purple-500 text-purple-400"
                  : "border-transparent text-gray-500 hover:text-gray-300"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="bg-gray-900 rounded-lg overflow-hidden">
          {tab === "signals" && (
            <>
              <div className="bg-gray-800 px-4 py-2 flex items-center justify-between">
                <span className="text-sm font-semibold text-gray-300">
                  Markets with Consensus Met ({triggeredSignals.length})
                </span>
                <span className="text-xs text-gray-500">Last 48 hours</span>
              </div>
              {signalsLoading ? (
                <div className="p-8 text-center text-gray-500 animate-pulse">
                  Loading signals...
                </div>
              ) : (
                <SignalsTable
                  signals={triggeredSignals}
                  showExcluded={false}
                />
              )}
            </>
          )}

          {tab === "candidates" && (
            <>
              <div className="bg-gray-800 px-4 py-2 flex items-center justify-between">
                <span className="text-sm font-semibold text-gray-300">
                  Building Consensus ({candidates.length})
                </span>
                <span className="text-xs text-gray-500">
                  Markets entering the pool — below threshold
                </span>
              </div>
              {signalsLoading ? (
                <div className="p-8 text-center text-gray-500 animate-pulse">
                  Loading candidates...
                </div>
              ) : (
                <SignalsTable
                  signals={[...candidates].sort(
                    (a, b) =>
                      (b.consensus_count ?? 0) / Math.max(b.consensus_threshold ?? 1, 1) -
                      (a.consensus_count ?? 0) / Math.max(a.consensus_threshold ?? 1, 1)
                  )}
                  showExcluded={false}
                />
              )}
            </>
          )}

          {tab === "pool" && (
            <>
              <div className="bg-gray-800 px-4 py-2 flex items-center justify-between">
                <span className="text-sm font-semibold text-gray-300">
                  Qualified Insiders ({traders.length})
                </span>
                <span className="text-xs text-gray-500">
                  Bayesian HR {"\u2265"} 75% | HIGH % {"\u2265"} 20% | {"\u2265"} 3
                  positions
                </span>
              </div>
              {poolLoading ? (
                <div className="p-8 text-center text-gray-500 animate-pulse">
                  Loading pool...
                </div>
              ) : (
                <PoolTable traders={traders} />
              )}
            </>
          )}
        </div>
      </div>
    </main>
  );
}
