const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ||
  (typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8001`
    : "http://localhost:8001");

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  return res.json();
}

// -- Health & Metrics --------------------------------------------------------

export interface Metrics {
  window_s: number;
  requests: number;
  req_per_s: number;
}

export const fetchHealth = () => get<{ status: string }>("/api/health");
export const fetchMetrics = () => get<Metrics>("/api/metrics");

// -- Ingestion ---------------------------------------------------------------

export interface SourceRow {
  source: string;
  trades: string;
  tps: number;
  latest: string;
}

export interface TpsPoint {
  minute: string;
  source: string;
  cnt: string;
}

export interface IngestionData {
  hours: number;
  by_source: SourceRow[];
  total: { trades: string; tps: number; latest: string; earliest: string };
  tps_series: TpsPoint[];
}

export const fetchIngestion = (hours = 1) =>
  get<IngestionData>(`/api/ingestion?hours=${hours}`);

// -- Metadata ----------------------------------------------------------------

export interface MetadataData {
  markets: { total_markets: string; resolved: string; open: string };
  tokens: string | number;
  last_update: string | null;
  recent_markets: { condition_id: string; question: string; created_at: string; category: string }[];
}

export const fetchMetadata = () => get<MetadataData>("/api/metadata");

// -- Strategies & Activity ---------------------------------------------------

export const fetchStrategies = () =>
  get<{ strategies: string[] }>("/api/strategies");

export interface IntentRecord {
  strategy: string;
  condition_id: string;
  side: string;
  outcome: string;
  size_usd: number;
  urgency: string;
  max_price: number | null;
  reason: string;
  signal_time: number;
  asset_id: string | null;
  _source: string;
  _index: number;
}

export interface FillRecord {
  intent_id: string;
  strategy: string;
  condition_id: string;
  side: string;
  outcome: string;
  filled_price: number;
  filled_size_usd: number;
  fee_usd: number;
  status: string;
  filled_at: number;
  error: string | null;
  order_id: string | null;
  _source: string;
}

export const fetchIntents = (strategy?: string, n = 100) => {
  const params = new URLSearchParams({ n: String(n) });
  if (strategy) params.set("strategy", strategy);
  return get<{ count: number; intents: IntentRecord[] }>(`/api/intents?${params}`);
};

export const fetchFills = (strategy?: string, n = 100) => {
  const params = new URLSearchParams({ n: String(n) });
  if (strategy) params.set("strategy", strategy);
  return get<{ count: number; fills: FillRecord[] }>(`/api/fills?${params}`);
};

export interface StrategyActivity {
  total_intents: number;
  total_fills: number;
  total_rejected: number;
  total_filled_usd: number;
  recent_intents: IntentRecord[];
  recent_fills: FillRecord[];
  config?: string;
}

export const fetchActivity = (strategy?: string) => {
  const params = strategy ? `?strategy=${strategy}` : "";
  return get<Record<string, StrategyActivity>>(`/api/activity${params}`);
};

// -- Intent Detail -----------------------------------------------------------

export interface PnlInfo {
  entry_price: number;
  current_price: number;
  shares: number;
  cost: number;
  current_value: number;
  unrealized_pnl: number;
  pnl_pct: number;
}

export interface ResolvedPnl {
  winner: string;
  won: boolean;
  payout: number;
  cost: number;
  realized_pnl: number;
}

export interface PricePoint {
  minute: string;
  outcome: string;
  avg_price: number;
  trades: number;
}

export interface TradeBubble {
  ts: string;
  outcome: string;
  price: number;
  size_usd: number;
  side: string;
}

export interface OBPoint {
  ts: string;
  bid: number;
  ask: number;
}

export interface TradeRow {
  price: number;
  side: string;
  amount_usd: number;
  timestamp: string;
  source?: string;
  outcome?: string;
}

export interface OrderbookSnapshot {
  best_bid: number;
  best_ask: number;
  bid_depth_usd: number;
  ask_depth_usd: number;
  timestamp: string;
}

export interface MarketInfo {
  condition_id: string;
  question: string;
  category: string;
  status: string;
  resolution_value: string | null;
  winner_outcome: string | null;
  created_at: string | null;
  closed_at: string | null;
  resolved_at: string | null;
  end_date: string | null;
}

export interface IntentDetail {
  intent: IntentRecord;
  intent_index: number;
  total_intents: number;
  fill: FillRecord | null;
  market: MarketInfo | null;
  current_price: TradeRow | null;
  signal_price: { price: number; timestamp: string } | null;
  orderbook_at_signal: OrderbookSnapshot | null;
  price_history: PricePoint[];
  recent_trades: TradeRow[];
  pnl: PnlInfo | null;
  resolved_pnl: ResolvedPnl | null;
  error?: string;
}

export const fetchIntentDetail = (strategy: string, index: number) =>
  get<IntentDetail>(`/api/intent/${strategy}/${index}`);

// -- Round Trips -------------------------------------------------------------

export interface RoundTrip {
  config: string;
  strategy: string;
  condition_id: string;
  outcome: string;
  entry_time: number;
  entry_price: number | null;
  entry_size: number;
  exit_time: number | null;
  exit_price: number | null;
  hold_s: number | null;
  pnl: number | null;
  status: string;
  reason: string;
  exit_reason: string;
}

export interface RoundTripSummary {
  total_trips: number;
  closed: number;
  open: number;
  total_pnl: number;
  wins: number;
  losses: number;
  hit_rate: number;
  avg_hold_s: number;
}

export interface RoundTripsResponse {
  count: number;
  roundtrips: RoundTrip[];
  summary: RoundTripSummary;
}

export const fetchRoundTrips = (strategy?: string, n = 50) => {
  const params = new URLSearchParams({ n: String(n) });
  if (strategy) params.set("strategy", strategy);
  return get<RoundTripsResponse>(`/api/roundtrips?${params}`);
};

// -- Round Trip Detail -------------------------------------------------------

export interface RoundTripDetail {
  config: string;
  strategy: string;
  condition_id: string;
  outcome: string;
  buy_intent: IntentRecord;
  sell_intent: IntentRecord | null;
  buy_fill: FillRecord | null;
  sell_fill: FillRecord | null;
  market: MarketInfo | null;
  price_history: TradeBubble[];
  ob_series: OBPoint[];
  ob_at_entry: OrderbookSnapshot | null;
  ob_at_exit: OrderbookSnapshot | null;
  pnl: number | null;
  hold_s: number | null;
  error?: string;
}

export const fetchRoundTripDetail = (config: string, conditionId: string) =>
  get<RoundTripDetail>(`/api/roundtrip/${config}/${conditionId}`);

export type ZoomLevel = "1m" | "2m" | "5m" | "15m" | "30m" | "1h" | "6h" | "24h" | "7d" | "30d" | "all";

export interface PriceHistoryResponse {
  zoom: string;
  signal_time: number;
  points: TradeBubble[];
  ob_series: OBPoint[];
}

export const fetchPriceHistory = (
  conditionId: string,
  signalTime: number,
  zoom: ZoomLevel = "6h",
) =>
  get<PriceHistoryResponse>(
    `/api/prices/${conditionId}?signal_time=${signalTime}&zoom=${zoom}`,
  );

// -- Introspection & Candidates ----------------------------------------------

export interface IntrospectServer {
  config: string;
  port: number;
  status: "up" | "down";
  data?: Record<string, unknown>;
}

export interface Candidate {
  strategy: string;
  condition_id: string;
  question: string | null;
  n_traders: number;
  n_threshold: number;
  distance: number;
  consensus_direction: string;
  direction_filter: string;
  would_pass_filter: boolean;
  yes_usd: number;
  no_usd: number;
  signaled: boolean;
  traders: Record<string, unknown>;
}

export const fetchIntrospect = () =>
  get<{ servers: IntrospectServer[] }>("/api/introspect");

export const fetchCandidates = (config: string) =>
  get<{ candidates: Candidate[] }>(`/api/introspect/${config}/candidates`);
