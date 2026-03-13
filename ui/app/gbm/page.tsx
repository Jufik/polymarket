"use client";

import { useCallback, useEffect, useState } from "react";

const API_BASE =
  typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8001`
    : "http://localhost:8001";

interface GBMState {
  windows: number;
  s0_tracked: number;
  spot_price: string;
  sigma: string;
  signaled: number;
  positions: number;
  orderbooks: number;
  timestamp: string;
}

interface PaperStats {
  trades_processed: number;
  trades_per_sec: number;
  unique_markets: number;
  intents_submitted: number;
  open_positions: number;
  total_cost_basis: number;
  total_realized_pnl: number;
  trailing_stops: number;
  timestamp: string;
}

interface Entry {
  side: string;
  cid: string;
  gbm: string;
  pm: string;
  lag: string;
  minute: string;
  timestamp: string;
}

interface Exit {
  type: string;
  cid: string;
  outcome: string;
  gbm_ours: string;
  pm_ours: string;
  hold_s: string;
  entry: string;
  timestamp: string;
}

interface Eval {
  cid: string;
  gbm: string;
  pm: string;
  lag: string;
  elapsed: string;
  remaining: string;
  spread: string;
  timestamp: string;
}

interface S0Capture {
  cid: string;
  s0: string;
  timestamp: string;
}

interface Intent {
  strategy: string;
  condition_id: string;
  side: string;
  outcome: string;
  size_usd: number;
  max_price: number | null;
  reason: string;
  signal_time: number;
}

interface Fill {
  strategy: string;
  condition_id: string;
  side: string;
  outcome: string;
  filled_price: number;
  filled_size_usd: number;
  fee_usd: number;
  status: string;
  filled_at: number;
}

interface GBMData {
  state: GBMState;
  stats: PaperStats;
  entries: Entry[];
  exits: Exit[];
  evals: Eval[];
  s0_captures: S0Capture[];
  pm_misses: number;
  intents: Intent[];
  fills: Fill[];
}

function ago(ts: string): string {
  if (!ts) return "";
  const d = new Date(ts);
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function fmtTime(ts: string | number): string {
  const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  return d.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function parseReason(reason: string) {
  const parts: Record<string, string> = {};
  reason.replace(/(\w+)=([\S]+)/g, (_, k, v) => {
    parts[k] = v;
    return "";
  });
  return parts;
}

function Stat({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: string | number;
  sub?: string;
  color?: string;
}) {
  return (
    <div className="flex justify-between py-1.5 border-b border-gray-800/50">
      <span className="text-xs text-gray-500">{label}</span>
      <span className={`text-sm font-mono ${color || "text-gray-200"}`}>
        {value}
        {sub && <span className="text-gray-500 text-xs ml-1">({sub})</span>}
      </span>
    </div>
  );
}

function Card({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
        {title}
      </h3>
      {children}
    </div>
  );
}

export default function GBMPage() {
  const [data, setData] = useState<GBMData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/gbm/state`, {
        cache: "no-store",
      });
      const d = await res.json();
      if (d.error) {
        setError(d.error);
      } else {
        setData(d);
        setError(null);
      }
      setLastRefresh(Date.now());
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  if (error) {
    return (
      <div className="min-h-screen bg-black text-red-400 p-8">
        <h1 className="text-lg font-bold mb-2">GBM Monitor</h1>
        <p>{error}</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="min-h-screen bg-black text-gray-400 p-8">
        <p>Loading...</p>
      </div>
    );
  }

  const { state, stats, entries, exits, evals, s0_captures, intents, fills } =
    data;
  const sigma = parseFloat(state.sigma || "0");
  const btcPrice = parseFloat(state.spot_price || "0");

  return (
    <div className="min-h-screen bg-black text-gray-200 p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-bold text-gray-100">
              Crypto GBM Monitor
            </h1>
            <p className="text-xs text-gray-500 mt-1">
              Auto-refresh 5s &middot; Last:{" "}
              {lastRefresh ? fmtTime(new Date(lastRefresh).toISOString()) : "—"}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <div
              className={`w-2 h-2 rounded-full ${
                state.timestamp ? "bg-green-400 animate-pulse" : "bg-red-500"
              }`}
            />
            <span className="text-xs text-gray-500">
              {state.timestamp ? ago(state.timestamp) : "offline"}
            </span>
          </div>
        </div>

        {/* Top row: State + Stats + BTC */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
          <Card title="Strategy State">
            <Stat label="BTC Price" value={`$${btcPrice.toLocaleString()}`} />
            <Stat label="Sigma (1m)" value={sigma.toFixed(6)} />
            <Stat label="Active Windows" value={state.windows} />
            <Stat label="S0 Tracked" value={state.s0_tracked} />
            <Stat label="Signaled" value={state.signaled} />
            <Stat
              label="Open Positions"
              value={state.positions}
              color={state.positions > 0 ? "text-yellow-400" : "text-gray-200"}
            />
            <Stat label="Orderbooks" value={state.orderbooks.toLocaleString()} />
          </Card>

          <Card title="Paper Stats">
            <Stat
              label="Trades/sec"
              value={stats.trades_per_sec?.toFixed(1) || "0"}
            />
            <Stat
              label="Trades Processed"
              value={(stats.trades_processed || 0).toLocaleString()}
            />
            <Stat label="Unique Markets" value={stats.unique_markets || 0} />
            <Stat label="Intents Submitted" value={stats.intents_submitted || 0} />
            <Stat
              label="Cost Basis"
              value={`$${(stats.total_cost_basis || 0).toFixed(2)}`}
            />
            <Stat
              label="Realized PnL"
              value={`$${(stats.total_realized_pnl || 0).toFixed(2)}`}
              color={
                (stats.total_realized_pnl || 0) > 0
                  ? "text-green-400"
                  : (stats.total_realized_pnl || 0) < 0
                    ? "text-red-400"
                    : "text-gray-200"
              }
            />
            <Stat label="Trailing Stops" value={stats.trailing_stops || 0} />
          </Card>

          <Card title="Signal Quality">
            <Stat label="PM Price Misses" value={data.pm_misses} />
            <Stat label="Total Entries" value={entries.length} />
            <Stat label="Total Exits" value={exits.length} />
            <Stat
              label="Exit Types"
              value={
                exits.length > 0
                  ? [
                      ...new Set(exits.map((e) => e.type)),
                    ].join(", ")
                  : "—"
              }
            />
            {intents.length > 0 && (
              <>
                <Stat
                  label="Avg Bet Size"
                  value={`$${(intents.reduce((s, i) => s + i.size_usd, 0) / intents.length).toFixed(0)}`}
                />
                <Stat
                  label="BUY YES / NO"
                  value={`${intents.filter((i) => i.outcome === "YES").length} / ${intents.filter((i) => i.outcome === "NO").length}`}
                />
              </>
            )}
          </Card>
        </div>

        {/* S0 captures */}
        {s0_captures.length > 0 && (
          <div className="mb-4">
            <Card title="Recent S0 Captures">
              <div className="space-y-1">
                {s0_captures.map((s, i) => (
                  <div
                    key={i}
                    className="flex justify-between text-xs font-mono"
                  >
                    <span className="text-gray-500">{fmtTime(s.timestamp)}</span>
                    <span className="text-gray-400">{s.cid}</span>
                    <span className="text-gray-200">
                      S0=${parseFloat(s.s0).toLocaleString()}
                    </span>
                  </div>
                ))}
              </div>
            </Card>
          </div>
        )}

        {/* Evaluations */}
        {evals.length > 0 && (
          <div className="mb-4">
            <Card title="Recent Evaluations">
              <div className="overflow-x-auto">
                <table className="w-full text-xs font-mono">
                  <thead>
                    <tr className="text-gray-500 border-b border-gray-800">
                      <th className="text-left py-1 pr-3">Time</th>
                      <th className="text-left py-1 pr-3">CID</th>
                      <th className="text-right py-1 pr-3">GBM</th>
                      <th className="text-right py-1 pr-3">PM</th>
                      <th className="text-right py-1 pr-3">Lag</th>
                      <th className="text-right py-1 pr-3">Elapsed</th>
                      <th className="text-right py-1 pr-3">Remaining</th>
                      <th className="text-right py-1">Spread</th>
                    </tr>
                  </thead>
                  <tbody>
                    {evals.map((ev, i) => {
                      const lag = parseFloat(ev.lag);
                      return (
                        <tr
                          key={i}
                          className="border-b border-gray-800/30 hover:bg-gray-800/30"
                        >
                          <td className="py-1 pr-3 text-gray-500">
                            {fmtTime(ev.timestamp)}
                          </td>
                          <td className="py-1 pr-3 text-gray-400">{ev.cid}</td>
                          <td className="py-1 pr-3 text-right text-gray-200">
                            {ev.gbm}
                          </td>
                          <td className="py-1 pr-3 text-right text-gray-200">
                            {ev.pm}
                          </td>
                          <td
                            className={`py-1 pr-3 text-right font-semibold ${
                              Math.abs(lag) >= 0.1
                                ? "text-yellow-400"
                                : "text-gray-400"
                            }`}
                          >
                            {ev.lag}
                          </td>
                          <td className="py-1 pr-3 text-right text-gray-400">
                            {ev.elapsed}m
                          </td>
                          <td className="py-1 pr-3 text-right text-gray-400">
                            {ev.remaining}m
                          </td>
                          <td className="py-1 text-right text-gray-500">
                            {ev.spread}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        )}

        {/* Entries & Exits side by side */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          <Card title={`Entries (${entries.length})`}>
            {entries.length === 0 ? (
              <p className="text-xs text-gray-600">No entries yet</p>
            ) : (
              <div className="space-y-2">
                {entries
                  .slice()
                  .reverse()
                  .map((e, i) => (
                    <div
                      key={i}
                      className="bg-gray-800/40 rounded p-2 text-xs font-mono"
                    >
                      <div className="flex justify-between mb-1">
                        <span
                          className={
                            e.side.includes("YES")
                              ? "text-green-400"
                              : "text-red-400"
                          }
                        >
                          {e.side}
                        </span>
                        <span className="text-gray-500">
                          {fmtTime(e.timestamp)}
                        </span>
                      </div>
                      <div className="flex gap-3 text-gray-400">
                        <span>
                          GBM={e.gbm} PM={e.pm}
                        </span>
                        <span className="text-yellow-400">lag={e.lag}</span>
                        <span>min={e.minute}</span>
                      </div>
                      <div className="text-gray-600 truncate">{e.cid}</div>
                    </div>
                  ))}
              </div>
            )}
          </Card>

          <Card title={`Exits (${exits.length})`}>
            {exits.length === 0 ? (
              <p className="text-xs text-gray-600">No exits yet</p>
            ) : (
              <div className="space-y-2">
                {exits
                  .slice()
                  .reverse()
                  .map((e, i) => {
                    const typeColor =
                      e.type === "exit_trailing"
                        ? "text-yellow-400"
                        : e.type === "exit_gbm_flip"
                          ? "text-red-400"
                          : e.type === "exit_time_stop"
                            ? "text-orange-400"
                            : "text-green-400";
                    return (
                      <div
                        key={i}
                        className="bg-gray-800/40 rounded p-2 text-xs font-mono"
                      >
                        <div className="flex justify-between mb-1">
                          <span className={typeColor}>{e.type}</span>
                          <span className="text-gray-500">
                            {fmtTime(e.timestamp)}
                          </span>
                        </div>
                        <div className="flex gap-3 text-gray-400">
                          <span>{e.outcome}</span>
                          <span>entry={e.entry}</span>
                          <span>gbm={e.gbm_ours}</span>
                          <span>hold={e.hold_s}s</span>
                        </div>
                        <div className="text-gray-600 truncate">{e.cid}</div>
                      </div>
                    );
                  })}
              </div>
            )}
          </Card>
        </div>

        {/* Intents & Fills */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Card title={`Intents (${intents.length})`}>
            {intents.length === 0 ? (
              <p className="text-xs text-gray-600">
                No intents since last wipe
              </p>
            ) : (
              <div className="space-y-2">
                {intents
                  .slice()
                  .reverse()
                  .map((intent, i) => {
                    const r = parseReason(intent.reason);
                    return (
                      <div
                        key={i}
                        className="bg-gray-800/40 rounded p-2 text-xs font-mono"
                      >
                        <div className="flex justify-between mb-1">
                          <span>
                            <span
                              className={
                                intent.side === "BUY"
                                  ? "text-green-400"
                                  : "text-red-400"
                              }
                            >
                              {intent.side}
                            </span>{" "}
                            <span
                              className={
                                intent.outcome === "YES"
                                  ? "text-green-400"
                                  : "text-red-400"
                              }
                            >
                              {intent.outcome}
                            </span>
                          </span>
                          <span className="text-gray-200">
                            ${intent.size_usd.toFixed(0)}
                            {intent.max_price && (
                              <span className="text-gray-500">
                                {" "}
                                @{intent.max_price.toFixed(3)}
                              </span>
                            )}
                          </span>
                        </div>
                        <div className="text-gray-500 truncate">
                          {intent.reason}
                        </div>
                        <div className="flex justify-between mt-1">
                          <span className="text-gray-600 truncate">
                            {intent.condition_id.slice(0, 20)}...
                          </span>
                          <span className="text-gray-500">
                            {fmtTime(intent.signal_time)}
                          </span>
                        </div>
                      </div>
                    );
                  })}
              </div>
            )}
          </Card>

          <Card title={`Fills (${fills.length})`}>
            {fills.length === 0 ? (
              <p className="text-xs text-gray-600">
                No fills since last wipe
              </p>
            ) : (
              <div className="space-y-2">
                {fills
                  .slice()
                  .reverse()
                  .map((fill, i) => (
                    <div
                      key={i}
                      className="bg-gray-800/40 rounded p-2 text-xs font-mono"
                    >
                      <div className="flex justify-between mb-1">
                        <span>
                          <span
                            className={
                              fill.side === "BUY"
                                ? "text-green-400"
                                : "text-red-400"
                            }
                          >
                            {fill.side}
                          </span>{" "}
                          <span
                            className={
                              fill.outcome === "YES"
                                ? "text-green-400"
                                : "text-red-400"
                            }
                          >
                            {fill.outcome}
                          </span>
                        </span>
                        <span
                          className={
                            fill.status === "filled"
                              ? "text-green-400"
                              : "text-red-400"
                          }
                        >
                          {fill.status}
                        </span>
                      </div>
                      <div className="flex gap-3 text-gray-400">
                        <span>
                          ${fill.filled_size_usd.toFixed(2)} @
                          {fill.filled_price.toFixed(3)}
                        </span>
                        <span className="text-gray-500">
                          fee=${fill.fee_usd.toFixed(2)}
                        </span>
                      </div>
                      <div className="flex justify-between mt-1">
                        <span className="text-gray-600 truncate">
                          {fill.condition_id.slice(0, 20)}...
                        </span>
                        <span className="text-gray-500">
                          {fmtTime(fill.filled_at)}
                        </span>
                      </div>
                    </div>
                  ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
