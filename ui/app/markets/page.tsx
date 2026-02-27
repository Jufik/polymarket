"use client";

import { useEffect, useState } from "react";
import {
  fetchWillNoMarkets,
  fetchPoolTraders,
  WillNoMarket,
  PoolTrader,
} from "@/lib/api";

type Tab = "will_no" | "pool";

function formatCountdown(endDate: string): string {
  const diff = new Date(endDate).getTime() - Date.now();
  if (diff <= 0) return "Ended";
  const d = Math.floor(diff / 86400000);
  const h = Math.floor((diff % 86400000) / 3600000);
  if (d > 0) return `${d}d ${h}h`;
  return `${h}h`;
}

function WillNoTab() {
  const [markets, setMarkets] = useState<WillNoMarket[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchWillNoMarkets()
      .then((d) => setMarkets(d.markets))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="text-gray-400 text-sm animate-pulse p-4">Loading markets...</div>;
  }

  if (markets.length === 0) {
    return <div className="text-gray-500 text-sm p-4">No eligible Will-NO markets found.</div>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-400 border-b border-gray-800">
            <th className="py-2 px-3 font-medium">Question</th>
            <th className="py-2 px-3 font-medium">Niche Keywords</th>
            <th className="py-2 px-3 font-medium">Ends</th>
            <th className="py-2 px-3 font-medium">Link</th>
          </tr>
        </thead>
        <tbody>
          {markets.map((m) => (
            <tr
              key={m.condition_id}
              className="border-b border-gray-800/50 hover:bg-gray-800/30"
            >
              <td className="py-2 px-3 text-gray-200 max-w-md truncate">
                {m.question}
              </td>
              <td className="py-2 px-3">
                <div className="flex flex-wrap gap-1">
                  {m.keywords_matched.map((kw) => (
                    <span
                      key={kw}
                      className="inline-block px-1.5 py-0.5 text-xs rounded bg-blue-900/50 text-blue-300"
                    >
                      {kw}
                    </span>
                  ))}
                </div>
              </td>
              <td className="py-2 px-3 text-gray-400 text-xs font-mono whitespace-nowrap">
                {m.end_date ? formatCountdown(m.end_date) : "-"}
              </td>
              <td className="py-2 px-3">
                {m.polymarket_url ? (
                  <a
                    href={m.polymarket_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-400 hover:text-blue-300 text-xs"
                  >
                    View
                  </a>
                ) : (
                  <span className="text-gray-600 text-xs">-</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PoolTab() {
  const [traders, setTraders] = useState<PoolTrader[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchPoolTraders()
      .then((d) => setTraders(d.traders))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="text-gray-400 text-sm animate-pulse p-4">Loading pool...</div>;
  }

  if (traders.length === 0) {
    return (
      <div className="text-gray-500 text-sm p-4">
        No pool traders found. Pool is populated after the first provider refresh.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-400 border-b border-gray-800">
            <th className="py-2 px-3 font-medium">Strategy</th>
            <th className="py-2 px-3 font-medium">Trader</th>
            <th className="py-2 px-3 font-medium text-right">Trades (30d)</th>
            <th className="py-2 px-3 font-medium text-right">Markets</th>
            <th className="py-2 px-3 font-medium text-right">Volume</th>
          </tr>
        </thead>
        <tbody>
          {traders.map((t) => (
            <tr
              key={`${t.strategy}-${t.trader_address}`}
              className="border-b border-gray-800/50 hover:bg-gray-800/30"
            >
              <td className="py-2 px-3">
                <span className="inline-block px-1.5 py-0.5 text-xs rounded bg-purple-900/50 text-purple-300">
                  {t.strategy}
                </span>
              </td>
              <td className="py-2 px-3 font-mono text-gray-300 text-xs">
                {t.trader_address.slice(0, 8)}...{t.trader_address.slice(-6)}
              </td>
              <td className="py-2 px-3 text-right font-mono text-gray-200">
                {t.n_trades.toLocaleString()}
              </td>
              <td className="py-2 px-3 text-right font-mono text-gray-200">
                {t.n_markets.toLocaleString()}
              </td>
              <td className="py-2 px-3 text-right font-mono text-gray-200">
                ${t.volume_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function MarketsPage() {
  const [tab, setTab] = useState<Tab>("will_no");

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <div className="max-w-6xl mx-auto p-6">
        <h1 className="text-xl font-semibold mb-4">Markets</h1>

        {/* Tab bar */}
        <div className="flex gap-1 mb-4 border-b border-gray-800">
          {(
            [
              ["will_no", "Will-NO Markets"],
              ["pool", "HR Pool Traders"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                tab === key
                  ? "border-blue-500 text-blue-400"
                  : "border-transparent text-gray-500 hover:text-gray-300"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        {tab === "will_no" && <WillNoTab />}
        {tab === "pool" && <PoolTab />}
      </div>
    </div>
  );
}
