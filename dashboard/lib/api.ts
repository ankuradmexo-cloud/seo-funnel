const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`${init?.method ?? "GET"} ${path} failed: ${res.status} ${await res.text()}`);
  }
  return res.json();
}

export type StatusCounts = {
  deduped: number; validated: number; judged: number;
  shortlisted: number; queued: number; published: number;
};

export type SiteOverview = {
  website_id: number;
  name: string;
  category: string;
  active: boolean;
  status_counts: StatusCounts;
  awaiting_publish: number;
  in_progress: number;
  published: number;
  shortlisted_last_24h: number;
  daily_target: number;
  niches: { active: number; exhausted: number; paused: number };
  last_run: null | {
    run_id: number; status: string; started_at: string; finished_at: string | null;
    candidates_found: number; shortlisted_count: number; error_message: string | null;
  };
};

export type Overview = {
  automation_enabled: boolean;
  daily_target_per_site: number;
  websites: SiteOverview[];
};

export type Keyword = {
  keyword_id: number; keyword: string; status: string; niche_id: number;
  niches?: { name: string } | null;
  search_volume: number | null; cpc: number | null;
  competition: number | null; difficulty: number | null;
  intents: string[] | null; history_trend: Record<string, number> | null;
  judge_score: number | null; judge_rationale: string | null;
  intent_cluster: string | null; run_id: number | null;
  last_updated: string; target_url: string | null;
};

export type Run = {
  run_id: number; website_id: number; niche_id: number | null; status: string;
  candidates_found: number; shortlisted_count: number;
  error_message: string | null; started_at: string; finished_at: string | null;
};

export type Niche = {
  niche_id: number; website_id: number; name: string; status: string;
  source: string; times_processed: number; last_processed_at: string | null;
};

export type UsageTotal = {
  provider: string; total_calls: number; total_credits: number;
  total_tokens: number; runs: number;
  avg_credits_per_run: number | null; avg_calls_per_run: number | null;
  unit_cost_usd: number | null; total_cost_usd: number | null;
  avg_cost_per_run_usd: number | null;
};

export type UsageEndpoint = {
  provider: string; endpoint: string; total_calls: number;
  total_credits: number; total_tokens: number; runs: number;
};

export const api = {
  usage: () => req<{ totals: UsageTotal[]; by_endpoint: UsageEndpoint[] }>("/usage"),
  overview: () => req<Overview>("/overview"),
  websites: () => req<SiteOverview[]>("/websites"),
  updateWebsite: (id: number, body: Partial<{ name: string; category: string; active: boolean }>) =>
    req(`/websites/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  automation: () => req<{ enabled: boolean }>("/automation"),
  setAutomation: (enabled: boolean) =>
    req<{ enabled: boolean }>("/automation", { method: "POST", body: JSON.stringify({ enabled }) }),
  keywords: (params: Record<string, string | number | undefined>) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && q.set(k, String(v)));
    return req<Keyword[]>(`/keywords?${q}`);
  },
  runs: (websiteId?: number) =>
    req<Run[]>(`/runs${websiteId ? `?website_id=${websiteId}` : ""}`),
  niches: (websiteId: number) => req<Niche[]>(`/niches?website_id=${websiteId}`),
};
