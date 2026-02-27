"use client";

interface DataPoint {
  hour: string;
  source: string;
  trades: number;
}

interface Props {
  data: DataPoint[];
}

const SOURCE_COLORS: Record<string, string> = {
  alchemy: "#3b82f6",          // blue
  rtds: "#22c55e",             // green
  goldsky_subgraph: "#f59e0b", // amber
};

const SOURCE_ORDER = ["alchemy", "rtds", "goldsky_subgraph"];

function formatK(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k`;
  return String(n);
}

export default function ReconciliationChart({ data }: Props) {
  if (data.length === 0) {
    return (
      <div className="text-sm text-gray-500 p-8 text-center">
        No trade data in the last 24 hours
      </div>
    );
  }

  // Group by hour
  const hourMap = new Map<string, Record<string, number>>();
  for (const d of data) {
    const existing = hourMap.get(d.hour) || {};
    existing[d.source] = (existing[d.source] || 0) + d.trades;
    hourMap.set(d.hour, existing);
  }

  const hours = Array.from(hourMap.keys()).sort();
  const allSources = Array.from(new Set(data.map((d) => d.source))).sort(
    (a, b) => SOURCE_ORDER.indexOf(a) - SOURCE_ORDER.indexOf(b)
  );

  // Per-source max for Y-axis
  let maxVal = 0;
  for (const sources of hourMap.values()) {
    for (const v of Object.values(sources)) {
      if (v > maxVal) maxVal = v;
    }
  }
  maxVal = maxVal || 1;

  // Chart dimensions
  const width = 700;
  const height = 220;
  const padTop = 12;
  const padBottom = 40;
  const padLeft = 52;
  const padRight = 16;
  const chartW = width - padLeft - padRight;
  const chartH = height - padTop - padBottom;

  const xFor = (i: number) => padLeft + (i / Math.max(hours.length - 1, 1)) * chartW;
  const yFor = (v: number) => padTop + chartH - (v / maxVal) * chartH;

  // Y-axis ticks
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(maxVal * f));

  const formatHour = (iso: string) => {
    const d = new Date(iso + "Z");
    return `${d.getUTCHours().toString().padStart(2, "0")}:00`;
  };

  // Build polyline points + circle data per source
  const sourceLines = allSources.map((s) => {
    const points: string[] = [];
    const circles: { x: number; y: number; val: number; hour: string }[] = [];
    hours.forEach((h, i) => {
      const val = hourMap.get(h)?.[s] ?? 0;
      const x = xFor(i);
      const y = yFor(val);
      points.push(`${x},${y}`);
      circles.push({ x, y, val, hour: h });
    });
    return { source: s, points: points.join(" "), circles };
  });

  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full max-w-3xl">
        {/* Y-axis grid + labels */}
        {yTicks.map((tick) => {
          const y = yFor(tick);
          return (
            <g key={tick}>
              <line
                x1={padLeft}
                x2={width - padRight}
                y1={y}
                y2={y}
                stroke="#374151"
                strokeWidth="0.5"
              />
              <text
                x={padLeft - 8}
                y={y + 3}
                textAnchor="end"
                className="fill-gray-500"
                fontSize="10"
              >
                {formatK(tick)}
              </text>
            </g>
          );
        })}

        {/* Lines per source */}
        {sourceLines.map(({ source, points }) => (
          <polyline
            key={source}
            points={points}
            fill="none"
            stroke={SOURCE_COLORS[source] || "#6b7280"}
            strokeWidth="2"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}

        {/* Data points with tooltips */}
        {sourceLines.map(({ source, circles }) =>
          circles.map((c, i) => (
            <circle
              key={`${source}-${i}`}
              cx={c.x}
              cy={c.y}
              r="3"
              fill={SOURCE_COLORS[source] || "#6b7280"}
              stroke="#111827"
              strokeWidth="1"
              opacity={c.val > 0 ? 1 : 0}
            >
              <title>
                {formatHour(c.hour)} | {source}: {c.val.toLocaleString()} trades
              </title>
            </circle>
          ))
        )}

        {/* X-axis labels */}
        {hours.map((h, i) => {
          if (hours.length > 12 && i % 3 !== 0 && i !== hours.length - 1)
            return null;
          if (hours.length <= 12 || i % 3 === 0 || i === hours.length - 1) {
            return (
              <text
                key={`x-${h}`}
                x={xFor(i)}
                y={height - padBottom + 16}
                textAnchor="middle"
                className="fill-gray-400"
                fontSize="10"
              >
                {formatHour(h)}
              </text>
            );
          }
          return null;
        })}
      </svg>

      {/* Legend */}
      <div className="flex gap-6 mt-2 ml-12">
        {allSources.map((s) => {
          // Sum total for this source
          const total = data
            .filter((d) => d.source === s)
            .reduce((sum, d) => sum + d.trades, 0);
          return (
            <div key={s} className="flex items-center gap-1.5 text-xs text-gray-400">
              <div
                className="w-3 h-[3px] rounded-full"
                style={{ backgroundColor: SOURCE_COLORS[s] || "#6b7280" }}
              />
              <span>{s}</span>
              <span className="font-mono text-gray-500">{formatK(total)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
