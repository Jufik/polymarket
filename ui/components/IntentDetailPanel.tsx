"use client";

import { useEffect, useRef, useState } from "react";
import { fetchIntentDetail, IntentDetail } from "@/lib/api";

// Client-side cache with 60s TTL
const detailCache = new Map<number, { data: IntentDetail; ts: number }>();
const CACHE_TTL_MS = 60_000;

function Sparkline({ prices }: { prices: { ts: string; price: number }[] }) {
  if (prices.length < 2) {
    return <div className="text-xs text-gray-500">No price data</div>;
  }

  const width = 200;
  const height = 48;
  const padding = 2;

  const values = prices.map((p) => p.price);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 0.01;

  const points = values.map((v, i) => {
    const x = padding + (i / (values.length - 1)) * (width - 2 * padding);
    const y = height - padding - ((v - min) / range) * (height - 2 * padding);
    return `${x},${y}`;
  });

  const trending = values[values.length - 1] >= values[0];
  const color = trending ? "#4ade80" : "#f87171";

  return (
    <svg width={width} height={height} className="block">
      <polyline
        points={points.join(" ")}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function formatCountdown(endDate: string): string {
  const diff = new Date(endDate).getTime() - Date.now();
  if (diff <= 0) return "Ended";
  const d = Math.floor(diff / 86400000);
  const h = Math.floor((diff % 86400000) / 3600000);
  const m = Math.floor((diff % 3600000) / 60000);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function OrderbookGrid({
  ob,
}: {
  ob: { best_bid: number; best_ask: number; spread: number; source: string };
}) {
  return (
    <div>
      <div className="text-xs text-gray-400 mb-1">Orderbook at Signal</div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
        <div className="text-gray-500">Bid</div>
        <div className="text-right font-mono text-green-400">
          ${ob.best_bid.toFixed(4)}
        </div>
        <div className="text-gray-500">Ask</div>
        <div className="text-right font-mono text-red-400">
          ${ob.best_ask.toFixed(4)}
        </div>
        <div className="text-gray-500">Spread</div>
        <div className="text-right font-mono text-gray-300">
          ${ob.spread.toFixed(4)}
        </div>
        <div className="text-gray-500">Source</div>
        <div className="text-right text-gray-400">{ob.source}</div>
      </div>
    </div>
  );
}

function RationaleGrid({ rationale }: { rationale: Record<string, unknown> }) {
  const entries = Object.entries(rationale).filter(
    ([k]) => k !== "strategy"
  );
  if (entries.length === 0) return null;

  return (
    <div>
      <div className="text-xs text-gray-400 mb-1">Rationale</div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
        {entries.map(([key, value]) => (
          <div key={key} className="contents">
            <div className="text-gray-500">{key.replace(/_/g, " ")}</div>
            <div className="text-right font-mono text-gray-300 truncate max-w-[140px]">
              {Array.isArray(value)
                ? value.join(", ")
                : value === null
                ? "-"
                : String(value)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

interface Props {
  intentId: number;
  anchorRect: DOMRect | null;
}

export default function IntentDetailPanel({ intentId, anchorRect }: Props) {
  const [detail, setDetail] = useState<IntentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setLoading(true);
    setDetail(null);

    // Check cache first (with TTL)
    const cached = detailCache.get(intentId);
    if (cached && Date.now() - cached.ts < CACHE_TTL_MS) {
      setDetail(cached.data);
      setLoading(false);
      return;
    }

    // 200ms debounce
    debounceRef.current = setTimeout(async () => {
      try {
        const data = await fetchIntentDetail(intentId);
        detailCache.set(intentId, { data, ts: Date.now() });
        setDetail(data);
      } catch {
        /* ignore fetch errors */
      } finally {
        setLoading(false);
      }
    }, 200);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [intentId]);

  if (!anchorRect) return null;

  // Position: right of the row, vertically centered
  const left = anchorRect.right + 12;

  return (
    <div
      className="fixed z-50 bg-gray-800 border border-gray-700 rounded-lg shadow-xl p-4 w-72"
      style={{ top: anchorRect.top - 40, left: Math.min(left, window.innerWidth - 300) }}
    >
      {loading ? (
        <div className="text-sm text-gray-400 animate-pulse">Loading...</div>
      ) : detail ? (
        <div className="space-y-3">
          {/* Market question */}
          <div className="text-sm font-medium text-gray-200 leading-tight">
            {detail.question || detail.condition_id.slice(0, 16) + "..."}
          </div>

          {/* Sparkline */}
          <div>
            <div className="text-xs text-gray-400 mb-1">48h Price</div>
            <Sparkline prices={detail.price_history} />
          </div>

          {/* PnL */}
          {detail.pnl ? (
            <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
              <div className="text-gray-400">Entry</div>
              <div className="text-right font-mono">
                ${detail.pnl.entry_price.toFixed(4)}
              </div>
              <div className="text-gray-400">Current</div>
              <div className="text-right font-mono">
                ${detail.pnl.current_price.toFixed(4)}
              </div>
              <div className="text-gray-400">PnL</div>
              <div
                className={`text-right font-mono font-semibold ${
                  detail.pnl.pnl_usd >= 0 ? "text-green-400" : "text-red-400"
                }`}
              >
                {detail.pnl.pnl_usd >= 0 ? "+" : ""}
                ${detail.pnl.pnl_usd.toFixed(2)} ({detail.pnl.pnl_pct.toFixed(1)}%)
              </div>
            </div>
          ) : (
            detail.current_price != null && (
              <div className="text-xs">
                <span className="text-gray-400">Current: </span>
                <span className="font-mono">${detail.current_price.toFixed(4)}</span>
              </div>
            )
          )}

          {/* Orderbook at signal */}
          {detail.metadata?.orderbook && (
            <div className="border-t border-gray-700 pt-2">
              <OrderbookGrid ob={detail.metadata.orderbook} />
            </div>
          )}

          {/* Rationale */}
          {detail.metadata?.rationale && (
            <div className="border-t border-gray-700 pt-2">
              <RationaleGrid rationale={detail.metadata.rationale} />
            </div>
          )}

          {/* Resolution */}
          <div className="text-xs border-t border-gray-700 pt-2">
            {detail.resolution.resolved_at ? (
              <span className="text-yellow-400">
                Resolved: {detail.resolution.winner_outcome || "N/A"}
              </span>
            ) : detail.resolution.end_date ? (
              new Date(detail.resolution.end_date).getTime() <= Date.now() ? (
                <span className="text-orange-400">Awaiting resolution</span>
              ) : (
                <span className="text-gray-400">
                  Ends in{" "}
                  <span className="text-gray-200 font-mono">
                    {formatCountdown(detail.resolution.end_date)}
                  </span>
                </span>
              )
            ) : (
              <span className="text-gray-500">No end date</span>
            )}
          </div>
        </div>
      ) : (
        <div className="text-sm text-gray-500">Failed to load</div>
      )}
    </div>
  );
}
