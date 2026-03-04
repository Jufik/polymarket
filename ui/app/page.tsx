"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchHealth,
  fetchIntents,
  fetchAnalytics,
  fetchPnlSummary,
  fetchPoolHealth,
  triggerPanic,
  Intent,
  AnalyticsData,
  PnlSummary,
  PoolHealth,
} from "@/lib/api";
import IntentDetailPanel from "@/components/IntentDetailPanel";
import ReconciliationChart from "@/components/ReconciliationChart";

function CountdownBadge({ endDate, resolvedAt, winner }: {
  endDate: string | null;
  resolvedAt: string | null;
  winner: string | null;
}) {
  if (resolvedAt) {
    return (
      <span className="inline-block px-1.5 py-0.5 text-xs rounded bg-yellow-900/50 text-yellow-400">
        {winner || "Resolved"}
      </span>
    );
  }
  if (!endDate) return <span className="text-xs text-gray-600">-</span>;

  const diff = new Date(endDate).getTime() - Date.now();
  if (diff <= 0) return <span className="text-xs text-orange-400">Awaiting resolution</span>;

  const d = Math.floor(diff / 86400000);
  const h = Math.floor((diff % 86400000) / 3600000);

  return (
    <span className="text-xs font-mono text-gray-400">
      {d > 0 ? `${d}d ${h}h` : `${h}h`}
    </span>
  );
}

export default function Dashboard() {
  const [health, setHealth] = useState<string>("loading...");
  const [panicResult, setPanicResult] = useState<string | null>(null);
  const [intents, setIntents] = useState<Intent[]>([]);
  const [intentTotal, setIntentTotal] = useState(0);
  const [intentFilter, setIntentFilter] = useState<string>("");
  const [analytics, setAnalytics] = useState<AnalyticsData | null>(null);
  const [pnl, setPnl] = useState<PnlSummary | null>(null);
  const [poolHealth, setPoolHealth] = useState<PoolHealth[] | null>(null);

  // Hover panel state
  const [hoveredIntentId, setHoveredIntentId] = useState<number | null>(null);
  const [hoverRect, setHoverRect] = useState<DOMRect | null>(null);
  const leaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleRowEnter = useCallback((id: number, el: HTMLTableRowElement) => {
    if (leaveTimerRef.current) {
      clearTimeout(leaveTimerRef.current);
      leaveTimerRef.current = null;
    }
    setHoveredIntentId(id);
    setHoverRect(el.getBoundingClientRect());
  }, []);

  const handleRowLeave = useCallback(() => {
    leaveTimerRef.current = setTimeout(() => {
      setHoveredIntentId(null);
      setHoverRect(null);
    }, 300);
  }, []);

  const refresh = async () => {
    try {
      const h = await fetchHealth();
      setHealth(h.status);
    } catch {
      setHealth("unreachable");
    }
    try {
      const data = await fetchIntents(
        50,
        0,
        undefined,
        intentFilter || undefined
      );
      setIntents(data.intents);
      setIntentTotal(data.total);
    } catch {
      /* ignore */
    }
    try {
      const a = await fetchAnalytics();
      setAnalytics(a);
    } catch {
      /* ignore */
    }
    try {
      const p = await fetchPnlSummary();
      setPnl(p);
    } catch {
      /* ignore */
    }
    try {
      const ph = await fetchPoolHealth();
      setPoolHealth(ph.pools);
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intentFilter]);

  const handlePanic = async () => {
    if (!confirm("PANIC: Close ALL positions immediately?")) return;
    const result = await triggerPanic();
    setPanicResult(
      `Closed: ${result.total_closed}, Failed: ${result.total_failed}`
    );
    refresh();
  };

  const dispositionColor = (d: string) => {
    if (d === "filled") return "text-green-400";
    if (d === "risk_rejected") return "text-yellow-400";
    return "text-red-400";
  };

  const timeAgo = (iso: string) => {
    const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    return `${Math.floor(s / 3600)}h ago`;
  };

  return (
    <main className="min-h-screen bg-gray-950 text-gray-100 p-8">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-3xl font-bold mb-6">Polymarket Trading</h1>

        {/* Status Bar */}
        <div className="flex gap-4 mb-8">
          <div className="bg-gray-900 rounded-lg p-4 flex-1">
            <div className="text-sm text-gray-400">Health</div>
            <div
              className={`text-lg font-mono ${health === "healthy" ? "text-green-400" : "text-red-400"}`}
            >
              {health}
            </div>
          </div>
          <div className="bg-gray-900 rounded-lg p-4 flex-1">
            <div className="text-sm text-gray-400">Positions</div>
            <div className="text-lg font-mono">{pnl ? pnl.live.count : "-"}</div>
          </div>
          <div className="bg-gray-900 rounded-lg p-4 flex-1">
            <div className="text-sm text-gray-400">Total Exposure</div>
            <div className="text-lg font-mono">
              {pnl ? `$${pnl.live.invested.toFixed(2)}` : "-"}
            </div>
          </div>
          <div className="bg-gray-900 rounded-lg p-4 flex-1">
            <div className="text-sm text-gray-400">Intents</div>
            <div className="text-lg font-mono">{intentTotal}</div>
          </div>
          <div className="bg-gray-900 rounded-lg p-4 flex-1">
            <div className="text-sm text-gray-400">Data Health</div>
            {analytics ? (
              <div
                className={`text-lg font-mono ${
                  analytics.data_health.broken_count < 10
                    ? "text-green-400"
                    : "text-yellow-400"
                }`}
              >
                {analytics.data_health.broken_count === 0
                  ? "clean"
                  : `${analytics.data_health.broken_count} broken`}
              </div>
            ) : (
              <div className="text-lg font-mono text-gray-500">-</div>
            )}
          </div>
          <button
            onClick={handlePanic}
            className="bg-red-700 hover:bg-red-600 text-white font-bold px-8 py-4 rounded-lg text-lg"
          >
            PANIC
          </button>
        </div>

        {panicResult && (
          <div className="bg-yellow-900/50 border border-yellow-600 rounded p-3 mb-4">
            {panicResult}
          </div>
        )}

        {/* PnL Summary Cards */}
        {pnl && (
          <div className="flex gap-4 mb-8">
            <div className="bg-gray-900 rounded-lg p-4 flex-1 border border-gray-800">
              <div className="text-sm text-gray-400">Total PnL</div>
              <div
                className={`text-2xl font-mono font-bold ${
                  pnl.summary.total_pnl_usd >= 0 ? "text-green-400" : "text-red-400"
                }`}
              >
                {pnl.summary.total_pnl_usd >= 0 ? "+" : ""}
                ${pnl.summary.total_pnl_usd.toFixed(2)}
              </div>
              <div className="text-xs text-gray-500 mt-1">
                {pnl.summary.n_fills} fills / ${pnl.summary.total_invested.toFixed(0)} invested
              </div>
            </div>
            <div className="bg-gray-900 rounded-lg p-4 flex-1 border border-gray-800">
              <div className="text-sm text-gray-400">Live (Unrealized)</div>
              <div
                className={`text-2xl font-mono font-bold ${
                  pnl.live.pnl_usd >= 0 ? "text-green-400" : "text-red-400"
                }`}
              >
                {pnl.live.pnl_usd >= 0 ? "+" : ""}
                ${pnl.live.pnl_usd.toFixed(2)}
              </div>
              <div className="text-xs text-gray-500 mt-1">
                {pnl.live.count} positions / ${pnl.live.invested.toFixed(0)} invested
              </div>
            </div>
            <div className="bg-gray-900 rounded-lg p-4 flex-1 border border-gray-800">
              <div className="text-sm text-gray-400">Resolved (Realized)</div>
              <div
                className={`text-2xl font-mono font-bold ${
                  pnl.resolved.pnl_usd >= 0 ? "text-green-400" : "text-red-400"
                }`}
              >
                {pnl.resolved.pnl_usd >= 0 ? "+" : ""}
                ${pnl.resolved.pnl_usd.toFixed(2)}
              </div>
              <div className="text-xs text-gray-500 mt-1">
                {pnl.resolved.wins}W / {pnl.resolved.losses}L
                {pnl.summary.win_rate != null &&
                  ` (${(pnl.summary.win_rate * 100).toFixed(0)}%)`}
              </div>
            </div>
          </div>
        )}

        {/* Strategy Pool Health (compact) */}
        {poolHealth && poolHealth.length > 0 && (
          <div className="flex gap-4 mb-8">
            {poolHealth.map((p) => {
              const labels: Record<string, { name: string; color: string; border: string }> = {
                s2_insider_sports: { name: "Sports", color: "text-blue-400", border: "border-blue-500/30" },
                s2_insider_politics: { name: "Politics", color: "text-purple-400", border: "border-purple-500/30" },
                s2_insider_misc: { name: "Misc", color: "text-amber-400", border: "border-amber-500/30" },
              };
              const display = labels[p.pool] || { name: p.pool, color: "text-gray-400", border: "border-gray-700" };
              const healthDot = p.fills_7d === 0
                ? "text-yellow-400"
                : p.hr_7d !== null && p.hr_7d < 0.4
                  ? "text-red-400"
                  : "text-green-400";
              return (
                <a
                  key={p.pool}
                  href="/insiders"
                  className={`bg-gray-900 rounded-lg p-3 flex-1 border ${display.border} hover:bg-gray-800/50 transition-colors`}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <span className={healthDot}>{"\u25CF"}</span>
                    <span className={`text-sm font-semibold ${display.color}`}>
                      {display.name}
                    </span>
                    <span className="text-xs text-gray-500 ml-auto font-mono">
                      c{"\u2265"}{p.consensus_threshold}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 text-xs font-mono">
                    <span className="text-gray-300">
                      {p.fills_7d} fills
                    </span>
                    <span className="text-gray-600">{"\u00B7"}</span>
                    <span className={p.hr_7d !== null ? (p.hr_7d >= 0.5 ? "text-green-400" : "text-red-400") : "text-gray-500"}>
                      {p.hr_7d !== null ? `${(p.hr_7d * 100).toFixed(0)}%` : "-"}
                    </span>
                    <span className="text-gray-600">{"\u00B7"}</span>
                    <span className="text-gray-300" title="Capital deployed in open positions">
                      ${(p.invested_7d ?? 0).toFixed(0)} inv
                    </span>
                    {p.resolved_7d > 0 && (
                      <>
                        <span className="text-gray-600">{"\u00B7"}</span>
                        <span className={p.pnl_7d >= 0 ? "text-green-400" : "text-red-400"}>
                          {p.pnl_7d >= 0 ? "+" : ""}${p.pnl_7d.toFixed(0)}
                        </span>
                      </>
                    )}
                    <span className="text-gray-600">{"\u00B7"}</span>
                    <span className="text-purple-400">
                      {p.candidates} cand
                    </span>
                  </div>
                </a>
              );
            })}
          </div>
        )}

        {/* Positions Table — from PnL live positions (PG-based, not in-memory tracker) */}
        <div className="bg-gray-900 rounded-lg overflow-hidden mb-8">
          <div className="bg-gray-800 px-4 py-2 flex items-center justify-between">
            <span className="text-sm font-semibold text-gray-300">
              Open Positions ({pnl?.live.count ?? 0})
            </span>
            <span className="text-xs text-gray-500">
              Invested: ${pnl?.live.invested.toFixed(2) ?? "0.00"}
            </span>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-800/50 text-gray-400">
                <th className="p-3 text-left">Market</th>
                <th className="p-3 text-left">Strategy</th>
                <th className="p-3 text-center">Outcome</th>
                <th className="p-3 text-right">Invested</th>
                <th className="p-3 text-right">Entry</th>
                <th className="p-3 text-right">Current</th>
                <th className="p-3 text-right">Unrealized PnL</th>
                <th className="p-3 text-left">Time Left</th>
              </tr>
            </thead>
            <tbody>
              {(pnl?.live.positions ?? []).map((p) => {
                const isExpired =
                  p.end_date && new Date(p.end_date).getTime() < Date.now();
                return (
                  <tr key={p.id} className="border-t border-gray-800 hover:bg-gray-800/30">
                    <td className="p-3 text-xs max-w-[260px]">
                      <span className="text-gray-300">
                        {p.question || `#${p.id}`}
                      </span>
                    </td>
                    <td className="p-3 font-mono text-xs text-gray-400">
                      {p.strategy || "\u2014"}
                    </td>
                    <td className="p-3 text-center">
                      <span
                        className={`inline-block px-1.5 py-0.5 text-xs rounded font-medium ${
                          p.outcome === "YES"
                            ? "bg-green-900/50 text-green-400"
                            : p.outcome === "NO"
                            ? "bg-red-900/50 text-red-400"
                            : "bg-gray-800 text-gray-400"
                        }`}
                      >
                        {p.outcome}
                      </span>
                    </td>
                    <td className="p-3 text-right font-mono text-gray-200">
                      ${p.size_usd.toFixed(2)}
                    </td>
                    <td className="p-3 text-right font-mono text-gray-400">
                      ${p.entry_price.toFixed(4)}
                    </td>
                    <td className="p-3 text-right font-mono text-gray-300">
                      {p.current_price != null
                        ? `$${p.current_price.toFixed(4)}`
                        : "-"}
                    </td>
                    <td
                      className={`p-3 text-right font-mono font-semibold ${
                        p.pnl_usd != null
                          ? p.pnl_usd >= 0
                            ? "text-green-400"
                            : "text-red-400"
                          : "text-gray-500"
                      }`}
                    >
                      {p.pnl_usd != null ? (
                        <>
                          {p.pnl_usd >= 0 ? "+" : ""}${p.pnl_usd.toFixed(2)}
                          {p.pnl_pct != null && (
                            <span className="text-xs ml-1 opacity-70">
                              ({p.pnl_pct.toFixed(1)}%)
                            </span>
                          )}
                        </>
                      ) : (
                        "-"
                      )}
                    </td>
                    <td className="p-3 text-xs">
                      {isExpired ? (
                        <span className="text-orange-400">Awaiting res.</span>
                      ) : p.end_date ? (
                        <span className="text-gray-500 font-mono">
                          {(() => {
                            const d = Math.floor(
                              (new Date(p.end_date).getTime() - Date.now()) / 86400000
                            );
                            const h = Math.floor(
                              ((new Date(p.end_date).getTime() - Date.now()) % 86400000) / 3600000
                            );
                            return d > 0 ? `${d}d ${h}h` : `${h}h`;
                          })()}
                        </span>
                      ) : (
                        <span className="text-gray-600">Open</span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {(!pnl || pnl.live.count === 0) && (
                <tr>
                  <td colSpan={8} className="p-8 text-center text-gray-500">
                    No open positions
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Source Reconciliation */}
        <div className="bg-gray-900 rounded-lg overflow-hidden mb-8">
          <div className="bg-gray-800 px-4 py-2 text-sm font-semibold text-gray-300">
            Source Reconciliation (24h)
          </div>
          <div className="p-4">
            <ReconciliationChart data={analytics?.reconciliation || []} />
          </div>
        </div>

        {/* Strategy Intents */}
        <div className="bg-gray-900 rounded-lg overflow-hidden relative">
          <div className="bg-gray-800 px-4 py-2 flex items-center justify-between">
            <span className="text-sm font-semibold text-gray-300">
              Strategy Intents ({intentTotal})
            </span>
            <div className="flex gap-2">
              {["", "filled", "risk_rejected", "rejected"].map((f) => (
                <button
                  key={f}
                  onClick={() => setIntentFilter(f)}
                  className={`px-2 py-1 text-xs rounded ${
                    intentFilter === f
                      ? "bg-blue-600 text-white"
                      : "bg-gray-700 text-gray-300 hover:bg-gray-600"
                  }`}
                >
                  {f || "all"}
                </button>
              ))}
            </div>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-800/50 text-gray-400">
                <th className="p-3 text-left">Time</th>
                <th className="p-3 text-left">Strategy</th>
                <th className="p-3 text-left">Market</th>
                <th className="p-3 text-center">Bet</th>
                <th className="p-3 text-right">Size</th>
                <th className="p-3 text-right">Entry</th>
                <th className="p-3 text-left">Status</th>
                <th className="p-3 text-left">Countdown</th>
                <th className="p-3 text-left">Reason</th>
              </tr>
            </thead>
            <tbody>
              {intents.map((i) => (
                <tr
                  key={i.id}
                  className="border-t border-gray-800 hover:bg-gray-800/40 cursor-default"
                  onMouseEnter={(e) =>
                    handleRowEnter(i.id, e.currentTarget)
                  }
                  onMouseLeave={handleRowLeave}
                >
                  <td className="p-3 text-xs text-gray-400">
                    {timeAgo(i.captured_at)}
                  </td>
                  <td className="p-3 font-mono text-xs">{i.strategy}</td>
                  <td className="p-3 text-xs max-w-[200px] truncate" title={i.condition_id}>
                    {i.event_slug ? (
                      <a
                        href={`https://polymarket.com/event/${i.event_slug}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-400 hover:text-blue-300 hover:underline"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {i.question || i.condition_id.slice(0, 10) + "..."}
                      </a>
                    ) : (
                      i.question || i.condition_id.slice(0, 10) + "..."
                    )}
                  </td>
                  <td className="p-3 text-center">
                    <span
                      className={`inline-block px-1.5 py-0.5 text-xs rounded font-semibold ${
                        i.outcome === "YES"
                          ? "bg-green-900/50 text-green-400"
                          : "bg-red-900/50 text-red-400"
                      }`}
                    >
                      {i.outcome}
                    </span>
                    {i.side === "SELL" && (
                      <span className="text-[10px] text-gray-500 ml-1">EXIT</span>
                    )}
                  </td>
                  <td className="p-3 text-right font-mono">
                    ${i.size_usd.toFixed(2)}
                  </td>
                  <td className="p-3 text-right font-mono">
                    {i.filled_price != null
                      ? `$${i.filled_price.toFixed(4)}`
                      : i.max_price != null
                        ? `\u2264$${i.max_price.toFixed(4)}`
                        : "-"}
                  </td>
                  <td className="p-3">
                    <span className={dispositionColor(i.disposition)}>
                      {i.disposition}
                    </span>
                  </td>
                  <td className="p-3">
                    <CountdownBadge
                      endDate={i.end_date}
                      resolvedAt={i.resolved_at}
                      winner={i.winner_outcome}
                    />
                  </td>
                  <td className="p-3 text-xs text-gray-400 max-w-xs truncate">
                    {i.rejection_reason || i.reason}
                  </td>
                </tr>
              ))}
              {intents.length === 0 && (
                <tr>
                  <td colSpan={9} className="p-8 text-center text-gray-500">
                    No intents captured yet
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Hover detail panel */}
        {hoveredIntentId != null && (
          <IntentDetailPanel
            intentId={hoveredIntentId}
            anchorRect={hoverRect}
          />
        )}
      </div>
    </main>
  );
}
