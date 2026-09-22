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
  domain: string | null;
  wp_base_url: string | null;
  wp_username: string | null;
  wp_app_password: string | null;
  seo_plugin: "yoast" | "rankmath" | "none";
  articles_per_day: number;
  article_automation_enabled: boolean;
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

export type BacklinkGapJob = {
  job_id: number; keyword_id: number; website_id: number;
  status: "running" | "success" | "failed";
  error_message: string | null;
  started_at: string; finished_at: string | null;
};

export type BacklinkSampleLink = {
  linked_to_competitor: string; from_page: string;
  from_page_title: string | null; anchor: string | null; nofollow: boolean | null;
};

export type BacklinkCandidate = {
  candidate_id: number; job_id: number; keyword_id: number; website_id: number;
  referring_domain: string; domain_inlink_rank: number | null;
  competitors_linked_count: number | null;
  sample_links: BacklinkSampleLink[];
  outreach_draft: string | null;
  status: "new" | "contacted" | "replied" | "linked" | "rejected";
  found_at: string;
};

export type OffpageChannel = "directory" | "guest_post" | "social";

export type OffpageJob = {
  job_id: number; website_id: number; channel: OffpageChannel;
  status: "running" | "success" | "failed";
  error_message: string | null;
  started_at: string; finished_at: string | null;
};

export type OffpageOpportunity = {
  opportunity_id: number; job_id: number; website_id: number; channel: OffpageChannel;
  target_url: string | null; target_domain: string | null; title: string | null;
  signal_summary: string | null; contact_info: string | null; outreach_draft: string | null;
  status: "new" | "contacted" | "replied" | "won" | "rejected";
  found_at: string;
};

export type PublishedArticle = {
  article_id: number; keyword_id: number | null; website_id: number;
  wp_post_id: number | null; wp_post_url: string | null;
  title: string; slug: string;
  quality_gate_verdict: "pass" | "warn" | "fail" | null;
  cost_usd: number | null; hero_image_url: string | null;
  published_at: string;
  keywords?: { keyword: string } | null;
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

export type ProviderBalance = {
  provider: string; ok: boolean;
  remaining: number | null; required: number | null;
  unit: string; detail: string | null; checked: boolean;
};

export type CreditStatus = {
  ok: boolean;
  providers: ProviderBalance[];
  last_preflight: {
    ok: boolean; blocked_by: string[]; reason: string | null; checked_at?: string;
  } | null;
};

export const api = {
  credits: () => req<CreditStatus>("/credits"),
  usage: () => req<{ totals: UsageTotal[]; by_endpoint: UsageEndpoint[] }>("/usage"),
  overview: () => req<Overview>("/overview"),
  websites: () => req<SiteOverview[]>("/websites"),
  updateWebsite: (
    id: number,
    body: Partial<{
      name: string; category: string; active: boolean; domain: string;
      wp_base_url: string; wp_username: string; wp_app_password: string;
      seo_plugin: "yoast" | "rankmath" | "none"; articles_per_day: number;
      article_automation_enabled: boolean;
    }>,
  ) => req(`/websites/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  automation: () => req<{ enabled: boolean }>("/automation"),
  setAutomation: (enabled: boolean) =>
    req<{ enabled: boolean }>("/automation", { method: "POST", body: JSON.stringify({ enabled }) }),
  articleAutomation: () => req<{ enabled: boolean }>("/automation/articles"),
  setArticleAutomation: (enabled: boolean) =>
    req<{ enabled: boolean }>("/automation/articles", { method: "POST", body: JSON.stringify({ enabled }) }),
  keywords: (params: Record<string, string | number | undefined>) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && q.set(k, String(v)));
    return req<Keyword[]>(`/keywords?${q}`);
  },
  runs: (websiteId?: number) =>
    req<Run[]>(`/runs${websiteId ? `?website_id=${websiteId}` : ""}`),
  niches: (websiteId: number) => req<Niche[]>(`/niches?website_id=${websiteId}`),
  triggerBacklinkGap: (keywordId: number) =>
    req<{ job_id: number; status: string }>(`/keywords/${keywordId}/backlink-gap`, { method: "POST", body: JSON.stringify({}) }),
  getBacklinkJob: (jobId: number) => req<BacklinkGapJob>(`/backlink-jobs/${jobId}`),
  backlinkCandidates: (keywordId: number) =>
    req<BacklinkCandidate[]>(`/keywords/${keywordId}/backlink-candidates`),
  setBacklinkCandidateStatus: (candidateId: number, status: BacklinkCandidate["status"]) =>
    req<BacklinkCandidate>(`/backlink-candidates/${candidateId}`, { method: "PATCH", body: JSON.stringify({ status }) }),
  draftBacklinkOutreach: (candidateId: number) =>
    req<BacklinkCandidate>(`/backlink-candidates/${candidateId}/draft-outreach`, { method: "POST" }),
  articles: (websiteId?: number) =>
    req<PublishedArticle[]>(`/articles${websiteId ? `?website_id=${websiteId}` : ""}`),
  triggerOffpageResearch: (websiteId: number, channel: OffpageChannel) =>
    req<{ job_id: number; status: string }>(`/websites/${websiteId}/offpage/${channel}`, { method: "POST" }),
  getOffpageJob: (jobId: number) => req<OffpageJob>(`/offpage-jobs/${jobId}`),
  offpageOpportunities: (websiteId: number, channel: OffpageChannel) =>
    req<OffpageOpportunity[]>(`/websites/${websiteId}/offpage-opportunities?channel=${channel}`),
  setOffpageOpportunityStatus: (opportunityId: number, status: OffpageOpportunity["status"]) =>
    req<OffpageOpportunity>(`/offpage-opportunities/${opportunityId}`, { method: "PATCH", body: JSON.stringify({ status }) }),
};
