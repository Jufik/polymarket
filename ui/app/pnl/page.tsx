"use client";

import { useEffect, useState } from "react";
import {
  fetchPnlSummary,
  PnlSummary,
  PnlLivePosition,
  PnlResolvedPosition,
} from "@/lib/api";

function pnlColor(v: number | null) {
  if (v == null) return "text-gray-500";
  return v >= 0 ? "text-green-400" : "text-red-400";
}

function formatPnl(v: number | null) {
  if (v == null) return "-";
  return `${v >= 0 ? "+" : ""}$${v.toFixed(2)}`;
}

function formatPct(v: number | null) {
  if (v == null) return "-";
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

function timeRemaining(endDate: string | null) {
  if (!endDate) return "-";
  const diff = new Date(endDate).getTime() - Date.now();
  if (diff <= 0) return "Awaiting resolution";
  const d = Math.floor(diff / 86400000);
  const h = Math.floor((diff % 86400000) / 3600000);
  return d > 0 ? `${d}d ${h}h` : `${h}h`;
}

function LiveTable({ positions }: { positions: PnlLivePosition[] }) {
  const sorted = [...positions].sort(
    (a, b) => (b.pnl_usd ?? 0) - (a.pnl_usd ?? 0)
  );

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="bg-gray-800/50 text-gray-400">
          <th className="p-3 text-left">Market</th>
          <th className="p-3 text-left">Outcome</th>
          <th className="p-3 text-left">Strategy</th>
          <th className="p-3 text-right">Entry</th>
          <th className="p-3 text-right">Current</th>
          <th className="p-3 text-right">Size</th>
          <th className="p-3 text-right">PnL ($)</th>
          <th className="p-3 text-right">PnL (%)</th>
          <th className="p-3 text-right">Time Left</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((p) => (
          <tr key={p.id} className="border-t border-gray-800">
            <td className="p-3 text-xs max-w-[250px] truncate">
              {p.question || "-"}
            </td>
            <td className="p-3 text-xs">{p.outcome}</td>
            <td className="p-3 text-xs font-mono">{p.strategy}</td>
            <td className="p-3 text-right font-mono">
              ${p.entry_price.toFixed(4)}
            </td>
            <td className="p-3 text-right font-mono">
              {p.current_price != null
                ? `$${p.current_price.toFixed(4)}`
                : "-"}
            </td>
            <td className="p-3 text-right font-mono">
              ${p.size_usd.toFixed(2)}
            </td>
            <td className={`p-3 text-right font-mono ${pnlColor(p.pnl_usd)}`}>
              {formatPnl(p.pnl_usd)}
            </td>
            <td className={`p-3 text-right font-mono ${pnlColor(p.pnl_pct)}`}>
              {formatPct(p.pnl_pct)}
            </td>
            <td className="p-3 text-right text-xs text-gray-400">
              {timeRemaining(p.end_date)}
            </td>
          </tr>
        ))}
        {sorted.length === 0 && (
          <tr>
            <td colSpan={9} className="p-8 text-center text-gray-500">
              No live positions
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

function ResolvedTable({ positions }: { positions: PnlResolvedPosition[] }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="bg-gray-800/50 text-gray-400">
          <th className="p-3 text-left">Market</th>
          <th className="p-3 text-left">Outcome</th>
          <th className="p-3 text-left">Strategy</th>
          <th className="p-3 text-right">Entry</th>
          <th className="p-3 text-right">Size</th>
          <th className="p-3 text-center">Result</th>
          <th className="p-3 text-right">PnL ($)</th>
          <th className="p-3 text-right">PnL (%)</th>
          <th className="p-3 text-right">Resolved</th>
        </tr>
      </thead>
      <tbody>
        {positions.map((p) => (
          <tr key={p.id} className="border-t border-gray-800">
            <td className="p-3 text-xs max-w-[250px] truncate">
              {p.question || "-"}
            </td>
            <td className="p-3 text-xs">{p.outcome}</td>
            <td className="p-3 text-xs font-mono">{p.strategy}</td>
            <td className="p-3 text-right font-mono">
              ${p.entry_price.toFixed(4)}
            </td>
            <td className="p-3 text-right font-mono">
              ${p.size_usd.toFixed(2)}
            </td>
            <td className="p-3 text-center">
              <span
                className={`inline-block px-2 py-0.5 text-xs rounded font-semibold ${
                  p.won
                    ? "bg-green-900/50 text-green-400"
                    : "bg-red-900/50 text-red-400"
                }`}
              >
                {p.won ? "WON" : "LOST"}
              </span>
            </td>
            <td className={`p-3 text-right font-mono ${pnlColor(p.pnl_usd)}`}>
              {formatPnl(p.pnl_usd)}
            </td>
            <td className={`p-3 text-right font-mono ${pnlColor(p.pnl_pct)}`}>
              {formatPct(p.pnl_pct)}
            </td>
            <td className="p-3 text-right text-xs text-gray-400">
              {p.resolved_at
                ? new Date(p.resolved_at).toLocaleDateString()
                : "-"}
            </td>
          </tr>
        ))}
        {positions.length === 0 && (
          <tr>
            <td colSpan={9} className="p-8 text-center text-gray-500">
              No resolved positions
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

export default function PnlPage() {
  const [data, setData] = useState<PnlSummary | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    try {
      const d = await fetchPnlSummary();
      setData(d);
    } catch {
      /* ignore */
    }
    setLoading(false);
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 10000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <main className="min-h-screen bg-gray-950 text-gray-100 p-8">
        <div className="max-w-7xl mx-auto">
          <h1 className="text-3xl font-bold mb-6">PnL</h1>
          <div className="text-gray-500">Loading...</div>
        </div>
      </main>
    );
  }

  if (!data) {
    return (
      <main className="min-h-screen bg-gray-950 text-gray-100 p-8">
        <div className="max-w-7xl mx-auto">
          <h1 className="text-3xl font-bold mb-6">PnL</h1>
          <div className="text-red-400">Failed to load PnL data</div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-gray-950 text-gray-100 p-8">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-3xl font-bold mb-6">PnL</h1>

        {/* Summary cards */}
        <div className="flex gap-4 mb-8">
          <div className="bg-gray-900 rounded-lg p-4 flex-1 border border-gray-800">
            <div className="text-sm text-gray-400">Total PnL</div>
            <div
              className={`text-2xl font-mono font-bold ${
                data.summary.total_pnl_usd >= 0
                  ? "text-green-400"
                  : "text-red-400"
              }`}
            >
              {data.summary.total_pnl_usd >= 0 ? "+" : ""}$
              {data.summary.total_pnl_usd.toFixed(2)}
            </div>
            <div className="text-xs text-gray-500 mt-1">
              {data.summary.n_fills} fills / $
              {data.summary.total_invested.toFixed(0)} invested
            </div>
          </div>
          <div className="bg-gray-900 rounded-lg p-4 flex-1 border border-gray-800">
            <div className="text-sm text-gray-400">Live (Unrealized)</div>
            <div
              className={`text-2xl font-mono font-bold ${
                data.live.pnl_usd >= 0 ? "text-green-400" : "text-red-400"
              }`}
            >
              {data.live.pnl_usd >= 0 ? "+" : ""}${data.live.pnl_usd.toFixed(2)}
            </div>
            <div className="text-xs text-gray-500 mt-1">
              {data.live.count} positions / ${data.live.invested.toFixed(0)}{" "}
              invested
            </div>
          </div>
          <div className="bg-gray-900 rounded-lg p-4 flex-1 border border-gray-800">
            <div className="text-sm text-gray-400">Resolved (Realized)</div>
            <div
              className={`text-2xl font-mono font-bold ${
                data.resolved.pnl_usd >= 0 ? "text-green-400" : "text-red-400"
              }`}
            >
              {data.resolved.pnl_usd >= 0 ? "+" : ""}$
              {data.resolved.pnl_usd.toFixed(2)}
            </div>
            <div className="text-xs text-gray-500 mt-1">
              {data.resolved.wins}W / {data.resolved.losses}L
              {data.summary.win_rate != null &&
                ` (${(data.summary.win_rate * 100).toFixed(0)}%)`}
            </div>
          </div>
        </div>

        {/* Live Positions */}
        <div className="bg-gray-900 rounded-lg overflow-hidden mb-8">
          <div className="bg-gray-800 px-4 py-2 text-sm font-semibold text-gray-300">
            Live Positions ({data.live.count})
          </div>
          <LiveTable positions={data.live.positions} />
        </div>

        {/* Resolved Positions */}
        <div className="bg-gray-900 rounded-lg overflow-hidden mb-8">
          <div className="bg-gray-800 px-4 py-2 text-sm font-semibold text-gray-300">
            Resolved Positions ({data.resolved.count})
          </div>
          <ResolvedTable positions={data.resolved.positions} />
        </div>
      </div>
    </main>
  );
}
