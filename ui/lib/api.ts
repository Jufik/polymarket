const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
