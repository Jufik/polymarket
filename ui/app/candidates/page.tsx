"use client";

import { useCallback, useEffect, useState } from "react";
import {
  fetchIntrospect,
  fetchCandidates,
  IntrospectServer,
  Candidate,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function ProviderCard({
  p,
}: {
  p: Record<string, unknown>;
}) {
  const name = String(p.name ?? "");
  const tag = String(p.tag ?? name);
  const poolSize = Number(p.pool_size ?? 0);
  const k = Number(p.k ?? 0);
  const tagMarkets = Number(p.tag_markets ?? 0);
  const initialized = Boolean(p.initialized);

  return (
    <div className="bg-gray-900 rounded-lg p-3 border border-gray-800">
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm font-semibold text-gray-200">{tag}</span>
        <span className="text-xs font-mono text-gray-500">{name}</span>
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs font-mono">
        <div>
          <span className="text-gray-500">pool </span>
          <span className="text-cyan-400">{poolSize}</span>
          {k > 0 && <span className="text-gray-600">/{k}</span>}
        </div>
        <div>
          <span className="text-gray-500">markets </span>
          <span className="text-gray-200">
            {tagMarkets.toLocaleString()}
          </span>
        </div>
        <div>
          <span
            className={`inline-block px-1.5 py-0.5 rounded ${
              initialized
                ? "bg-green-900/40 text-green-400"
                : "bg-red-900/40 text-red-400"
            }`}
          >
            {initialized ? "ready" : "loading"}
          </span>
        </div>
      </div>
    </div>
  );
}

function StrategyBadge({
  s,
}: {
  s: Record<string, unknown>;
}) {
  const name = String(s.name ?? "");
  const dirFilter = s.direction_filter as string | null;
  const nThreshold = Number(s.n_threshold ?? 0);
  const marketCount = Number(s.market_state_count ?? 0);
  const signaledCount = Number(s.signaled_count ?? 0);
  const sizeUsd = Number(s.size_usd ?? 0);

  return (
    <div className="bg-gray-900 rounded-lg p-3 border border-gray-800">
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm font-semibold text-gray-200">{name}</span>
        <span
          className={`text-xs font-mono px-1.5 py-0.5 rounded ${
            dirFilter === "YES"
              ? "bg-green-900/40 text-green-400"
              : dirFilter === "NO"
              ? "bg-red-900/40 text-red-400"
              : "bg-gray-800 text-gray-400"
          }`}
        >
          {dirFilter ?? "ANY"}{nThreshold > 0 ? ` N=${nThreshold}` : ""}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs font-mono">
        <div>
          <span className="text-gray-500">tracking </span>
          <span className="text-gray-200">{marketCount}</span>
        </div>
        <div>
          <span className="text-gray-500">signaled </span>
          <span className="text-yellow-400">{signaledCount}</span>
        </div>
        <div>
          <span className="text-gray-500">${sizeUsd}</span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Candidate Row
// ---------------------------------------------------------------------------

function distanceColor(d: number) {
  if (d === 0) return "text-green-400 bg-green-900/40";
  if (d === 1) return "text-yellow-400 bg-yellow-900/40";
  return "text-gray-500 bg-gray-800";
}

function CandidateRow({ c }: { c: Candidate }) {
  const [expanded, setExpanded] = useState(false);
  const traderCount = Object.keys(c.traders).length;

  return (
    <>
      <tr
        className="border-b border-gray-800/50 hover:bg-gray-800/30 cursor-pointer transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {/* Strategy */}
        <td className="py-2 px-3 text-xs text-gray-200">{c.strategy}</td>

        {/* Question */}
        <td className="py-2 px-3 text-xs text-gray-300 max-w-md">
          <div className="truncate" title={c.question ?? c.condition_id}>
            {c.question ?? (
              <span className="font-mono text-gray-600">
                {c.condition_id.slice(0, 16)}...
              </span>
            )}
          </div>
        </td>

        {/* Progress */}
        <td className="py-2 px-3 text-center">
          <span className="font-mono text-sm font-semibold text-gray-200">
            {c.n_traders}
          </span>
          <span className="text-gray-600 font-mono text-xs">
            /{c.n_threshold}
          </span>
        </td>

        {/* Distance */}
        <td className="py-2 px-3 text-center">
          <span
            className={`inline-block px-2 py-0.5 text-xs rounded font-mono font-semibold ${distanceColor(
              c.distance
            )}`}
          >
            {c.distance === 0 ? "READY" : `-${c.distance}`}
          </span>
        </td>

        {/* Direction */}
        <td className="py-2 px-3 text-center">
          <span
            className={`inline-block px-1.5 py-0.5 text-xs rounded font-medium ${
              c.consensus_direction === "YES"
                ? "bg-green-900/30 text-green-400"
                : "bg-red-900/30 text-red-400"
            }`}
          >
            {c.consensus_direction}
          </span>
        </td>

        {/* Filter match */}
        <td className="py-2 px-3 text-center">
          {c.would_pass_filter ? (
            <span className="text-green-400 text-xs">pass</span>
          ) : (
            <span className="text-gray-600 text-xs">miss</span>
          )}
        </td>

        {/* Signaled */}
        <td className="py-2 px-3 text-center">
          {c.signaled ? (
            <span className="text-yellow-400 text-xs font-semibold">fired</span>
          ) : (
            <span className="text-gray-600 text-xs">-</span>
          )}
        </td>

        {/* Volume */}
        <td className="py-2 px-3 text-right font-mono text-xs text-gray-400">
          <span className="text-green-400/80">
            ${c.yes_usd.toFixed(0)}
          </span>
          {" / "}
          <span className="text-red-400/80">${c.no_usd.toFixed(0)}</span>
        </td>
      </tr>

      {/* Expanded: show traders */}
      {expanded && (
        <tr className="border-b border-gray-800/50">
          <td colSpan={8} className="py-2 px-6 bg-gray-900/50">
            <div className="text-xs text-gray-500 mb-1">
              {traderCount} pool trader{traderCount !== 1 ? "s" : ""} &middot;{" "}
              <span className="font-mono text-gray-600">
                {c.condition_id}
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(c.traders).map(([addr, dir]) => (
                <span
                  key={addr}
                  className={`font-mono text-xs px-2 py-0.5 rounded ${
                    dir === "YES"
                      ? "bg-green-900/30 text-green-400"
                      : "bg-red-900/30 text-red-400"
                  }`}
                >
                  {addr.slice(0, 10)}... {String(dir)}
                </span>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

type StratFilter = string | null;
type SortKey = "distance" | "strategy" | "n_traders";

export default function CandidatesPage() {
  const [servers, setServers] = useState<IntrospectServer[]>([]);
  const [selectedConfig, setSelectedConfig] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [stratFilter, setStratFilter] = useState<StratFilter>(null);
  const [sortKey, setSortKey] = useState<SortKey>("distance");
  const [showAll, setShowAll] = useState(false);
  const [lastRefresh, setLastRefresh] = useState("");

  // Discover available introspect servers
  useEffect(() => {
    fetchIntrospect()
      .then((r) => {
        setServers(r.servers);
        // Auto-select portfolio_v3 if up, else first "up" server
        const preferred = r.servers.find((s) => s.status === "up" && s.config === "portfolio_v3");
        const up = preferred ?? r.servers.find((s) => s.status === "up");
        if (up && !selectedConfig) setSelectedConfig(up.config);
      })
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch candidates when config changes
  const refresh = useCallback(async () => {
    if (!selectedConfig) return;
    setLoading(true);
    try {
      const data = await fetchCandidates(selectedConfig);
      setCandidates(data.candidates);
    } catch {
      setCandidates([]);
    }
    setLoading(false);
    setLastRefresh(new Date().toLocaleTimeString());
  }, [selectedConfig]);

  useEffect(() => {
    refresh();
    const iv = setInterval(refresh, 15_000);
    return () => clearInterval(iv);
  }, [refresh]);

  // Current server data
  const server = servers.find((s) => s.config === selectedConfig);
  const serverData = server?.data;
  const providers: Record<string, unknown>[] = serverData
    ? (serverData.providers as unknown as Record<string, unknown>[]) ?? []
    : [];
  const strategies: Record<string, unknown>[] = serverData
    ? (serverData.strategies as unknown as Record<string, unknown>[]) ?? []
    : [];

  // Filter + sort candidates
  const stratNames = [...new Set(candidates.map((c) => c.strategy))].sort();
  let filtered = candidates;
  if (stratFilter) filtered = filtered.filter((c) => c.strategy === stratFilter);
  if (!showAll) filtered = filtered.filter((c) => c.would_pass_filter || c.distance === 0);

  filtered = [...filtered].sort((a, b) => {
    if (sortKey === "distance") return a.distance - b.distance || b.n_traders - a.n_traders;
    if (sortKey === "n_traders") return b.n_traders - a.n_traders || a.distance - b.distance;
    return a.strategy.localeCompare(b.strategy) || a.distance - b.distance;
  });

  const atThreshold = candidates.filter((c) => c.distance === 0 && c.would_pass_filter);
  const oneAway = candidates.filter((c) => c.distance === 1 && c.would_pass_filter);

  return (
    <main className="min-h-screen bg-gray-950 text-gray-100 p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold">Live Candidates</h1>
          <span className="text-xs text-gray-600">
            {lastRefresh && <>Last refresh: {lastRefresh} &middot; </>}auto 15s
          </span>
        </div>

        {/* Config selector */}
        <div className="flex gap-2">
          {servers.map((s) => (
            <button
              key={s.config}
              onClick={() => setSelectedConfig(s.config)}
              className={`px-4 py-2 text-sm rounded-lg transition-colors ${
                selectedConfig === s.config
                  ? "bg-blue-600 text-white"
                  : s.status === "up"
                  ? "bg-gray-800 text-gray-300 hover:bg-gray-700"
                  : "bg-gray-900 text-gray-600 cursor-not-allowed"
              }`}
              disabled={s.status === "down"}
            >
              {s.config}
              <span
                className={`ml-2 inline-block w-2 h-2 rounded-full ${
                  s.status === "up" ? "bg-green-400" : "bg-red-400"
                }`}
              />
            </button>
          ))}
        </div>

        {/* Provider + Strategy cards */}
        {server?.data && (
          <div className="space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {providers.map((p, i) => (
                <ProviderCard key={String(p.name ?? i)} p={p} />
              ))}
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              {strategies.map((s, i) => (
                <StrategyBadge key={String(s.name ?? i)} s={s} />
              ))}
            </div>
          </div>
        )}

        {/* Summary strip */}
        <div className="flex items-center gap-4 text-sm">
          <div className="bg-gray-900 rounded-lg px-4 py-2">
            <span className="text-gray-500">Total </span>
            <span className="font-mono text-gray-200 font-semibold">
              {candidates.length}
            </span>
          </div>
          <div className="bg-green-900/30 rounded-lg px-4 py-2">
            <span className="text-green-500">At threshold </span>
            <span className="font-mono text-green-400 font-semibold">
              {atThreshold.length}
            </span>
          </div>
          <div className="bg-yellow-900/30 rounded-lg px-4 py-2">
            <span className="text-yellow-500">1 away </span>
            <span className="font-mono text-yellow-400 font-semibold">
              {oneAway.length}
            </span>
          </div>
          <div className="flex-1" />
          <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer">
            <input
              type="checkbox"
              checked={showAll}
              onChange={(e) => setShowAll(e.target.checked)}
              className="rounded bg-gray-800 border-gray-700"
            />
            Show direction mismatches
          </label>
        </div>

        {/* Strategy filter tabs */}
        <div className="flex gap-2">
          <button
            onClick={() => setStratFilter(null)}
            className={`px-3 py-1 text-xs rounded ${
              !stratFilter
                ? "bg-blue-600 text-white"
                : "bg-gray-800 text-gray-400 hover:bg-gray-700"
            }`}
          >
            All
          </button>
          {stratNames.map((s) => (
            <button
              key={s}
              onClick={() => setStratFilter(s)}
              className={`px-3 py-1 text-xs rounded ${
                stratFilter === s
                  ? "bg-blue-600 text-white"
                  : "bg-gray-800 text-gray-400 hover:bg-gray-700"
              }`}
            >
              {s}
            </button>
          ))}
        </div>

        {/* Candidates table */}
        <div className="bg-gray-900 rounded-lg p-4">
          {loading && candidates.length === 0 ? (
            <div className="text-center text-gray-600 py-8">Loading...</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-gray-400 text-left border-b border-gray-800">
                    <th
                      className="py-2 px-3 font-medium cursor-pointer hover:text-gray-200"
                      onClick={() => setSortKey("strategy")}
                    >
                      Strategy {sortKey === "strategy" && "^"}
                    </th>
                    <th className="py-2 px-3 font-medium">Market</th>
                    <th
                      className="py-2 px-3 font-medium text-center cursor-pointer hover:text-gray-200"
                      onClick={() => setSortKey("n_traders")}
                    >
                      Progress {sortKey === "n_traders" && "v"}
                    </th>
                    <th
                      className="py-2 px-3 font-medium text-center cursor-pointer hover:text-gray-200"
                      onClick={() => setSortKey("distance")}
                    >
                      Distance {sortKey === "distance" && "^"}
                    </th>
                    <th className="py-2 px-3 font-medium text-center">
                      Direction
                    </th>
                    <th className="py-2 px-3 font-medium text-center">
                      Filter
                    </th>
                    <th className="py-2 px-3 font-medium text-center">
                      Signaled
                    </th>
                    <th className="py-2 px-3 font-medium text-right">
                      YES$ / NO$
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((c, i) => (
                    <CandidateRow key={`${c.strategy}-${c.condition_id}-${i}`} c={c} />
                  ))}
                  {filtered.length === 0 && (
                    <tr>
                      <td colSpan={8} className="py-8 text-center text-gray-600">
                        No candidates matching filters
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
