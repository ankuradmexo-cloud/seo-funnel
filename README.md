# SEO Keyword Funnel

An agentic pipeline that finds long-tail keywords three low-authority review sites
can realistically rank for, and hands two per site per day to n8n for publishing.

Every discovery technique here was measured on real niches rather than assumed, and
several plausible ones were removed after the data came back — see
[What was tried and dropped](#what-was-tried-and-dropped).

| | |
|---|---|
| **Sites** | trusted-topreviews.com, dealstackpro.com, trustedtopcasinos.com (scales to 6) |
| **Output** | 2 publishable keywords per site per day |
| **Schedule** | GitHub Actions, `0 6,18 * * *` |
| **Cost** | ~$0.18 per site per run (~$0.54 for a full fleet dispatch) |
| **Runtime** | 15–40 min for all three sites |

## Architecture

```
GitHub Actions (cron 0 6,18 * * *)
        │
        ▼
run_pipeline.py ──────────► DeepSeek     seeds, relevance, judge
   8 stages                 Scrappa      autocomplete, SERP
   15–40 min                SE Ranking   demand validation, questions
        │
        ▼
   Supabase (Postgres) ──────► n8n   polls status='shortlisted'
        ▲
        │ service_role
   FastAPI on Render ◄──────── Next.js dashboard on Vercel
```

The dashboard never touches Supabase directly. All reads and writes go through
FastAPI so the service-role key stays server-side and never ships in a
`NEXT_PUBLIC_` variable. This is why CORS configuration is load-bearing rather
than a formality.

## The pipeline

One run processes **one niche for one website**, chosen round-robin by
`last_processed_at ASC NULLS FIRST`. Stage order is deliberate — each stage exists
to make the next one cheaper.

| # | Stage | Module | Cost |
|---|---|---|---|
| 1 | Niche selection | `niche_discovery.py` | 0–1 DeepSeek calls |
| 2 | Seed generation | `seed_generation.py` | 1 DeepSeek call |
| 3 | Discovery expansion | `discovery.py` | ~330 Scrappa + 150 SE Ranking credits/seed |
| 4 | Exact dedup | `normalize.py` | free |
| 5 | Demand validation | `demand_validation.py` | 100 SE Ranking credits, **flat** |
| 6 | Relevance filter | `relevance_filter.py` | 1 DeepSeek call per 100 candidates |
| 7 | Ranking + difficulty cutoff | `orchestrator.py` | free |
| 8 | SERP check + SEO judge | `serp_validation.py`, `seo_judge.py` | 1 Scrappa + 1 DeepSeek per candidate |

Three ordering decisions are load-bearing:

- **Relevance runs before ranking.** An off-topic keyword faces no competition in
  this niche, so it scores as deceptively "easy" and would otherwise crowd out real
  candidates.
- **Ranking is volume-descending, not difficulty-ascending.** Easiest-first sounds
  right and is counterproductive: the lowest-difficulty keywords are the
  lowest-demand, weakest-intent ones. Difficulty is a hard cutoff at 40 instead of a
  sort key.
- **Demand validation is one batched call.** SE Ranking bills `keywords/export` at
  100 credits flat for up to 5,000 keywords, so never split it.

### How the judge decides

The judge ([`seo_judge.py`](src/pipeline/seo_judge.py)) receives the demand metrics,
a 12-month volume trend, and the **live top-5 SERP** — real titles, domains and
snippets, not a fabricated authority score. It returns `approve`, `score` (0–100),
a rationale, and an intent cluster. `approve` is not derived from `score`; both are
emitted independently and there is no threshold in code.

Four rules govern it: read the SERP and cross-check against difficulty rather than
trusting either alone; treat every search intent as equally valid (informational
keywords are explicitly legitimate targets); use the trend directionally; and reject
coupon terms whose SERPs are owned by deal aggregators, which are a harder vertical
for a new site than ordinary review content.

Observed behaviour across 111 judged keywords: cleanly bimodal, rejections 0–45 and
approvals 62–78.

## Providers

| Provider | Used for | Billing |
|---|---|---|
| **DeepSeek** | Seed generation, niche discovery, relevance filter, SEO judge | Per token, negligible |
| **Scrappa** | Autocomplete expansion, live SERP | 1 credit/request, ~$0.00025 |
| **SE Ranking** | Demand validation, `questions` discovery | `export` 100 flat; `questions` 10/keyword returned |
| **Supabase** | Postgres + PostgREST | — |

SE Ranking is ~83% of spend on ~8% of the calls. Both cost dials point at it.

## What was tried and dropped

| Technique | Measured result | Verdict |
|---|---|---|
| Scrappa autocomplete | 28–31% real-volume rate; best approval yield of anything tested | **Primary source** |
| SE Ranking `questions` | ~93% real-volume rate — but only with short head-term seeds | **Kept** |
| SE Ranking `related` | ~98% real-volume rate, the highest of any source, and zero approvals ever | Disabled |
| SE Ranking `longtail` | 0% real search volume, twice | Dropped |
| LLM bulk generation | 0–0.8% real-volume rate across four tests | Deleted |
| Vector semantic search | More architectural complexity than retrieval value at this scale | Cut |
| LLM semantic dedup | Exact database matching is cheaper, deterministic, auditable | Cut |
| Semantic expansion | Expected to raise yield materially | **Deferred** — scaffolding retained |

**The finding that outranks all of these:** niche competitiveness dominates
technique choice. The identical pipeline returned 23% approval on *Travel
Journaling* and 0% on *Productivity Apps for Remote Workers*. The same niche has
produced 11 approvals in one run and 1 in another. That variance is why a niche is
retired only after **two consecutive** zero-approval runs, and why only runs that
completed successfully count as evidence — an infrastructure failure cannot retire
a viable niche.

## Configuration

All settings are environment variables; none require a code change. See
[`src/config.py`](src/config.py).

| Variable | Default | Effect |
|---|---|---|
| `SEEDS_PER_NICHE` | 30 | Primary cost dial. Each seed ≈ 11 Scrappa calls + 150 SE Ranking credits. |
| `QUESTIONS_LIMIT_PER_SEED` | 15 | Second cost dial. Multiplies with the first. |
| `RELATED_LIMIT_PER_SEED` | 0 | Off. Raise to re-enable `related`. |
| `MAX_DIFFICULTY_TO_JUDGE` | 40 | Hard cutoff before the judge. 100 disables it. |
| `MAX_KEYWORDS_PER_SITE_PER_DAY` | 2 | Publishing target; stops the judge loop. |
| `MAX_TOOL_CALLS_PER_RUN` | 100 | DeepSeek budget. Raises `BudgetExceeded`, exempt from retry. |
| `MAX_CANDIDATES_TO_JUDGE_PER_RUN` | 200 | Safety rail only, not an active filter. |

Secrets: `DEEPSEEK_API_KEY`, `SCRAPPA_API_KEY`, `SERANKING_API_KEY`, `SUPABASE_URL`,
`SUPABASE_KEY` (service_role). Never committed — see [`.env.example`](.env.example).

## Database

Tables: `websites`, `niches`, `keywords`, `pipeline_runs`, `agent_calls`,
`api_usage`, `system_config`, `expansions` (scaffolded, unused).

Views: `keyword_status_counts`, `niche_status_counts`, `api_usage_totals`,
`api_usage_by_endpoint`.

**All aggregation happens in Postgres views, never client-side.** PostgREST silently
caps unbounded selects at 1,000 rows, which made counts quietly wrong — 2,443
keywords counted as 1,000, and a shortlisted count read 0 when the true figure was 8.
No error, no warning. Do not reintroduce client-side tallying.

Keyword status flow:

```
deduped → validated → judged ─┬─► shortlisted → queued → published
                              └─► (rejected, stays 'judged')
```

n8n polls `status='shortlisted'`.

## Running locally

```bash
python -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.example .env        # fill in your own keys
```

```bash
./.venv/bin/python run_pipeline.py                    # all active websites
./.venv/bin/python run_pipeline.py --website_id 1     # one site
./.venv/bin/python run_pipeline.py --ignore-pause     # override the dashboard pause
```

API and dashboard:

```bash
./.venv/bin/python run_api.py
```

```bash
cd dashboard && npm install && npm run dev
```

Never run `npm run build` while `npm run dev` is live — they share `.next` and the
build clobbers the dev server's output.

## Deployment

See [DEPLOY.md](DEPLOY.md) for the full runbook. Summary:

- **Pipeline** → GitHub Actions ([`.github/workflows/pipeline.yml`](.github/workflows/pipeline.yml)).
  Render's free tier has no cron jobs. This repo is public because public repos get
  unlimited Actions minutes; two 40-minute runs a day would exceed a private repo's
  2,000-minute allowance.
- **API** → Render free web service ([`render.yaml`](render.yaml)). Spins down when
  idle, so the dashboard's first load after a quiet period takes ~50s.
- **Dashboard** → Vercel, Root Directory `dashboard`, `NEXT_PUBLIC_API_URL` set to
  the Render URL with no trailing slash and no `/api`.

Two failure modes worth knowing before they bite:

- Scheduled Actions workflows are **disabled automatically after 60 days of repo
  inactivity**. If keywords quietly stop appearing, check the Actions tab first.
- Vercel preview deployments get their own hostnames, which are not in
  `ALLOWED_ORIGINS`, so previews fail CORS while production works.

The dashboard's pause switch cannot stop the Actions scheduler. It writes
`automation_enabled=false` to `system_config`; the job still fires and exits in
seconds before spending a credit.

## Gotchas worth reading before you change things

- **DeepSeek's JSON mode guarantees valid JSON, not the right shape.** Every
  structured call injects the JSON Schema into the system prompt with an explicit
  instruction to use those exact field names. Without it the model invents its own,
  and a schema field with a default validates the mismatch as an empty result rather
  than raising. That silently dropped 168 of 168 candidates once.
- **Tenacity raises `RetryError`, not the original exception.** Every client retry
  sets `reraise=True`. Without it, `except httpx.HTTPStatusError` never fires and a
  single transient 5xx kills a whole run the code was written to survive.
- **Transient and fatal HTTP errors are not the same.** 5xx and timeouts skip the
  seed; 4xx propagates. A bad key fails identically on every seed, and swallowing it
  would finish a run "successfully" with zero keywords — which would then retire
  niches for a fault that wasn't theirs.
- **Cleanup writes go through `_best_effort()`.** Failure handlers write to Supabase,
  which is often exactly what failed. A DNS blip once left a run stuck at `running`
  forever because `finish_run()` raised while handling the original error.
- **No guard may disguise a failure.** A minimum keep-ratio fallback in the relevance
  filter was removed for reporting "kept 12/12" while the underlying call returned
  nothing usable.
- **Python 3.9 locally, 3.11 in CI.** No PEP 604 unions — use `Optional[X]`, not
  `X | None`.

## Repository layout

```
src/clients/        API wrappers, one per provider, plus usage tracking
src/pipeline/       The eight stages + orchestrator
src/api/            FastAPI dashboard backend
src/models/         Pydantic schemas
dashboard/          Next.js 15 dashboard (Overview, Keywords, Runs, Niches, Usage, Settings)
supabase/           schema.sql + migrations
.github/workflows/  The pipeline schedule
```

## Contributing

**Update this README whenever you change the repository.** Architecture, stage
order, cost dials, provider choices, deployment topology and the gotchas above are
all documented here — a change that makes any of it stale should update it in the
same commit.
