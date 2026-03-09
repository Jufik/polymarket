"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import {
  fetchRoundTripDetail,
  fetchPriceHistory,
  RoundTripDetail,
  PricePoint,
  ZoomLevel,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtTs(ts: number) {
  return new Date(ts * 1000).toLocaleString("en-GB", {
    month: "short",
    day: "numeric",
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
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function priceFmt(p: number | null | undefined) {
  if (p == null) return "\u2014";
  return Number(p).toFixed(4);
}

function usdFmt(v: number | null | undefined) {
  if (v == null) return "\u2014";
  return `$${Number(v).toFixed(2)}`;
}

// ---------------------------------------------------------------------------
// Info Row
// ---------------------------------------------------------------------------

function Info({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between py-1.5 border-b border-gray-800/50">
      <span className="text-xs text-gray-500">{label}</span>
      <span className="text-sm font-mono text-gray-200">{children}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Price Chart (SVG) — shows entry + exit markers
// ---------------------------------------------------------------------------

function RoundTripChart({
  points,
  entryTime,
  exitTime,
  entryPrice,
  exitPrice,
  outcome,
}: {
  points: PricePoint[];
  entryTime: number;
  exitTime: number | null;
  entryPrice: number | null;
  exitPrice: number | null;
  outcome: string;
}) {
  if (points.length === 0)
    return <div className="text-sm text-gray-600 p-4">No price data</div>;

  const W = 700, H = 220, PL = 55, PR = 10, PT = 15, PB = 30;
  const cw = W - PL - PR, ch = H - PT - PB;

  const yesByMin = new Map<string, number>();
  const noByMin = new Map<string, number>();
  const allMinutes = new Set<string>();
  for (const p of points) {
    allMinutes.add(p.minute);
    const v = Number(p.avg_price);
    if (p.outcome === "YES") yesByMin.set(p.minute, v);
    else if (p.outcome === "NO") noByMin.set(p.minute, v);
    else if (!yesByMin.has(p.minute)) yesByMin.set(p.minute, v);
  }
  const minutes = Array.from(allMinutes).sort();
  const hasYes = yesByMin.size > 0;
  const hasNo = noByMin.size > 0;

  const allPrices: number[] = [];
  for (const v of yesByMin.values()) allPrices.push(v);
  for (const v of noByMin.values()) allPrices.push(v);
  if (entryPrice != null) allPrices.push(entryPrice);
  if (exitPrice != null) allPrices.push(exitPrice);
  const minP = Math.min(...allPrices) * 0.995;
  const maxP = Math.max(...allPrices) * 1.005;
  const range = maxP - minP || 0.01;

  const xPos = (i: number) => PL + (i / Math.max(minutes.length - 1, 1)) * cw;
  const yPos = (v: number) => PT + ch - ((v - minP) / range) * ch;

  const buildLine = (byMin: Map<string, number>) => {
    const segs: string[] = [];
    minutes.forEach((m, i) => {
      const v = byMin.get(m);
      if (v != null) segs.push(`${xPos(i)},${yPos(v)}`);
    });
    return segs.join(" ");
  };

  // Find minute indices for entry/exit
  const entryDt = new Date(entryTime * 1000).toISOString().slice(0, 16).replace("T", " ");
  const exitDt = exitTime
    ? new Date(exitTime * 1000).toISOString().slice(0, 16).replace("T", " ")
    : null;

  let entryIdx = -1, exitIdx = -1;
  for (let i = 0; i < minutes.length; i++) {
    if (entryIdx < 0 && minutes[i] >= entryDt) entryIdx = i;
    if (exitDt && exitIdx < 0 && minutes[i] >= exitDt) exitIdx = i;
  }

  const yesStroke = outcome === "NO" ? 0.8 : 1.8;
  const noStroke = outcome === "YES" ? 0.8 : 1.8;
  const yesOpacity = outcome === "NO" ? 0.4 : 1;
  const noOpacity = outcome === "YES" ? 0.4 : 1;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      {/* Grid */}
      {[0, 0.25, 0.5, 0.75, 1].map((f) => {
        const val = minP + range * f;
        const yy = yPos(val);
        return (
          <g key={f}>
            <line x1={PL} x2={W - PR} y1={yy} y2={yy} stroke="#1e293b" strokeWidth="0.5" />
            <text x={PL - 6} y={yy + 3} textAnchor="end" fill="#64748b" fontSize="9">
              {val.toFixed(3)}
            </text>
          </g>
        );
      })}

      {/* Shade the hold region */}
      {entryIdx >= 0 && exitIdx >= 0 && (
        <rect
          x={xPos(entryIdx)}
          y={PT}
          width={xPos(exitIdx) - xPos(entryIdx)}
          height={ch}
          fill="#38bdf8"
          opacity={0.05}
        />
      )}

      {/* Price lines */}
      {hasYes && (
        <polyline points={buildLine(yesByMin)} fill="none" stroke="#22c55e"
          strokeWidth={yesStroke} strokeLinejoin="round" opacity={yesOpacity} />
      )}
      {hasNo && (
        <polyline points={buildLine(noByMin)} fill="none" stroke="#ef4444"
          strokeWidth={noStroke} strokeLinejoin="round" opacity={noOpacity} />
      )}

      {/* Entry vertical */}
      {entryIdx >= 0 && (
        <>
          <line x1={xPos(entryIdx)} x2={xPos(entryIdx)} y1={PT} y2={H - PB}
            stroke="#22c55e" strokeWidth="1.5" strokeDasharray="4,3" />
          <text x={xPos(entryIdx) + 4} y={PT + 10} fill="#22c55e" fontSize="9" fontWeight="bold">
            BUY
          </text>
        </>
      )}

      {/* Exit vertical */}
      {exitIdx >= 0 && (
        <>
          <line x1={xPos(exitIdx)} x2={xPos(exitIdx)} y1={PT} y2={H - PB}
            stroke="#ef4444" strokeWidth="1.5" strokeDasharray="4,3" />
          <text x={xPos(exitIdx) + 4} y={PT + 10} fill="#ef4444" fontSize="9" fontWeight="bold">
            SELL
          </text>
        </>
      )}

      {/* Entry price horizontal */}
      {entryPrice != null && (
        <line x1={PL} x2={W - PR} y1={yPos(entryPrice)} y2={yPos(entryPrice)}
          stroke="#38bdf8" strokeWidth="1" strokeDasharray="3,3" />
      )}

      {/* Exit price horizontal */}
      {exitPrice != null && (
        <line x1={PL} x2={W - PR} y1={yPos(exitPrice)} y2={yPos(exitPrice)}
          stroke="#a78bfa" strokeWidth="1" strokeDasharray="3,3" />
      )}

      {/* X labels */}
      {minutes.map((m, i) => {
        if (minutes.length > 30 && i % 5 !== 0) return null;
        if (minutes.length > 15 && minutes.length <= 30 && i % 3 !== 0) return null;
        return (
          <text key={i} x={xPos(i)} y={H - 4} textAnchor="middle" fill="#64748b" fontSize="8">
            {m.slice(11, 16)}
          </text>
        );
      })}

      {/* Legend */}
      {hasYes && (
        <>
          <line x1={W - PR - 80} x2={W - PR - 65} y1={PT + 6} y2={PT + 6} stroke="#22c55e" strokeWidth="2" />
          <text x={W - PR - 62} y={PT + 9} fill="#22c55e" fontSize="9">YES</text>
        </>
      )}
      {hasNo && (
        <>
          <line x1={W - PR - 80} x2={W - PR - 65} y1={PT + 18} y2={PT + 18} stroke="#ef4444" strokeWidth="2" />
          <text x={W - PR - 62} y={PT + 21} fill="#ef4444" fontSize="9">NO</text>
        </>
      )}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Zoomable wrapper
// ---------------------------------------------------------------------------

const ZOOM_LEVELS: { label: string; value: ZoomLevel }[] = [
  { label: "1h", value: "1h" },
  { label: "6h", value: "6h" },
  { label: "24h", value: "24h" },
  { label: "7d", value: "7d" },
  { label: "All", value: "all" },
];

function ZoomableChart({
  data,
  initialPoints,
}: {
  data: RoundTripDetail;
  initialPoints: PricePoint[];
}) {
  const [zoom, setZoom] = useState<ZoomLevel>("6h");
  const [points, setPoints] = useState<PricePoint[]>(initialPoints);
  const [loading, setLoading] = useState(false);

  const handleZoom = (z: ZoomLevel) => {
    setZoom(z);
    setLoading(true);
    fetchPriceHistory(data.condition_id, data.buy_intent.signal_time, z)
      .then((resp) => setPoints(resp.points))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Price History
        </h3>
        <div className="flex gap-1">
          {ZOOM_LEVELS.map((z) => (
            <button
              key={z.value}
              onClick={() => handleZoom(z.value)}
              className={`px-2 py-0.5 text-xs rounded ${
                zoom === z.value
                  ? "bg-blue-600 text-white"
                  : "bg-gray-800 text-gray-400 hover:bg-gray-700"
              }`}
            >
              {z.label}
            </button>
          ))}
        </div>
      </div>
      {loading ? (
        <div className="text-sm text-gray-500 py-8 text-center">Loading...</div>
      ) : (
        <RoundTripChart
          points={points}
          entryTime={data.buy_intent.signal_time}
          exitTime={data.sell_intent?.signal_time ?? null}
          entryPrice={data.buy_fill?.filled_price ?? null}
          exitPrice={data.sell_fill?.filled_price ?? null}
          outcome={data.outcome}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function RoundTripDetailPage() {
  const params = useParams();
  const config = params.config as string;
  const cid = params.cid as string;

  const [data, setData] = useState<RoundTripDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchRoundTripDetail(config, cid)
      .then((d) => {
        if (d.error) setError(d.error);
        else if (!d.buy_intent) setError((d as any).detail || "Invalid response");
        else setData(d);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [config, cid]);

  if (loading) {
    return (
      <main className="min-h-screen bg-gray-950 text-gray-100 p-6">
        <div className="max-w-5xl mx-auto text-gray-500">Loading round trip...</div>
      </main>
    );
  }

  if (error || !data) {
    return (
      <main className="min-h-screen bg-gray-950 text-gray-100 p-6">
        <div className="max-w-5xl mx-auto">
          <Link href="/" className="text-blue-400 text-sm hover:underline mb-4 inline-block">
            &larr; Dashboard
          </Link>
          <div className="text-red-400 mt-4">{error || "Round trip not found"}</div>
        </div>
      </main>
    );
  }

  const { buy_intent, sell_intent, buy_fill, sell_fill, market } = data;
  const entryPrice = buy_fill?.filled_price;
  const exitPrice = sell_fill?.filled_price;
  const entrySize = buy_fill?.filled_size_usd ?? buy_intent.size_usd;
  const tokens = entryPrice && entryPrice > 0 ? entrySize / entryPrice : 0;
  const isClosed = sell_fill != null;

  return (
    <main className="min-h-screen bg-gray-950 text-gray-100 p-6">
      <div className="max-w-5xl mx-auto space-y-6">
        {/* Nav */}
        <div className="flex items-center gap-4">
          <Link href="/" className="text-blue-400 text-sm hover:underline">
            &larr; Dashboard
          </Link>
          <h1 className="text-xl font-bold">
            Round Trip
            <span className="text-gray-500 text-sm font-normal ml-2">
              {data.strategy} &middot;{" "}
              <span className={data.outcome === "YES" ? "text-green-400" : "text-red-400"}>
                {data.outcome}
              </span>
            </span>
          </h1>
          <span
            className={`px-2 py-0.5 text-xs rounded font-medium ${
              isClosed
                ? "bg-green-900/50 text-green-400"
                : "bg-yellow-900/40 text-yellow-400"
            }`}
          >
            {isClosed ? "CLOSED" : "OPEN"}
          </span>
        </div>

        {/* Market question */}
        {market && (
          <div className="bg-gray-900 rounded-lg p-4 border border-gray-800">
            <div className="text-sm text-gray-400 mb-1">{market.category}</div>
            <div className="text-lg font-medium">{market.question}</div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-xs text-gray-500">
              {market.resolved_at && (
                <span>
                  Resolved: {fmtDt(market.resolved_at)} &middot;{" "}
                  <span className={market.winner_outcome === data.outcome ? "text-green-400" : "text-red-400"}>
                    Winner: {market.winner_outcome}
                  </span>
                </span>
              )}
            </div>
          </div>
        )}

        {/* PnL banner */}
        {data.pnl != null && (
          <div
            className={`rounded-lg p-5 text-center ${
              data.pnl >= 0 ? "bg-green-900/20 border border-green-800/50" : "bg-red-900/20 border border-red-800/50"
            }`}
          >
            <div className={`text-3xl font-bold font-mono ${data.pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
              {data.pnl >= 0 ? "+" : ""}{usdFmt(data.pnl)}
            </div>
            <div className="text-sm text-gray-400 mt-1">
              {entryPrice && exitPrice && (
                <span>
                  {priceFmt(entryPrice)} &rarr; {priceFmt(exitPrice)}
                  {" "}&middot;{" "}
                  {tokens.toFixed(1)} tokens
                  {" "}&middot;{" "}
                  {data.hold_s != null ? `${data.hold_s.toFixed(0)}s hold` : ""}
                </span>
              )}
            </div>
          </div>
        )}

        {/* Entry + Exit side by side */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Entry */}
          <div className="bg-gray-900 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-green-400 uppercase tracking-wider mb-3">
              Entry (BUY)
            </h3>
            <Info label="Time">
              {fmtTs(buy_intent.signal_time)}{" "}
              <span className="text-gray-500 text-xs">({ago(buy_intent.signal_time)})</span>
            </Info>
            <Info label="Outcome">
              <span className={data.outcome === "YES" ? "text-green-400" : "text-red-400"}>
                {data.outcome}
              </span>
            </Info>
            <Info label="Size">{usdFmt(buy_intent.size_usd)}</Info>
            <Info label="Max Price">{priceFmt(buy_intent.max_price)}</Info>
            {buy_fill && (
              <>
                <div className="mt-2 mb-1 border-t border-gray-800" />
                <Info label="Fill Price">{priceFmt(buy_fill.filled_price)}</Info>
                <Info label="Fill Size">{usdFmt(buy_fill.filled_size_usd)}</Info>
                <Info label="Fee">{usdFmt(buy_fill.fee_usd)}</Info>
                <Info label="Status">
                  <span className="text-green-400">{buy_fill.status}</span>
                </Info>
              </>
            )}
            {data.ob_at_entry && (
              <>
                <div className="mt-2 mb-1 border-t border-gray-800" />
                <Info label="OB Bid">{priceFmt(data.ob_at_entry.best_bid)}</Info>
                <Info label="OB Ask">{priceFmt(data.ob_at_entry.best_ask)}</Info>
                <Info label="Spread">
                  {((data.ob_at_entry.best_ask - data.ob_at_entry.best_bid) * 100).toFixed(1)}%
                </Info>
              </>
            )}
            <div className="mt-3 text-xs text-gray-500 bg-gray-800/50 rounded p-2">
              {buy_intent.reason}
            </div>
          </div>

          {/* Exit */}
          <div className="bg-gray-900 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-red-400 uppercase tracking-wider mb-3">
              Exit (SELL)
            </h3>
            {sell_intent ? (
              <>
                <Info label="Time">
                  {fmtTs(sell_intent.signal_time)}{" "}
                  <span className="text-gray-500 text-xs">({ago(sell_intent.signal_time)})</span>
                </Info>
                <Info label="Hold Duration">
                  {data.hold_s != null ? `${data.hold_s.toFixed(1)}s` : "\u2014"}
                </Info>
                {sell_fill && (
                  <>
                    <div className="mt-2 mb-1 border-t border-gray-800" />
                    <Info label="Fill Price">{priceFmt(sell_fill.filled_price)}</Info>
                    <Info label="Fill Size">{usdFmt(sell_fill.filled_size_usd)}</Info>
                    <Info label="Fee">{usdFmt(sell_fill.fee_usd)}</Info>
                    <Info label="Status">
                      <span className="text-green-400">{sell_fill.status}</span>
                    </Info>
                  </>
                )}
                {data.ob_at_exit && (
                  <>
                    <div className="mt-2 mb-1 border-t border-gray-800" />
                    <Info label="OB Bid">{priceFmt(data.ob_at_exit.best_bid)}</Info>
                    <Info label="OB Ask">{priceFmt(data.ob_at_exit.best_ask)}</Info>
                    <Info label="Spread">
                      {((data.ob_at_exit.best_ask - data.ob_at_exit.best_bid) * 100).toFixed(1)}%
                    </Info>
                  </>
                )}
                <div className="mt-3 text-xs text-gray-500 bg-gray-800/50 rounded p-2">
                  {sell_intent.reason}
                </div>
              </>
            ) : (
              <div className="text-yellow-400 text-sm py-4">
                Position still open &mdash; no exit signal yet
              </div>
            )}
          </div>
        </div>

        {/* Price chart */}
        <ZoomableChart data={data} initialPoints={data.price_history} />

        {/* IDs */}
        <div className="bg-gray-900 rounded-lg p-4 text-xs text-gray-500 font-mono space-y-1">
          <div>condition_id: {data.condition_id}</div>
          {buy_intent.asset_id && <div>asset_id: {buy_intent.asset_id}</div>}
        </div>
      </div>
    </main>
  );
}
