const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ||
  (typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8001`
    : "http://localhost:8001");

export interface Position {
  condition_id: string;
  asset_id: string;
  side: string;
  size: number;
  avg_entry: number;
  cost_basis: number;
  unrealized_pnl: number;
  last_price: number;
}

export interface Fill {
  id: number;
  intent_id: string;
  strategy: string;
  condition_id: string;
  side: string;
  outcome: string;
  price: number;
  size_usd: number;
  fee_usd: number;
  filled_at: string;
}

export interface PanicResult {
  triggered: boolean;
  total_closed: number;
  total_failed: number;
  results: { order_id: string; success: boolean; error: string | null }[];
}

export async function fetchPositions(): Promise<{
  positions: Position[];
  total_exposure: number;
  count: number;
}> {
  const res = await fetch(`${API_BASE}/api/positions`, { cache: "no-store" });
  return res.json();
}

export async function fetchFills(
  limit = 50,
  offset = 0
): Promise<{ fills: Fill[]; total: number }> {
  const res = await fetch(
    `${API_BASE}/api/fills?limit=${limit}&offset=${offset}`,
    { cache: "no-store" }
  );
  return res.json();
}

export async function fetchHealth(): Promise<{ status: string; pg: string }> {
  const res = await fetch(`${API_BASE}/api/health`, { cache: "no-store" });
  return res.json();
}

export async function triggerPanic(): Promise<PanicResult> {
  const res = await fetch(`${API_BASE}/api/panic`, {
    method: "POST",
    cache: "no-store",
  });
  return res.json();
}

export interface Intent {
  id: number;
  strategy: string;
  condition_id: string;
  side: string;
  outcome: string;
  size_usd: number;
  max_price: number | null;
  reason: string;
  disposition: string;
  rejection_reason: string;
  filled_price: number | null;
  filled_size_usd: number | null;
  fee_usd: number | null;
  captured_at: string;
  question: string | null;
  event_slug: string | null;
  end_date: string | null;
  resolved_at: string | null;
  winner_outcome: string | null;
}

export interface IntentDetailPnL {
  entry_price: number;
  current_price: number;
  pnl_usd: number;
  pnl_pct: number;
}

export interface IntentDetailResolution {
  end_date: string | null;
  resolved_at: string | null;
  winner_outcome: string | null;
}

export interface IntentDetail {
  id: number;
  strategy: string;
  condition_id: string;
  question: string | null;
  side: string;
  outcome: string;
  size_usd: number;
  filled_price: number | null;
  filled_size_usd: number | null;
  disposition: string;
  price_history: { ts: string; price: number }[];
  current_price: number | null;
  pnl: IntentDetailPnL | null;
  resolution: IntentDetailResolution;
  metadata?: IntentMetadata;
}

export async function fetchIntents(
  limit = 50,
  offset = 0,
  strategy?: string,
  disposition?: string
): Promise<{ intents: Intent[]; total: number }> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (strategy) params.set("strategy", strategy);
  if (disposition) params.set("disposition", disposition);
  const res = await fetch(`${API_BASE}/api/intents?${params}`, {
    cache: "no-store",
  });
  return res.json();
}

export async function fetchIntentDetail(
  id: number
): Promise<IntentDetail> {
  const res = await fetch(`${API_BASE}/api/intents/${id}/detail`, {
    cache: "no-store",
  });
  return res.json();
}

export interface DataHealth {
  broken_count: number;
  broken_assets: number;
  total_trades: number;
}

export interface ReconciliationPoint {
  hour: string;
  source: string;
  trades: number;
}

export interface AnalyticsData {
  data_health: DataHealth;
  reconciliation: ReconciliationPoint[];
}

export async function fetchAnalytics(): Promise<AnalyticsData> {
  const res = await fetch(`${API_BASE}/api/analytics`, { cache: "no-store" });
  return res.json();
}

// --- Markets ---

export interface WillNoMarket {
  condition_id: string;
  question: string;
  category: string | null;
  keywords_matched: string[];
  end_date: string | null;
  event_slug: string | null;
  polymarket_url: string | null;
}

export interface PoolTrader {
  strategy: string;
  trader_address: string;
  n_trades: number;
  n_markets: number;
  volume_usd: number;
  refreshed_at: string | null;
}

export async function fetchWillNoMarkets(): Promise<{
  markets: WillNoMarket[];
  total: number;
}> {
  const res = await fetch(`${API_BASE}/api/markets/will_no`, {
    cache: "no-store",
  });
  return res.json();
}

export async function fetchPoolTraders(
  strategy?: string
): Promise<{ traders: PoolTrader[]; total: number }> {
  const params = new URLSearchParams();
  if (strategy) params.set("strategy", strategy);
  const qs = params.toString();
  const res = await fetch(
    `${API_BASE}/api/markets/pool${qs ? `?${qs}` : ""}`,
    { cache: "no-store" }
  );
  return res.json();
}

// --- Intent detail metadata ---

export interface IntentMetadata {
  orderbook?: {
    best_bid: number;
    best_ask: number;
    spread: number;
    source: string;
  };
  rationale?: Record<string, unknown>;
}
