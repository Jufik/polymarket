"use client";

import { useEffect, useState } from "react";
import {
  fetchInsiderPool,
  fetchInsiderSignals,
  fetchInsiderOverview,
  InsiderTrader,
  InsiderSignal,
  InsiderOverview,
} from "@/lib/api";

type Tab = "pool" | "signals";
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

/* ======================================================================== */
/* Overview Stats Cards                                                      */
/* ======================================================================== */

function OverviewCards({ data }: { data: InsiderOverview | null }) {
  if (!data) {
    return (
      <div className="flex gap-4 mb-8">
        {[...Array(5)].map((_, i) => (
          <div
            key={i}
            className="bg-gray-900 rounded-lg p-4 flex-1 animate-pulse h-20"
          />
        ))}
      </div>
    );
  }

  return (
    <div className="flex gap-4 mb-8">
      <div className="bg-gray-900 rounded-lg p-4 flex-1 border border-gray-800">
        <div className="text-sm text-gray-400">Pool Size</div>
        <div className="text-2xl font-mono font-bold text-blue-400">
          {data.pool_size}
        </div>
        <div className="text-xs text-gray-500 mt-1">qualified insiders</div>
      </div>
      <div className="bg-gray-900 rounded-lg p-4 flex-1 border border-gray-800">
        <div className="text-sm text-gray-400">Active Signals</div>
        <div className="text-2xl font-mono font-bold text-purple-400">
          {data.active_signals}
        </div>
        <div className="text-xs text-gray-500 mt-1">markets (48h)</div>
      </div>
      <div className="bg-gray-900 rounded-lg p-4 flex-1 border border-gray-800">
        <div className="text-sm text-gray-400">Total Intents</div>
        <div className="text-2xl font-mono font-bold text-gray-200">
          {data.total_intents}
        </div>
        <div className="text-xs text-gray-500 mt-1">all time</div>
      </div>
      <div className="bg-gray-900 rounded-lg p-4 flex-1 border border-gray-800">
        <div className="text-sm text-gray-400">Filled</div>
        <div className="text-2xl font-mono font-bold text-green-400">
          {data.filled_intents}
        </div>
        <div className="text-xs text-gray-500 mt-1">
          {data.total_intents > 0
            ? `${((data.filled_intents / data.total_intents) * 100).toFixed(0)}% fill rate`
            : "no intents yet"}
        </div>
      </div>
      <div className="bg-gray-900 rounded-lg p-4 flex-1 border border-gray-800">
        <div className="text-sm text-gray-400">Pool Refreshed</div>
        <div className="text-lg font-mono text-gray-300">
          {timeAgo(data.pool_refreshed_at)}
        </div>
      </div>
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
/* Signals Table                                                             */
/* ======================================================================== */

function SignalsTable({ signals }: { signals: InsiderSignal[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-gray-800/50 text-gray-400">
            <th className="p-3 text-left">Market</th>
            <th className="p-3 text-right">Consensus</th>
            <th className="p-3 text-right">Trades</th>
            <th className="p-3 text-right">Total USD</th>
            <th className="p-3 text-right">Max Price</th>
            <th className="p-3 text-left">Last Trade</th>
            <th className="p-3 text-center">Triggered</th>
            <th className="p-3 text-left">Intents</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((s) => (
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
                <td className="p-3 text-xs max-w-[300px]">
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
                <td className="p-3 text-right">
                  <span
                    className={`inline-block px-2 py-0.5 rounded font-mono font-bold text-sm ${
                      s.consensus_count >= 3
                        ? "bg-purple-900/50 text-purple-300"
                        : s.consensus_count >= 2
                          ? "bg-blue-900/50 text-blue-300"
                          : "bg-gray-800 text-gray-400"
                    }`}
                  >
                    {s.consensus_count}
                  </span>
                </td>
                <td className="p-3 text-right font-mono text-gray-300">
                  {s.trade_count}
                </td>
                <td className="p-3 text-right font-mono text-gray-200">
                  ${s.total_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </td>
                <td className="p-3 text-right font-mono text-gray-300">
                  ${s.max_price.toFixed(2)}
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
                    {s.intents.slice(0, 3).map((intent, i) => (
                      <span key={i}>{dispositionBadge(intent.disposition)}</span>
                    ))}
                    {s.intents.length > 3 && (
                      <span className="text-xs text-gray-500">
                        +{s.intents.length - 3}
                      </span>
                    )}
                  </div>
                </td>
              </tr>

              {/* Expanded detail row */}
              {expanded === s.condition_id && (
                <tr key={`${s.condition_id}-detail`}>
                  <td colSpan={8} className="p-0">
                    <div className="bg-gray-900/80 border-y border-gray-700 px-6 py-4">
                      {/* Insider addresses */}
                      <div className="mb-3">
                        <div className="text-xs text-gray-500 mb-1 uppercase tracking-wider">
                          Insider Addresses ({s.insider_addresses.length})
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {s.insider_addresses.map((addr) => (
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
                      {s.intents.length > 0 && (
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
                              {s.intents.map((intent, i) => (
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
                                    ${intent.size_usd.toFixed(2)}
                                  </td>
                                  <td className="py-1 pr-3 text-right font-mono">
                                    {intent.filled_price != null
                                      ? `$${intent.filled_price.toFixed(4)}`
                                      : "-"}
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
          {signals.length === 0 && (
            <tr>
              <td colSpan={8} className="p-8 text-center text-gray-500">
                No insider signals in the last 48 hours.
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
  const [overview, setOverview] = useState<InsiderOverview | null>(null);
  const [traders, setTraders] = useState<InsiderTrader[]>([]);
  const [signals, setSignals] = useState<InsiderSignal[]>([]);
  const [poolLoading, setPoolLoading] = useState(true);
  const [signalsLoading, setSignalsLoading] = useState(true);

  const refresh = async () => {
    try {
      const ov = await fetchInsiderOverview();
      setOverview(ov);
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
        setSignals(d.signals);
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

        <OverviewCards data={overview} />

        {/* Tab bar */}
        <div className="flex gap-1 mb-4 border-b border-gray-800">
          {(
            [
              ["signals", "Live Signals"],
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
                  Markets with Insider Activity ({signals.length})
                </span>
                <span className="text-xs text-gray-500">Last 48 hours</span>
              </div>
              {signalsLoading ? (
                <div className="p-8 text-center text-gray-500 animate-pulse">
                  Loading signals...
                </div>
              ) : (
                <SignalsTable signals={signals} />
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
