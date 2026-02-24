"use client";

import { useEffect, useState } from "react";
import { fetchPositions, fetchHealth, triggerPanic, Position } from "@/lib/api";

export default function Dashboard() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [totalExposure, setTotalExposure] = useState(0);
  const [health, setHealth] = useState<string>("loading...");
  const [panicResult, setPanicResult] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const data = await fetchPositions();
      setPositions(data.positions);
      setTotalExposure(data.total_exposure);
    } catch {
      setPositions([]);
    }
    try {
      const h = await fetchHealth();
      setHealth(h.status);
    } catch {
      setHealth("unreachable");
    }
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, []);

  const handlePanic = async () => {
    if (!confirm("PANIC: Close ALL positions immediately?")) return;
    const result = await triggerPanic();
    setPanicResult(
      `Closed: ${result.total_closed}, Failed: ${result.total_failed}`
    );
    refresh();
  };

  return (
    <main className="min-h-screen bg-gray-950 text-gray-100 p-8">
      <div className="max-w-6xl mx-auto">
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
            <div className="text-lg font-mono">{positions.length}</div>
          </div>
          <div className="bg-gray-900 rounded-lg p-4 flex-1">
            <div className="text-sm text-gray-400">Total Exposure</div>
            <div className="text-lg font-mono">
              ${totalExposure.toFixed(2)}
            </div>
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

        {/* Positions Table */}
        <div className="bg-gray-900 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-800 text-gray-400">
                <th className="p-3 text-left">Market</th>
                <th className="p-3 text-left">Side</th>
                <th className="p-3 text-right">Size</th>
                <th className="p-3 text-right">Entry</th>
                <th className="p-3 text-right">Last Price</th>
                <th className="p-3 text-right">PnL</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.condition_id} className="border-t border-gray-800">
                  <td className="p-3 font-mono text-xs">
                    {p.condition_id.slice(0, 12)}...
                  </td>
                  <td className="p-3">
                    <span
                      className={
                        p.side === "BUY" ? "text-green-400" : "text-red-400"
                      }
                    >
                      {p.side}
                    </span>
                  </td>
                  <td className="p-3 text-right font-mono">
                    {p.size.toFixed(4)}
                  </td>
                  <td className="p-3 text-right font-mono">
                    ${p.avg_entry.toFixed(4)}
                  </td>
                  <td className="p-3 text-right font-mono">
                    ${p.last_price.toFixed(4)}
                  </td>
                  <td
                    className={`p-3 text-right font-mono ${p.unrealized_pnl >= 0 ? "text-green-400" : "text-red-400"}`}
                  >
                    ${p.unrealized_pnl.toFixed(2)}
                  </td>
                </tr>
              ))}
              {positions.length === 0 && (
                <tr>
                  <td colSpan={6} className="p-8 text-center text-gray-500">
                    No open positions
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </main>
  );
}
