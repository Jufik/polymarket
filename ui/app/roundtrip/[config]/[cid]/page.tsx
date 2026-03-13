"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import {
  fetchRoundTripDetail,
  fetchPriceHistory,
  RoundTripDetail,
  TradeBubble,
  OBPoint,
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

/** Parse a ClickHouse timestamp string to epoch ms */
function parseTs(s: string): number {
  if (!s.includes("T") && !s.endsWith("Z")) s += "Z";
  return new Date(s).getTime();
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
// Aggregate trades by (price, outcome) for bubble chart
// ---------------------------------------------------------------------------

interface AggBubble {
  price: number;
  outcome: string;
  size_usd: number;
  minTs: number; // earliest trade ms — for x position
  maxTs: number;
  count: number;
}

function aggregateTrades(trades: TradeBubble[]): AggBubble[] {
  const map = new Map<string, AggBubble>();
  for (const t of trades) {
    const p = Number(t.price);
    const key = `${p}|${t.outcome}`;
    const ms = parseTs(t.ts);
    const cur = map.get(key);
    if (cur) {
      cur.size_usd += t.size_usd;
      cur.count++;
      if (ms < cur.minTs) cur.minTs = ms;
      if (ms > cur.maxTs) cur.maxTs = ms;
    } else {
      map.set(key, { price: p, outcome: t.outcome, size_usd: t.size_usd, minTs: ms, maxTs: ms, count: 1 });
    }
  }
  return Array.from(map.values());
}

// ---------------------------------------------------------------------------
// Chart — BBA lines + aggregated trade bubbles + entry/exit diamonds
// ---------------------------------------------------------------------------

function RoundTripChart({
  trades,
  obSeries,
  entryTime,
  exitTime,
  entryPrice,
  exitPrice,
}: {
  trades: TradeBubble[];
  obSeries: OBPoint[];
  entryTime: number;
  exitTime: number | null;
  entryPrice: number | null;
  exitPrice: number | null;
}) {
  if (trades.length === 0 && obSeries.length === 0)
    return <div className="text-sm text-gray-600 p-4">No price data</div>;

  const W = 700, H = 260, PL = 55, PR = 10, PT = 15, PB = 30;
  const cw = W - PL - PR, ch = H - PT - PB;

  // Aggregate trades by price
  const bubbles = aggregateTrades(trades);

  // Collect all timestamps and prices — always include entry/exit
  const allTsMs: number[] = [entryTime * 1000];
  const allPrices: number[] = [];

  if (exitTime) allTsMs.push(exitTime * 1000);
  if (entryPrice != null) allPrices.push(entryPrice);
  if (exitPrice != null) allPrices.push(exitPrice);

  for (const b of bubbles) {
    allTsMs.push(b.minTs, b.maxTs);
    allPrices.push(b.price);
  }
  for (const ob of obSeries) {
    const ms = parseTs(ob.ts);
    allTsMs.push(ms);
    allPrices.push(Number(ob.bid), Number(ob.ask));
  }

  if (allPrices.length === 0) return <div className="text-sm text-gray-600 p-4">No data</div>;

  const rawMinTs = Math.min(...allTsMs);
  const rawMaxTs = Math.max(...allTsMs);
  const rawRange = rawMaxTs - rawMinTs || 60_000;
  // Add 5% padding so entry/exit are never at the very edge
  const pad = rawRange * 0.05;
  const minTs = rawMinTs - pad;
  const maxTs = rawMaxTs + pad;
  const tsRange = maxTs - minTs;

  const minP = Math.min(...allPrices) * 0.995;
  const maxP = Math.max(...allPrices) * 1.005;
  const pRange = maxP - minP || 0.01;

  const xPos = (ms: number) => PL + ((ms - minTs) / tsRange) * cw;
  const yPos = (v: number) => PT + ch - ((v - minP) / pRange) * ch;

  // BBA lines
  const bidPts = obSeries
    .map((ob) => ({ x: xPos(parseTs(ob.ts)), y: yPos(Number(ob.bid)) }))
    .filter((p) => isFinite(p.x) && isFinite(p.y));
  const askPts = obSeries
    .map((ob) => ({ x: xPos(parseTs(ob.ts)), y: yPos(Number(ob.ask)) }))
    .filter((p) => isFinite(p.x) && isFinite(p.y));

  const toPolyline = (pts: { x: number; y: number }[]) =>
    pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");

  const entryMs = entryTime * 1000;
  const exitMs = exitTime ? exitTime * 1000 : null;

  // Bubble radius: scale by size_usd, clamp [2.5, 10]
  const maxSize = Math.max(...bubbles.map((b) => b.size_usd), 1);
  const bubbleR = (size: number) => Math.max(2.5, Math.min(10, 2.5 + (size / maxSize) * 7.5));

  const bubbleColor = (outcome: string) => {
    if (outcome === "YES") return "#22c55e";
    if (outcome === "NO") return "#ef4444";
    return "#94a3b8";
  };

  // X position for a bubble: midpoint of its time range
  const bubbleX = (b: AggBubble) => xPos((b.minTs + b.maxTs) / 2);

  // X-axis time labels
  const nLabels = 7;
  const labelStep = tsRange / nLabels;
  const timeLabels: { ms: number; label: string }[] = [];
  for (let i = 0; i <= nLabels; i++) {
    const ms = minTs + i * labelStep;
    const d = new Date(ms);
    const hh = d.getUTCHours().toString().padStart(2, "0");
    const mm = d.getUTCMinutes().toString().padStart(2, "0");
    const ss = d.getUTCSeconds().toString().padStart(2, "0");
    // Show seconds for short windows
    timeLabels.push({ ms, label: tsRange < 120_000 ? `${hh}:${mm}:${ss}` : `${hh}:${mm}` });
  }

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      {/* Grid */}
      {[0, 0.25, 0.5, 0.75, 1].map((f) => {
        const val = minP + pRange * f;
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
      {exitMs != null && (
        <rect
          x={xPos(entryMs)}
          y={PT}
          width={Math.max(0, xPos(exitMs) - xPos(entryMs))}
          height={ch}
          fill="#38bdf8"
          opacity={0.05}
        />
      )}

      {/* BBA bid line */}
      {bidPts.length > 1 && (
        <polyline
          points={toPolyline(bidPts)}
          fill="none"
          stroke="#22c55e"
          strokeWidth="1.2"
          strokeOpacity="0.6"
        />
      )}

      {/* BBA ask line */}
      {askPts.length > 1 && (
        <polyline
          points={toPolyline(askPts)}
          fill="none"
          stroke="#ef4444"
          strokeWidth="1.2"
          strokeOpacity="0.6"
        />
      )}

      {/* Aggregated trade bubbles */}
      {bubbles.map((b, i) => {
        const cx = bubbleX(b);
        const cy = yPos(b.price);
        if (!isFinite(cx) || !isFinite(cy)) return null;
        const color = bubbleColor(b.outcome);
        return (
          <circle
            key={i}
            cx={cx}
            cy={cy}
            r={bubbleR(b.size_usd)}
            fill={color}
            fillOpacity={0.5}
            stroke={color}
            strokeWidth="0.6"
          >
            <title>{b.outcome} @ {b.price.toFixed(4)} — ${b.size_usd.toFixed(2)} ({b.count} trades)</title>
          </circle>
        );
      })}

      {/* Entry vertical line */}
      <line
        x1={xPos(entryMs)}
        x2={xPos(entryMs)}
        y1={PT}
        y2={H - PB}
        stroke="#38bdf8"
        strokeWidth="1.5"
        strokeDasharray="4,3"
      />
      <text x={xPos(entryMs) + 4} y={PT + 10} fill="#38bdf8" fontSize="9" fontWeight="bold">
        BUY
      </text>

      {/* Exit vertical line */}
      {exitMs != null && (
        <>
          <line
            x1={xPos(exitMs)}
            x2={xPos(exitMs)}
            y1={PT}
            y2={H - PB}
            stroke="#a78bfa"
            strokeWidth="1.5"
            strokeDasharray="4,3"
          />
          <text x={xPos(exitMs) + 4} y={PT + 10} fill="#a78bfa" fontSize="9" fontWeight="bold">
            SELL
          </text>
        </>
      )}

      {/* Entry diamond */}
      {entryPrice != null && (() => {
        const cx = xPos(entryMs);
        const cy = yPos(entryPrice);
        const s = 6;
        const d = `M${cx},${cy - s} L${cx + s},${cy} L${cx},${cy + s} L${cx - s},${cy} Z`;
        return <path d={d} fill="#38bdf8" stroke="#fff" strokeWidth="0.8" />;
      })()}

      {/* Exit diamond */}
      {exitPrice != null && exitMs != null && (() => {
        const cx = xPos(exitMs);
        const cy = yPos(exitPrice);
        const s = 6;
        const d = `M${cx},${cy - s} L${cx + s},${cy} L${cx},${cy + s} L${cx - s},${cy} Z`;
        return <path d={d} fill="#a78bfa" stroke="#fff" strokeWidth="0.8" />;
      })()}

      {/* Entry price horizontal */}
      {entryPrice != null && (
        <line
          x1={PL}
          x2={W - PR}
          y1={yPos(entryPrice)}
          y2={yPos(entryPrice)}
          stroke="#38bdf8"
          strokeWidth="0.8"
          strokeDasharray="3,3"
          opacity={0.5}
        />
      )}

      {/* Exit price horizontal */}
      {exitPrice != null && (
        <line
          x1={PL}
          x2={W - PR}
          y1={yPos(exitPrice)}
          y2={yPos(exitPrice)}
          stroke="#a78bfa"
          strokeWidth="0.8"
          strokeDasharray="3,3"
          opacity={0.5}
        />
      )}

      {/* X-axis time labels */}
      {timeLabels.map((tl, i) => (
        <text
          key={i}
          x={xPos(tl.ms)}
          y={H - 4}
          textAnchor="middle"
          fill="#64748b"
          fontSize="8"
        >
          {tl.label}
        </text>
      ))}

      {/* Legend */}
      <line x1={W - PR - 130} x2={W - PR - 115} y1={PT + 6} y2={PT + 6} stroke="#22c55e" strokeWidth="1.2" strokeOpacity="0.6" />
      <text x={W - PR - 112} y={PT + 9} fill="#22c55e" fontSize="8">Bid</text>

      <line x1={W - PR - 130} x2={W - PR - 115} y1={PT + 16} y2={PT + 16} stroke="#ef4444" strokeWidth="1.2" strokeOpacity="0.6" />
      <text x={W - PR - 112} y={PT + 19} fill="#ef4444" fontSize="8">Ask</text>

      <circle cx={W - PR - 76} cy={PT + 6} r={3} fill="#22c55e" fillOpacity={0.5} />
      <text x={W - PR - 70} y={PT + 9} fill="#22c55e" fontSize="8">YES</text>

      <circle cx={W - PR - 76} cy={PT + 16} r={3} fill="#ef4444" fillOpacity={0.5} />
      <text x={W - PR - 70} y={PT + 19} fill="#ef4444" fontSize="8">NO</text>

      <path d={`M${W - PR - 32},${PT + 3} L${W - PR - 28},${PT + 6} L${W - PR - 32},${PT + 9} L${W - PR - 36},${PT + 6} Z`}
        fill="#38bdf8" stroke="#fff" strokeWidth="0.5" />
      <text x={W - PR - 24} y={PT + 9} fill="#38bdf8" fontSize="8">Entry</text>
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Zoomable wrapper — minute-based for round trips
// ---------------------------------------------------------------------------

const ZOOM_LEVELS: { label: string; value: ZoomLevel }[] = [
  { label: "1m", value: "1m" },
  { label: "2m", value: "2m" },
  { label: "5m", value: "5m" },
  { label: "15m", value: "15m" },
  { label: "30m", value: "30m" },
];

function ZoomableChart({ data }: { data: RoundTripDetail }) {
  const [zoom, setZoom] = useState<ZoomLevel>("5m");
  const [trades, setTrades] = useState<TradeBubble[]>(data.price_history);
  const [obSeries, setObSeries] = useState<OBPoint[]>(data.ob_series);
  const [loading, setLoading] = useState(false);

  const handleZoom = (z: ZoomLevel) => {
    setZoom(z);
    setLoading(true);
    fetchPriceHistory(data.condition_id, data.buy_intent.signal_time, z)
      .then((resp) => {
        setTrades(resp.points);
        setObSeries(resp.ob_series);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Price &amp; Orderbook
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
          trades={trades}
          obSeries={obSeries}
          entryTime={data.buy_intent.signal_time}
          exitTime={data.sell_intent?.signal_time ?? null}
          entryPrice={data.buy_fill?.filled_price ?? null}
          exitPrice={data.sell_fill?.filled_price ?? null}
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
        <ZoomableChart data={data} />

        {/* IDs */}
        <div className="bg-gray-900 rounded-lg p-4 text-xs text-gray-500 font-mono space-y-1">
          <div>condition_id: {data.condition_id}</div>
          {buy_intent.asset_id && <div>asset_id: {buy_intent.asset_id}</div>}
        </div>
      </div>
    </main>
  );
}
