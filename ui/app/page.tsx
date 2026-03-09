"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  fetchHealth,
  fetchMetrics,
  fetchIngestion,
  fetchMetadata,
  fetchActivity,
  fetchIntents,
  fetchFills,
  fetchRoundTrips,
  IngestionData,
  MetadataData,
  Metrics,
  StrategyActivity,
  IntentRecord,
  FillRecord,
  RoundTrip,
  RoundTripSummary,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(ts: number) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function fmtDt(s: string | null) {
  if (!s) return "\u2014";
  try {
    return new Date(s + "Z").toLocaleString("en-GB", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return s;
  }
}

function ago(ts: number) {
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 0) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function num(v: string | number) {
  return Number(v).toLocaleString();
}

// ---------------------------------------------------------------------------
// Metric Card
// ---------------------------------------------------------------------------

function Card({
  value,
  label,
  color,
}: {
  value: string;
  label: string;
  color?: string;
}) {
  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <div className={`text-2xl font-bold font-mono ${color ?? "text-gray-100"}`}>
        {value}
      </div>
      <div className="text-xs text-gray-500 mt-1">{label}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// TPS Mini Chart (pure SVG, no deps)
// ---------------------------------------------------------------------------

function TpsChart({ series }: { series: IngestionData["tps_series"] }) {
  if (series.length === 0)
    return <div className="text-sm text-gray-600 p-4">No TPS data</div>;

  // Group by minute, sum across sources
  const byMin = new Map<string, Map<string, number>>();
  const sources = new Set<string>();
  for (const r of series) {
    sources.add(r.source);
    if (!byMin.has(r.minute)) byMin.set(r.minute, new Map());
    byMin.get(r.minute)!.set(r.source, Number(r.cnt) / 60);
  }
  const minutes = [...byMin.keys()].sort();
  const srcArr = [...sources].sort();
  const colors: Record<string, string> = {
    alchemy: "#a78bfa",
    rtds: "#38bdf8",
    goldsky_subgraph: "#fbbf24",
    pending_block: "#34d399",
  };

  let maxTps = 0;
  for (const m of byMin.values())
    for (const v of m.values()) if (v > maxTps) maxTps = v;
  maxTps = maxTps || 1;

  const W = 700,
    H = 180,
    PL = 50,
    PR = 10,
    PT = 10,
    PB = 28;
  const cw = W - PL - PR,
    ch = H - PT - PB;
  const x = (i: number) => PL + (i / Math.max(minutes.length - 1, 1)) * cw;
  const y = (v: number) => PT + ch - (v / maxTps) * ch;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      {/* grid */}
      {[0, 0.5, 1].map((f) => {
        const yy = y(maxTps * f);
        return (
          <g key={f}>
            <line
              x1={PL}
              x2={W - PR}
              y1={yy}
              y2={yy}
              stroke="#1e293b"
              strokeWidth="0.5"
            />
            <text
              x={PL - 6}
              y={yy + 3}
              textAnchor="end"
              fill="#64748b"
              fontSize="9"
            >
              {(maxTps * f).toFixed(0)}
            </text>
          </g>
        );
      })}
      {/* lines */}
      {srcArr.map((src) => {
        const pts = minutes
          .map((m, i) => `${x(i)},${y(byMin.get(m)?.get(src) ?? 0)}`)
          .join(" ");
        return (
          <polyline
            key={src}
            points={pts}
            fill="none"
            stroke={colors[src] || "#6b7280"}
            strokeWidth="1.5"
            strokeLinejoin="round"
          />
        );
      })}
      {/* x labels */}
      {minutes.map((m, i) => {
        if (minutes.length > 30 && i % 5 !== 0) return null;
        if (minutes.length > 15 && minutes.length <= 30 && i % 3 !== 0) return null;
        return (
          <text
            key={m}
            x={x(i)}
            y={H - 4}
            textAnchor="middle"
            fill="#64748b"
            fontSize="9"
          >
            {m.slice(11, 16)}
          </text>
        );
      })}
      {/* legend */}
      {srcArr.map((src, i) => (
        <g key={src} transform={`translate(${PL + i * 110}, ${H - 16})`}>
          <rect width="10" height="3" rx="1" fill={colors[src] || "#6b7280"} />
          <text x="14" y="3" fill="#94a3b8" fontSize="9">
            {src}
          </text>
        </g>
      ))}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Strategy Cards
// ---------------------------------------------------------------------------

function StrategySummary({ data }: { data: Record<string, StrategyActivity> }) {
  const entries = Object.entries(data);
  if (entries.length === 0)
    return <div className="text-sm text-gray-600">No strategy logs</div>;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
      {entries.map(([name, info]) => {
        const fillRate =
          info.total_intents > 0
            ? Math.round((info.total_fills / info.total_intents) * 100)
            : 0;
        return (
          <div
            key={name}
            className="bg-gray-900 rounded-lg p-4 border border-gray-800"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-semibold text-gray-200">
                {name}
                {info.config && info.config !== name && (
                  <span className="text-gray-600 font-normal text-xs ml-1.5">
                    {info.config}
                  </span>
                )}
              </span>
              <span
                className={`text-xs font-mono px-2 py-0.5 rounded ${
                  fillRate > 50
                    ? "bg-green-900/40 text-green-400"
                    : fillRate > 0
                    ? "bg-yellow-900/40 text-yellow-400"
                    : "bg-gray-800 text-gray-500"
                }`}
              >
                {fillRate}% fill
              </span>
            </div>
            <div className="grid grid-cols-3 gap-2 text-xs font-mono">
              <div>
                <div className="text-gray-500">intents</div>
                <div className="text-gray-200">{info.total_intents}</div>
              </div>
              <div>
                <div className="text-gray-500">filled</div>
                <div className="text-green-400">{info.total_fills}</div>
              </div>
              <div>
                <div className="text-gray-500">rejected</div>
                <div className="text-red-400">{info.total_rejected}</div>
              </div>
            </div>
            <div className="mt-2 text-xs text-gray-400">
              Total filled:{" "}
              <span className="font-mono text-gray-200">
                ${info.total_filled_usd.toLocaleString()}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Intent + Fill Table
// ---------------------------------------------------------------------------

interface MergedRow {
  time: number;
  strategy: string;
  source: string;
  side: string;
  outcome: string;
  sizeUsd: number;
  maxPrice: number | null;
  fillPrice: number | null;
  fillStatus: string;
  reason: string;
  conditionId: string;
  intentIndex: number;
}

function IntentTable({
  intents,
  fills,
  filter,
  setFilter,
}: {
  intents: IntentRecord[];
  fills: FillRecord[];
  filter: string | null;
  setFilter: (v: string | null) => void;
}) {
  // Build fill lookup
  const fillMap = new Map<string, FillRecord[]>();
  for (const f of fills) {
    const key = `${f.condition_id}|${f.strategy}`;
    if (!fillMap.has(key)) fillMap.set(key, []);
    fillMap.get(key)!.push(f);
  }

  const usedFills = new Set<string>();
  const rows: MergedRow[] = intents.map((i) => {
    const key = `${i.condition_id}|${i.strategy}`;
    const candidates = fillMap.get(key) || [];
    const fill = candidates.find(
      (f) =>
        !usedFills.has(f.intent_id) &&
        Math.abs((f.filled_at || 0) - (i.signal_time || 0)) < 120
    );
    if (fill) usedFills.add(fill.intent_id);
    return {
      time: i.signal_time,
      strategy: i.strategy,
      source: i._source,
      side: i.side,
      outcome: i.outcome,
      sizeUsd: i.size_usd,
      maxPrice: i.max_price,
      fillPrice: fill ? fill.filled_price : null,
      fillStatus: fill ? fill.status : "pending",
      reason: i.reason || "",
      conditionId: i.condition_id,
      intentIndex: i._index,
    };
  });

  rows.sort((a, b) => b.time - a.time);

  // Build strategy filter list: use leg name (strategy), not config name (source)
  const strategies = [...new Set(rows.map((r) => r.strategy))].sort();

  return (
    <div>
      {/* Filter tabs */}
      <div className="flex gap-2 mb-3">
        <button
          onClick={() => setFilter(null)}
          className={`px-3 py-1 text-xs rounded ${
            !filter
              ? "bg-blue-600 text-white"
              : "bg-gray-800 text-gray-400 hover:bg-gray-700"
          }`}
        >
          All
        </button>
        {strategies.map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`px-3 py-1 text-xs rounded ${
              filter === s
                ? "bg-blue-600 text-white"
                : "bg-gray-800 text-gray-400 hover:bg-gray-700"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-gray-400 text-left border-b border-gray-800">
              <th className="py-2 px-3 font-medium">Time</th>
              <th className="py-2 px-3 font-medium">Strategy</th>
              <th className="py-2 px-3 font-medium text-center">Side</th>
              <th className="py-2 px-3 font-medium text-center">Outcome</th>
              <th className="py-2 px-3 font-medium text-right">Size</th>
              <th className="py-2 px-3 font-medium text-right">Max Price</th>
              <th className="py-2 px-3 font-medium text-right">Fill Price</th>
              <th className="py-2 px-3 font-medium text-center">Status</th>
              <th className="py-2 px-3 font-medium">Reason</th>
            </tr>
          </thead>
          <tbody>
            {rows
              .filter((r) => !filter || r.strategy === filter)
              .slice(0, 80)
              .map((r, i) => (
                <tr
                  key={i}
                  className="border-b border-gray-800/50 hover:bg-gray-800/50 cursor-pointer transition-colors"
                >
                  <td className="py-2 px-3 font-mono text-xs text-gray-400">
                    <Link href={`/intent/${r.source}/${r.intentIndex}`} className="hover:text-blue-400">
                      {fmt(r.time)}
                      <span className="text-gray-600 ml-1">{ago(r.time)}</span>
                    </Link>
                  </td>
                  <td className="py-2 px-3 text-xs">
                    <Link href={`/intent/${r.source}/${r.intentIndex}`}>
                      <span className="text-gray-200">{r.strategy}</span>
                      {r.strategy !== r.source && (
                        <span className="text-gray-600 ml-1">({r.source})</span>
                      )}
                    </Link>
                  </td>
                  <td className="py-2 px-3 text-center">
                    <span
                      className={`inline-block px-1.5 py-0.5 text-xs rounded font-semibold ${
                        r.side === "BUY"
                          ? "bg-green-900/50 text-green-400"
                          : "bg-red-900/50 text-red-400"
                      }`}
                    >
                      {r.side}
                    </span>
                  </td>
                  <td className="py-2 px-3 text-center">
                    <span
                      className={`inline-block px-1.5 py-0.5 text-xs rounded font-medium ${
                        r.outcome === "YES"
                          ? "bg-green-900/30 text-green-400"
                          : "bg-red-900/30 text-red-400"
                      }`}
                    >
                      {r.outcome}
                    </span>
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-gray-200">
                    ${r.sizeUsd.toFixed(0)}
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-gray-400">
                    {r.maxPrice != null ? r.maxPrice.toFixed(3) : "\u2014"}
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-gray-200">
                    {r.fillPrice != null ? r.fillPrice.toFixed(3) : "\u2014"}
                  </td>
                  <td className="py-2 px-3 text-center">
                    <span
                      className={`inline-block px-1.5 py-0.5 text-xs rounded font-medium ${
                        r.fillStatus === "filled"
                          ? "bg-green-900/50 text-green-400"
                          : r.fillStatus === "rejected"
                          ? "bg-red-900/50 text-red-400"
                          : "bg-gray-800 text-gray-500"
                      }`}
                    >
                      {r.fillStatus}
                    </span>
                  </td>
                  <td
                    className="py-2 px-3 text-xs text-gray-500 max-w-xs truncate"
                    title={r.reason}
                  >
                    {r.reason}
                  </td>
                </tr>
              ))}
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={9}
                  className="py-8 text-center text-gray-600"
                >
                  No intents captured
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Round Trips Table
// ---------------------------------------------------------------------------

function RoundTripsTable({
  trips,
  summary,
}: {
  trips: RoundTrip[];
  summary: RoundTripSummary;
}) {
  if (trips.length === 0)
    return <div className="text-sm text-gray-600 p-4">No round trips yet</div>;

  return (
    <div>
      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3 mb-4">
        <Card value={String(summary.total_trips)} label="Total Trips" />
        <Card
          value={String(summary.closed)}
          label="Closed"
          color="text-blue-400"
        />
        <Card
          value={String(summary.open)}
          label="Open"
          color="text-yellow-400"
        />
        <Card
          value={`$${summary.total_pnl.toFixed(2)}`}
          label="Total PnL"
          color={summary.total_pnl >= 0 ? "text-green-400" : "text-red-400"}
        />
        <Card
          value={`${summary.wins}/${summary.losses}`}
          label="Wins/Losses"
          color="text-cyan-400"
        />
        <Card
          value={`${summary.hit_rate}%`}
          label="Hit Rate"
          color={summary.hit_rate >= 50 ? "text-green-400" : "text-red-400"}
        />
        <Card
          value={`${summary.avg_hold_s.toFixed(0)}s`}
          label="Avg Hold"
          color="text-purple-400"
        />
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-gray-400 text-left border-b border-gray-800">
              <th className="py-2 px-3 font-medium">Entry</th>
              <th className="py-2 px-3 font-medium">Strategy</th>
              <th className="py-2 px-3 font-medium text-center">Outcome</th>
              <th className="py-2 px-3 font-medium text-right">Entry $</th>
              <th className="py-2 px-3 font-medium text-right">Exit $</th>
              <th className="py-2 px-3 font-medium text-right">Size</th>
              <th className="py-2 px-3 font-medium text-right">Hold</th>
              <th className="py-2 px-3 font-medium text-right">PnL</th>
              <th className="py-2 px-3 font-medium text-center">Status</th>
              <th className="py-2 px-3 font-medium">Exit Reason</th>
            </tr>
          </thead>
          <tbody>
            {trips.map((t, i) => (
              <tr
                key={i}
                className="border-b border-gray-800/50 hover:bg-gray-800/50 transition-colors cursor-pointer"
                onClick={() => window.location.href = `/roundtrip/${encodeURIComponent(t.config)}/${encodeURIComponent(t.condition_id)}`}
              >
                <td className="py-2 px-3 font-mono text-xs text-gray-400">
                  {fmt(t.entry_time)}
                  <span className="text-gray-600 ml-1">{ago(t.entry_time)}</span>
                </td>
                <td className="py-2 px-3 text-xs text-gray-200">{t.strategy}</td>
                <td className="py-2 px-3 text-center">
                  <span
                    className={`inline-block px-1.5 py-0.5 text-xs rounded font-medium ${
                      t.outcome === "YES"
                        ? "bg-green-900/30 text-green-400"
                        : "bg-red-900/30 text-red-400"
                    }`}
                  >
                    {t.outcome}
                  </span>
                </td>
                <td className="py-2 px-3 text-right font-mono text-gray-200">
                  {t.entry_price != null ? t.entry_price.toFixed(3) : "\u2014"}
                </td>
                <td className="py-2 px-3 text-right font-mono text-gray-200">
                  {t.exit_price != null ? t.exit_price.toFixed(3) : "\u2014"}
                </td>
                <td className="py-2 px-3 text-right font-mono text-gray-400">
                  ${t.entry_size.toFixed(0)}
                </td>
                <td className="py-2 px-3 text-right font-mono text-gray-400">
                  {t.hold_s != null ? `${t.hold_s.toFixed(0)}s` : "\u2014"}
                </td>
                <td
                  className={`py-2 px-3 text-right font-mono font-semibold ${
                    t.pnl == null
                      ? "text-gray-500"
                      : t.pnl >= 0
                      ? "text-green-400"
                      : "text-red-400"
                  }`}
                >
                  {t.pnl != null ? `$${t.pnl.toFixed(2)}` : "\u2014"}
                </td>
                <td className="py-2 px-3 text-center">
                  <span
                    className={`inline-block px-1.5 py-0.5 text-xs rounded font-medium ${
                      t.status === "closed"
                        ? "bg-green-900/50 text-green-400"
                        : t.status === "open"
                        ? "bg-yellow-900/40 text-yellow-400"
                        : "bg-red-900/50 text-red-400"
                    }`}
                  >
                    {t.status}
                  </span>
                </td>
                <td
                  className="py-2 px-3 text-xs text-gray-500 max-w-xs truncate"
                  title={t.exit_reason}
                >
                  {t.exit_reason.replace("scalp_exit ", "")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Dashboard
// ---------------------------------------------------------------------------

export default function Dashboard() {
  const [health, setHealth] = useState("loading...");
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [ingestion, setIngestion] = useState<IngestionData | null>(null);
  const [metadata, setMetadata] = useState<MetadataData | null>(null);
  const [activity, setActivity] = useState<Record<string, StrategyActivity>>({});
  const [intents, setIntents] = useState<IntentRecord[]>([]);
  const [fills, setFills] = useState<FillRecord[]>([]);
  const [roundTrips, setRoundTrips] = useState<RoundTrip[]>([]);
  const [roundTripSummary, setRoundTripSummary] = useState<RoundTripSummary | null>(null);
  const [stratFilter, setStratFilter] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<string>("");

  const refresh = useCallback(async () => {
    const [h, m, ing, meta, act, int, fil, rt] = await Promise.allSettled([
      fetchHealth(),
      fetchMetrics(),
      fetchIngestion(1),
      fetchMetadata(),
      fetchActivity(),
      fetchIntents(undefined, 100),
      fetchFills(undefined, 100),
      fetchRoundTrips(undefined, 100),
    ]);
    if (h.status === "fulfilled") setHealth(h.value.status);
    else setHealth("unreachable");
    if (m.status === "fulfilled") setMetrics(m.value);
    if (ing.status === "fulfilled") setIngestion(ing.value);
    if (meta.status === "fulfilled") setMetadata(meta.value);
    if (act.status === "fulfilled") setActivity(act.value);
    if (int.status === "fulfilled") setIntents(int.value.intents);
    if (fil.status === "fulfilled") setFills(fil.value.fills);
    if (rt.status === "fulfilled") {
      setRoundTrips(rt.value.roundtrips);
      setRoundTripSummary(rt.value.summary);
    }
    setLastRefresh(new Date().toLocaleTimeString());
  }, []);

  useEffect(() => {
    refresh();
    const iv = setInterval(refresh, 10_000);
    return () => clearInterval(iv);
  }, [refresh]);

  return (
    <main className="min-h-screen bg-gray-950 text-gray-100 p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* ── Header ────────────────────────────────── */}
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold">Pipeline Dashboard</h1>
          <span className="text-xs text-gray-600">
            Last refresh: {lastRefresh} &middot; auto 10s
          </span>
        </div>

        {/* ── Top cards ─────────────────────────────── */}
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
          <Card
            value={health}
            label="API Health"
            color={health === "ok" ? "text-green-400" : "text-red-400"}
          />
          <Card
            value={metrics ? `${metrics.req_per_s}/s` : "\u2014"}
            label="Request Rate (60s)"
            color="text-blue-400"
          />
          <Card
            value={ingestion ? num(ingestion.total.trades) : "\u2014"}
            label="Trades (1h)"
            color="text-purple-400"
          />
          <Card
            value={ingestion ? `${ingestion.total.tps}/s` : "\u2014"}
            label="Avg TPS"
            color="text-green-400"
          />
          <Card
            value={metadata ? num(metadata.markets.open) : "\u2014"}
            label="Open Markets"
            color="text-cyan-400"
          />
          <Card
            value={metadata ? num(metadata.tokens) : "\u2014"}
            label="Token Mappings"
          />
        </div>

        {/* ── Ingestion by Source ────────────────────── */}
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
            Ingestion by Source (1h)
          </h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
            {(ingestion?.by_source || []).map((s) => (
              <div key={s.source} className="bg-gray-900 rounded-lg p-3">
                <div className="text-xs text-gray-500">{s.source}</div>
                <div className="text-lg font-mono font-bold text-purple-400">
                  {num(s.trades)}
                </div>
                <div className="text-xs text-gray-500 font-mono">
                  {s.tps}/s &middot; latest {fmtDt(s.latest)}
                </div>
              </div>
            ))}
          </div>
          <div className="bg-gray-900 rounded-lg p-3">
            {ingestion && <TpsChart series={ingestion.tps_series} />}
          </div>
        </section>

        {/* ── Metadata ──────────────────────────────── */}
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
            Metadata
          </h2>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            <Card
              value={metadata ? num(metadata.markets.total_markets) : "\u2014"}
              label="Total Markets"
            />
            <Card
              value={metadata ? num(metadata.markets.open) : "\u2014"}
              label="Open"
              color="text-green-400"
            />
            <Card
              value={metadata ? num(metadata.markets.resolved) : "\u2014"}
              label="Resolved"
              color="text-purple-400"
            />
            <Card
              value={metadata ? num(metadata.tokens) : "\u2014"}
              label="Token Mappings"
              color="text-cyan-400"
            />
            <Card
              value={metadata ? fmtDt(metadata.last_update) : "\u2014"}
              label="Last Metadata Refresh"
            />
          </div>
        </section>

        {/* ── Strategy Activity ─────────────────────── */}
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
            Strategy Activity
          </h2>
          <StrategySummary data={activity} />
        </section>

        {/* ── Round Trips ────────────────────────────── */}
        {roundTrips.length > 0 && (
          <section>
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
              Round Trips (Scalp)
            </h2>
            <div className="bg-gray-900 rounded-lg p-4">
              <RoundTripsTable
                trips={roundTrips}
                summary={roundTripSummary || { total_trips: 0, closed: 0, open: 0, total_pnl: 0, wins: 0, losses: 0, hit_rate: 0, avg_hold_s: 0 }}
              />
            </div>
          </section>
        )}

        {/* ── Intent & Fill Table ───────────────────── */}
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
            Recent Intents &amp; Fills
          </h2>
          <div className="bg-gray-900 rounded-lg p-4">
            <IntentTable
              intents={intents}
              fills={fills}
              filter={stratFilter}
              setFilter={setStratFilter}
            />
          </div>
        </section>
      </div>
    </main>
  );
}
