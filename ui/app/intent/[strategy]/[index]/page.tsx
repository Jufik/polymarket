"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import {
  fetchIntentDetail,
  fetchPriceHistory,
  IntentDetail,
  PricePoint,
  TradeBubble,
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

function timeLeft(dateStr: string) {
  const end = new Date(dateStr + "Z").getTime();
  const now = Date.now();
  const diff = end - now;
  if (diff <= 0) return "overdue";
  const days = Math.floor(diff / 86400000);
  const hours = Math.floor((diff % 86400000) / 3600000);
  if (days > 0) return `${days}d ${hours}h left`;
  const mins = Math.floor((diff % 3600000) / 60000);
  if (hours > 0) return `${hours}h ${mins}m left`;
  return `${mins}m left`;
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
// Price Chart (SVG)
// ---------------------------------------------------------------------------

function PriceChart({
  points,
  signalTime,
  entryPrice,
  currentPrice,
  intentOutcome,
}: {
  points: PricePoint[];
  signalTime: number;
  entryPrice: number | null;
  currentPrice: number | null;
  intentOutcome: string;
}) {
  if (points.length === 0)
    return <div className="text-sm text-gray-600 p-4">No price data</div>;

  const W = 700,
    H = 200,
    PL = 55,
    PR = 10,
    PT = 15,
    PB = 30;
  const cw = W - PL - PR,
    ch = H - PT - PB;

  // Separate points by outcome, keyed by minute
  const yesByMin = new Map<string, number>();
  const noByMin = new Map<string, number>();
  const allMinutes = new Set<string>();
  for (const p of points) {
    allMinutes.add(p.minute);
    const v = Number(p.avg_price);
    if (p.outcome === "YES") yesByMin.set(p.minute, v);
    else if (p.outcome === "NO") noByMin.set(p.minute, v);
    else {
      // UNKNOWN — put on both if only one series exists, or skip
      if (!yesByMin.has(p.minute)) yesByMin.set(p.minute, v);
    }
  }
  const minutes = Array.from(allMinutes).sort();
  const hasYes = yesByMin.size > 0;
  const hasNo = noByMin.size > 0;

  // Price range across both series
  const allPrices: number[] = [];
  for (const v of yesByMin.values()) allPrices.push(v);
  for (const v of noByMin.values()) allPrices.push(v);
  if (entryPrice != null) allPrices.push(entryPrice);
  if (currentPrice != null) allPrices.push(currentPrice);
  const minP = Math.min(...allPrices) * 0.995;
  const maxP = Math.max(...allPrices) * 1.005;
  const range = maxP - minP || 0.01;

  const xPos = (i: number) => PL + (i / Math.max(minutes.length - 1, 1)) * cw;
  const yPos = (v: number) => PT + ch - ((v - minP) / range) * ch;

  // Build polyline strings
  const buildLine = (byMin: Map<string, number>) => {
    const segs: string[] = [];
    minutes.forEach((m, i) => {
      const v = byMin.get(m);
      if (v != null) segs.push(`${xPos(i)},${yPos(v)}`);
    });
    return segs.join(" ");
  };
  const yesLine = buildLine(yesByMin);
  const noLine = buildLine(noByMin);

  // Signal time index
  const signalDt = new Date(signalTime * 1000).toISOString().slice(0, 16).replace("T", " ");
  let signalIdx = -1;
  for (let i = 0; i < minutes.length; i++) {
    if (minutes[i] >= signalDt) { signalIdx = i; break; }
  }

  // Bold the intent's outcome, dim the other
  const yesStroke = intentOutcome === "NO" ? 0.8 : 1.8;
  const noStroke = intentOutcome === "YES" ? 0.8 : 1.8;
  const yesOpacity = intentOutcome === "NO" ? 0.4 : 1;
  const noOpacity = intentOutcome === "YES" ? 0.4 : 1;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      {/* Grid lines */}
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

      {/* YES price line */}
      {hasYes && (
        <polyline
          points={yesLine}
          fill="none"
          stroke="#22c55e"
          strokeWidth={yesStroke}
          strokeLinejoin="round"
          opacity={yesOpacity}
        />
      )}

      {/* NO price line */}
      {hasNo && (
        <polyline
          points={noLine}
          fill="none"
          stroke="#ef4444"
          strokeWidth={noStroke}
          strokeLinejoin="round"
          opacity={noOpacity}
        />
      )}

      {/* Signal time vertical line */}
      {signalIdx >= 0 && (
        <>
          <line
            x1={xPos(signalIdx)}
            x2={xPos(signalIdx)}
            y1={PT}
            y2={H - PB}
            stroke="#eab308"
            strokeWidth="1"
            strokeDasharray="4,3"
          />
          <text x={xPos(signalIdx) + 4} y={PT + 10} fill="#eab308" fontSize="8">
            signal
          </text>
        </>
      )}

      {/* Entry price horizontal line */}
      {entryPrice != null && (
        <>
          <line
            x1={PL}
            x2={W - PR}
            y1={yPos(entryPrice)}
            y2={yPos(entryPrice)}
            stroke="#38bdf8"
            strokeWidth="1"
            strokeDasharray="3,3"
          />
          <text x={W - PR + 2} y={yPos(entryPrice) + 3} fill="#38bdf8" fontSize="8">
            entry
          </text>
        </>
      )}

      {/* Current price horizontal line */}
      {currentPrice != null && (
        <>
          <line
            x1={PL}
            x2={W - PR}
            y1={yPos(currentPrice)}
            y2={yPos(currentPrice)}
            stroke="#a78bfa"
            strokeWidth="1"
            strokeDasharray="3,3"
          />
          <text x={W - PR + 2} y={yPos(currentPrice) + 3} fill="#a78bfa" fontSize="8">
            now
          </text>
        </>
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
// Zoomable Price Chart wrapper
// ---------------------------------------------------------------------------

const ZOOM_LEVELS: { label: string; value: ZoomLevel }[] = [
  { label: "1h", value: "1h" },
  { label: "6h", value: "6h" },
  { label: "24h", value: "24h" },
  { label: "7d", value: "7d" },
  { label: "30d", value: "30d" },
  { label: "All", value: "all" },
];

function ZoomablePriceChart({
  conditionId,
  signalTime,
  entryPrice,
  currentPrice,
  intentOutcome,
  initialPoints,
}: {
  conditionId: string;
  signalTime: number;
  entryPrice: number | null;
  currentPrice: number | null;
  intentOutcome: string;
  initialPoints: PricePoint[];
}) {
  const [zoom, setZoom] = useState<ZoomLevel>("6h");
  const [points, setPoints] = useState<PricePoint[]>(initialPoints);
  const [loading, setLoading] = useState(false);

  const loadPrices = useCallback(
    (z: ZoomLevel) => {
      setLoading(true);
      fetchPriceHistory(conditionId, signalTime, z)
        .then((resp) => {
          // Zoom endpoint returns TradeBubble[] — aggregate to PricePoint[] for this chart
          const byKey = new Map<string, { sum: number; cnt: number; outcome: string }>();
          for (const t of resp.points as TradeBubble[]) {
            const minute = t.ts.slice(0, 16).replace("T", " ");
            const key = `${minute}|${t.outcome}`;
            const cur = byKey.get(key);
            if (cur) { cur.sum += Number(t.price); cur.cnt++; }
            else byKey.set(key, { sum: Number(t.price), cnt: 1, outcome: t.outcome });
          }
          const pts: PricePoint[] = [];
          for (const [key, v] of byKey) {
            const minute = key.split("|")[0];
            pts.push({ minute, outcome: v.outcome, avg_price: v.sum / v.cnt, trades: v.cnt });
          }
          pts.sort((a, b) => a.minute.localeCompare(b.minute));
          setPoints(pts);
        })
        .catch(() => {})
        .finally(() => setLoading(false));
    },
    [conditionId, signalTime],
  );

  // Load on zoom change (skip initial — we already have data from intent detail)
  const handleZoom = (z: ZoomLevel) => {
    setZoom(z);
    loadPrices(z);
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
        <PriceChart
          points={points}
          signalTime={signalTime}
          entryPrice={entryPrice}
          currentPrice={currentPrice}
          intentOutcome={intentOutcome}
        />
      )}
    </div>
  );
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
// Main Page
// ---------------------------------------------------------------------------

export default function IntentDetailPage() {
  const params = useParams();
  const strategy = params.strategy as string;
  const index = Number(params.index);

  const [data, setData] = useState<IntentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchIntentDetail(strategy, index)
      .then((d) => {
        if (d.error) setError(d.error);
        else setData(d);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [strategy, index]);

  if (loading) {
    return (
      <main className="min-h-screen bg-gray-950 text-gray-100 p-6">
        <div className="max-w-5xl mx-auto">
          <div className="text-gray-500">Loading intent detail...</div>
        </div>
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
          <div className="text-red-400 mt-4">{error || "Intent not found"}</div>
        </div>
      </main>
    );
  }

  const { intent, fill, market, current_price, signal_price, orderbook_at_signal, price_history, recent_trades, pnl, resolved_pnl } = data;

  return (
    <main className="min-h-screen bg-gray-950 text-gray-100 p-6">
      <div className="max-w-5xl mx-auto space-y-6">
        {/* Nav */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Link href="/" className="text-blue-400 text-sm hover:underline">
              &larr; Dashboard
            </Link>
            <h1 className="text-xl font-bold">
              Intent #{index}
              <span className="text-gray-500 text-sm font-normal ml-2">
                {strategy} &middot; {data.intent_index + 1} of {data.total_intents}
              </span>
            </h1>
          </div>
          <div className="flex gap-2">
            {index > 0 && (
              <Link
                href={`/intent/${strategy}/${index - 1}`}
                className="px-3 py-1 text-xs bg-gray-800 text-gray-400 rounded hover:bg-gray-700"
              >
                &larr; Prev
              </Link>
            )}
            {index < data.total_intents - 1 && (
              <Link
                href={`/intent/${strategy}/${index + 1}`}
                className="px-3 py-1 text-xs bg-gray-800 text-gray-400 rounded hover:bg-gray-700"
              >
                Next &rarr;
              </Link>
            )}
          </div>
        </div>

        {/* Market question */}
        {market && (
          <div className="bg-gray-900 rounded-lg p-4 border border-gray-800">
            <div className="text-sm text-gray-400 mb-1">{market.category}</div>
            <div className="text-lg font-medium">{market.question}</div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-xs text-gray-500">
              <span>Created: {fmtDt(market.created_at)}</span>
              {market.end_date && !market.resolved_at && (
                <span>
                  Resolves: {fmtDt(market.end_date)} &middot;{" "}
                  <span className={
                    new Date(market.end_date + "Z").getTime() < Date.now()
                      ? "text-red-400 font-medium"
                      : "text-yellow-400 font-medium"
                  }>
                    {timeLeft(market.end_date)}
                  </span>
                </span>
              )}
              {market.resolved_at && (
                <span>
                  Resolved: {fmtDt(market.resolved_at)} &middot;{" "}
                  <span className={market.winner_outcome === intent.outcome ? "text-green-400" : "text-red-400"}>
                    Winner: {market.winner_outcome}
                  </span>
                </span>
              )}
              {market.closed_at && !market.resolved_at && (
                <span>Closed: {fmtDt(market.closed_at)}</span>
              )}
            </div>
          </div>
        )}

        {/* Top cards: intent + fill + PnL */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* Intent */}
          <div className="bg-gray-900 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
              Intent
            </h3>
            <Info label="Strategy">{intent.strategy}</Info>
            <Info label="Time">
              {fmtTs(intent.signal_time)}{" "}
              <span className="text-gray-500 text-xs">({ago(intent.signal_time)})</span>
            </Info>
            <Info label="Side">
              <span className={intent.side === "BUY" ? "text-green-400" : "text-red-400"}>
                {intent.side}
              </span>
            </Info>
            <Info label="Outcome">
              <span className={intent.outcome === "YES" ? "text-green-400" : "text-red-400"}>
                {intent.outcome}
              </span>
            </Info>
            <Info label="Size">{usdFmt(intent.size_usd)}</Info>
            <Info label="Max Price">{priceFmt(intent.max_price)}</Info>
            <Info label="Urgency">{intent.urgency}</Info>
            <div className="mt-3 text-xs text-gray-500 bg-gray-800/50 rounded p-2">
              {intent.reason}
            </div>
          </div>

          {/* Fill */}
          <div className="bg-gray-900 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
              Fill
            </h3>
            {fill ? (
              <>
                <Info label="Status">
                  <span
                    className={
                      fill.status === "filled"
                        ? "text-green-400"
                        : fill.status === "rejected"
                        ? "text-red-400"
                        : "text-yellow-400"
                    }
                  >
                    {fill.status}
                  </span>
                </Info>
                <Info label="Fill Price">{priceFmt(fill.filled_price)}</Info>
                <Info label="Fill Size">{usdFmt(fill.filled_size_usd)}</Info>
                <Info label="Fee">{usdFmt(fill.fee_usd)}</Info>
                <Info label="Fill Time">
                  {fmtTs(fill.filled_at)}{" "}
                  <span className="text-gray-500 text-xs">
                    (+{((fill.filled_at - intent.signal_time)).toFixed(1)}s)
                  </span>
                </Info>
                {fill.error && (
                  <div className="mt-3 text-xs text-red-400 bg-red-900/20 rounded p-2">
                    {fill.error}
                  </div>
                )}
                {fill.order_id && (
                  <Info label="Order ID">
                    <span className="text-xs">{fill.order_id}</span>
                  </Info>
                )}
              </>
            ) : (
              <div className="text-gray-600 text-sm py-4">No matching fill</div>
            )}
          </div>

          {/* PnL */}
          <div className="bg-gray-900 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
              P&amp;L
            </h3>
            {resolved_pnl ? (
              <>
                <Info label="Resolution">
                  <span className={resolved_pnl.won ? "text-green-400" : "text-red-400"}>
                    {resolved_pnl.won ? "WON" : "LOST"} (winner: {resolved_pnl.winner})
                  </span>
                </Info>
                <Info label="Cost">{usdFmt(resolved_pnl.cost)}</Info>
                <Info label="Payout">{usdFmt(resolved_pnl.payout)}</Info>
                <Info label="Realized PnL">
                  <span className={resolved_pnl.realized_pnl >= 0 ? "text-green-400" : "text-red-400"}>
                    {usdFmt(resolved_pnl.realized_pnl)}
                  </span>
                </Info>
              </>
            ) : pnl ? (
              <>
                <Info label="Entry">{priceFmt(pnl.entry_price)}</Info>
                <Info label="Current">{priceFmt(pnl.current_price)}</Info>
                <Info label="Shares">{pnl.shares.toFixed(2)}</Info>
                <Info label="Cost">{usdFmt(pnl.cost)}</Info>
                <Info label="Current Value">{usdFmt(pnl.current_value)}</Info>
                <Info label="Unrealized PnL">
                  <span className={pnl.unrealized_pnl >= 0 ? "text-green-400" : "text-red-400"}>
                    {usdFmt(pnl.unrealized_pnl)} ({pnl.pnl_pct > 0 ? "+" : ""}{pnl.pnl_pct}%)
                  </span>
                </Info>
              </>
            ) : (
              <div className="text-gray-600 text-sm py-4">
                {fill?.status === "filled" ? "No price data for PnL" : "No fill \u2014 no PnL"}
              </div>
            )}
          </div>
        </div>

        {/* Price context at signal */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="bg-gray-900 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
              Price at Signal
            </h3>
            {signal_price ? (
              <>
                <Info label="Price">{priceFmt(signal_price.price)}</Info>
                <Info label="Time">{fmtDt(signal_price.timestamp)}</Info>
              </>
            ) : (
              <div className="text-gray-600 text-sm">No trade data near signal time</div>
            )}
            {current_price && (
              <>
                <div className="mt-3 mb-2 border-t border-gray-800" />
                <Info label="Current Price">{priceFmt(current_price.price)}</Info>
                <Info label="Last Trade">{fmtDt(current_price.timestamp)}</Info>
                <Info label="Side / Size">
                  {current_price.side} &middot; {usdFmt(current_price.amount_usd)}
                </Info>
              </>
            )}
          </div>

          <div className="bg-gray-900 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
              Orderbook at Signal
            </h3>
            {orderbook_at_signal ? (
              <>
                <Info label="Best Bid">{priceFmt(orderbook_at_signal.best_bid)}</Info>
                <Info label="Best Ask">{priceFmt(orderbook_at_signal.best_ask)}</Info>
                <Info label="Spread">
                  {(
                    (orderbook_at_signal.best_ask - orderbook_at_signal.best_bid) *
                    100
                  ).toFixed(1)}
                  %
                </Info>
                <Info label="Bid Depth">{usdFmt(orderbook_at_signal.bid_depth_usd)}</Info>
                <Info label="Ask Depth">{usdFmt(orderbook_at_signal.ask_depth_usd)}</Info>
                <Info label="Snapshot Time">{fmtDt(orderbook_at_signal.timestamp)}</Info>
              </>
            ) : (
              <div className="text-gray-600 text-sm">No orderbook snapshot near signal time</div>
            )}
          </div>
        </div>

        {/* Price chart with zoom */}
        <ZoomablePriceChart
          conditionId={intent.condition_id}
          signalTime={intent.signal_time}
          entryPrice={fill?.filled_price ?? null}
          currentPrice={current_price ? Number(current_price.price) : null}
          intentOutcome={intent.outcome}
          initialPoints={price_history}
        />

        {/* Recent trades */}
        <div className="bg-gray-900 rounded-lg p-4">
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
            Recent Trades (this market)
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 text-left border-b border-gray-800">
                  <th className="py-2 px-3 font-medium">Time</th>
                  <th className="py-2 px-3 font-medium text-right">Price</th>
                  <th className="py-2 px-3 font-medium text-center">Side</th>
                  <th className="py-2 px-3 font-medium text-center">Outcome</th>
                  <th className="py-2 px-3 font-medium text-right">Amount</th>
                  <th className="py-2 px-3 font-medium">Source</th>
                </tr>
              </thead>
              <tbody>
                {recent_trades.map((t, i) => (
                  <tr key={i} className="border-b border-gray-800/50">
                    <td className="py-1.5 px-3 text-xs font-mono text-gray-400">
                      {fmtDt(t.timestamp)}
                    </td>
                    <td className="py-1.5 px-3 text-right font-mono text-gray-200">
                      {priceFmt(t.price)}
                    </td>
                    <td className="py-1.5 px-3 text-center">
                      <span
                        className={`text-xs font-semibold ${
                          t.side === "BUY" ? "text-green-400" : "text-red-400"
                        }`}
                      >
                        {t.side}
                      </span>
                    </td>
                    <td className="py-1.5 px-3 text-center">
                      {t.outcome ? (
                        <span
                          className={`text-xs font-medium ${
                            t.outcome === "YES"
                              ? "text-green-400"
                              : "text-red-400"
                          }`}
                        >
                          {t.outcome}
                        </span>
                      ) : (
                        <span className="text-gray-600">{"\u2014"}</span>
                      )}
                    </td>
                    <td className="py-1.5 px-3 text-right font-mono text-gray-300">
                      {usdFmt(t.amount_usd)}
                    </td>
                    <td className="py-1.5 px-3 text-xs text-gray-500">{t.source}</td>
                  </tr>
                ))}
                {recent_trades.length === 0 && (
                  <tr>
                    <td colSpan={6} className="py-4 text-center text-gray-600">
                      No trades found
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Condition ID / Asset ID */}
        <div className="bg-gray-900 rounded-lg p-4 text-xs text-gray-500 font-mono space-y-1">
          <div>condition_id: {intent.condition_id}</div>
          {intent.asset_id && <div>asset_id: {intent.asset_id}</div>}
        </div>
      </div>
    </main>
  );
}
