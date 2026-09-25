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
| **Cost** | ~$0.19 per site per run (measured 2026-09-24) · **~$35/month** at 3 sites, 2 dispatches/day |
| **Runtime** | 15–40 min for all three sites |

## Architecture

```
GitHub Actions (cron 0 6,18 * * *)
        │
        ▼
run_pipeline.py ──────────► DeepSeek     seeds, relevance, batched judge
   8 stages                 Scrappa      discovery (autocomplete), SERP checks
   15–40 min                SE Ranking   discovery (questions), demand validation
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

## Article pipeline (`article-pipeline/`)

A second, independently-scheduled pipeline lives in this same repo: it turns
a `status='shortlisted'` keyword into a written article and publishes it to
that website's WordPress site (REST API + Application Passwords), with
interlinking to/from that site's other published articles and SEO meta
written to whichever plugin the site uses (Yoast/RankMath/none). It shares
this repo's Supabase project and API keys but runs on its own GitHub Actions
schedule (`article_pipeline.yml`, once daily) with its own pause switches -
a global `system_config.article_automation_enabled` and a per-site
`websites.article_automation_enabled` column, both independent of keyword
shortlisting's own `automation_enabled`/`active` switches, so pausing one
pipeline never pauses the other. Everything it publishes goes live
immediately (no draft/review step); `websites.articles_per_day` is each
site's daily cap. See `article-pipeline/README.md` for the pipeline itself
and `supabase/migration_wordpress_publishing.sql` for the schema it adds.
A website's WordPress URL/credentials, SEO plugin, daily quota, and article
pause switch are all configured on the dashboard's Settings page.

## The pipeline

One run processes **one niche for one website**, chosen round-robin by
`last_processed_at ASC NULLS FIRST`. Stage order is deliberate — each stage exists
to make the next one cheaper.

| # | Stage | Module | Cost |
|---|---|---|---|
| 1 | Niche selection | `niche_discovery.py` | 0–1 DeepSeek calls |
| 2 | Seed generation | `seed_generation.py` | 1 DeepSeek call |
| 3 | Discovery expansion | `discovery.py` | Scrappa autocomplete (flat 1 credit/call, seed × (1 + breadth 10) calls) + SE Ranking `questions` (10 credits/keyword returned) |
| 4 | Exact dedup | `normalize.py` | free — word-order/stopword/plural/gerund-insensitive (`dedup_key`) |
| 5 | Demand validation | `demand_validation.py` | 100 SE Ranking credits, **flat** |
| 6 | Relevance filter | `relevance_filter.py` | 1 DeepSeek call per 100 candidates |
| 7 | Ranking + difficulty cutoff | `orchestrator.py` | free |
| 8 | SERP check + SEO judge | `serp_validation.py`, `seo_judge.py` | 1 Scrappa credit (SERP check) per candidate + 1 batched DeepSeek call per `JUDGE_BATCH_SIZE` (default 5) candidates |

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
- **Every candidate that reaches stage 8 gets judged — no early stop.** The judge
  loop used to break once `shortlisted_count >= MAX_KEYWORDS_PER_SITE_PER_DAY`,
  which meant most candidates reaching the judge each run never got their real
  demand/difficulty data persisted (found stuck at `status='deduped'`,
  un-revisitable — 38,960 keywords affected before the fix). Now every candidate
  is judged and its outcome is always written; `MAX_KEYWORDS_PER_SITE_PER_DAY` is
  purely a downstream article-publishing throttle (`websites.articles_per_day`),
  not a keyword-shortlisting cap. Judge calls are batched
  (`JUDGE_BATCH_SIZE`, default 5 keywords/call) so this doesn't multiply DeepSeek
  cost — verdicts are matched back by each candidate's own `keyword` field, not
  list position.

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
| **DeepSeek** | Seed generation, niche discovery, relevance filter, batched SEO judge | Per token, negligible (~$0.02 for a real 150-candidate/36-call run) |
| **Scrappa** | Discovery (`autocomplete`, primary volume source), live SERP checks | $10 = 33,000 credits ($0.0003). Flat 1 credit/call for both endpoints — ~33x cheaper per SERP check than SE Ranking's old `serp/classic` task |
| **SE Ranking** | Discovery (`questions`, secondary precision source), demand validation | $50 = 250,000 credits ($0.0002). `export` 100 flat; `questions`/`related` 10/keyword returned |
| **Supabase** | Postgres + PostgREST | — |

Scrappa and SE Ranking are both live, credit-metered providers as of
2026-09-24 — a full rebuild from the brief 2026-09-21 SE-Ranking-only period
(see [What was tried and dropped](#what-was-tried-and-dropped) for why that
was reverted). SE Ranking's `similar` endpoint was removed entirely; SERP
checks moved back to Scrappa project-wide, including in `article-pipeline/`.

### What it actually costs

Measured live end-to-end on 2026-09-24, after the rebuild below, on one real
run (website 1, niche "Budget Travel Apps for Backpackers"), via provider
balance deltas before/after — not estimated:

| Provider | Used | Cost |
|---|---|---|
| Scrappa (autocomplete + SERP checks) | 435 credits | $0.132 |
| SE Ranking (`questions` + demand validation) | 190 credits | $0.038 |
| DeepSeek (36 batched judge calls) | ~149k tokens | $0.020 |
| **Total** | | **≈ $0.19** |

That run found 941 candidates, judged all 150 that reached stage 8 (no early
stop), and shortlisted 50 — **≈$0.0038 per shortlisted keyword**.

Projected at two dispatches a day:

| Sites | Per day | Per month |
|---|---|---|
| 3 (today) | ~$1.16 | **~$35** |
| 6 (planned) | ~$2.33 | **~$70** |

## What was tried and dropped

| Technique | Measured result | Verdict |
|---|---|---|
| Scrappa autocomplete | 46% real-volume rate, 60.9% judge-approved once Scrappa SERP made per-check cost cheap; best cost-per-approved-keyword (~$0.0014 vs `questions`' ~$0.0041) | **Restored 2026-09-24** as primary discovery source, after a brief 2026-09-21 removal for reliability concerns that didn't reproduce on retest |
| SE Ranking `similar` | Tried as autocomplete's 2026-09-21 replacement | **Removed 2026-09-24** — no real yield data ever materialized before autocomplete was restored |
| SE Ranking `questions` | 95.2% real-volume rate → 65.0% judge-approved, the highest hit-rate of anything tested | **Kept** as the secondary precision source |
| SE Ranking `related` | ~98% real-volume rate, the highest of any source, and zero approvals ever | Disabled |
| SE Ranking `longtail` | 0% real search volume, twice | Dropped |
| SE Ranking `serp/classic` (SERP checks) | Correct results, but ~33x more expensive per check than Scrappa's flat 1-credit `/search` (50 credits vs 1) | **Reverted to Scrappa 2026-09-24**, project-wide (both pipelines) |
| Scrappa PAA → DeepSeek keyword rewrite | 0% real-volume rate raw, 33% after an LLM rewrite pass — still far below `questions`/`autocomplete` | Not adopted |
| LLM bulk generation | 0–0.8% real-volume rate across four tests | Deleted |
| Vector semantic search | More architectural complexity than retrieval value at this scale | Cut |
| LLM semantic dedup | Exact database matching is cheaper, deterministic, auditable | Cut |
| Semantic expansion | Expected to raise yield materially | **Deferred** — scaffolding retained |

**The finding that outranks all of these:** niche competitiveness dominates
technique choice. The identical pipeline returned 23% approval on *Travel
Journaling* and 0% on *Productivity Apps for Remote Workers*. The same niche has
produced 11 approvals in one run and 1 in another. That variance is why, as of
2026-09-24, a niche is retired after its **first** zero-approval run rather than
two consecutive ones — now that every candidate reaching the judge is actually
judged (no early stop), a zero-approval run is a real signal instead of a
possibly-unlucky partial sample, and `niche_discovery.py` can generate a fresh
niche to replace a retired one. Only runs that completed successfully count as
evidence either way — an infrastructure failure cannot retire a viable niche.

## Credit preflight

Every run checks all three provider balances **before calling any of them**, and
refuses to start if one is short.

| Provider | Balance endpoint | Gate |
|---|---|---|
| SE Ranking | `GET /v1/account/credits` | sum of subscription + addon + wallet remaining, **and** `access.can_use_data_api` |
| Scrappa | `GET /account/usage` | usable credits remaining |
| DeepSeek | `GET /user/balance` | USD balance and `is_available` |

All three are free account-metadata reads — checking costs nothing.

Requirements are derived from the cost dials in `src/clients/balances.py`, not
hardcoded, so raising `SEEDS_PER_NICHE` raises the bar a run must clear. At
current defaults (`SEEDS_PER_NICHE=30`): SE Ranking ~4,600 credits (demand +
`questions`), Scrappa ~530 credits (autocomplete BFS + per-candidate SERP
checks), DeepSeek $0.10 floor.

A bounced run creates **no `pipeline_runs` row** — nothing ran. The result is
written to `system_config.credit_preflight` and the dashboard shows a
**Credits not sufficient** banner on the Overview page, with the shortfall per
provider. `GET /api/credits` returns live balances plus the last preflight.

A provider whose balance endpoint is unreachable is treated as OK. Blocking every
run because a status endpoint had a bad minute would be a worse failure than the
one this guard prevents — and the run's own 4xx handling still fails loudly if the
credits really are gone.

Use `--skip-credit-check` to start anyway (debugging only).

## Configuration

All settings are environment variables; none require a code change. See
[`src/config.py`](src/config.py).

| Variable | Default | Effect |
|---|---|---|
| `SEEDS_PER_NICHE` | 30 | Primary cost dial. Each seed does one Scrappa autocomplete BFS (1 + 10 breadth calls, flat 1 credit each) plus one SE Ranking `questions` call (10 credits/keyword returned, up to `QUESTIONS_LIMIT_PER_SEED`). |
| `QUESTIONS_LIMIT_PER_SEED` | 15 | SE Ranking discovery cost dial (secondary source). |
| `RELATED_LIMIT_PER_SEED` | 0 | Off. Raise to re-enable SE Ranking `related`. |
| `MAX_DIFFICULTY_TO_JUDGE` | 40 | Hard cutoff before the judge. 100 disables it. |
| `MAX_KEYWORDS_PER_SITE_PER_DAY` | 2 | Downstream article-publishing throttle (`websites.articles_per_day`) — **not** a keyword-shortlisting cap; every candidate that reaches the judge is still judged. |
| `MAX_TOOL_CALLS_PER_RUN` | 50 | DeepSeek budget - raises `BudgetExceeded`, exempt from retry. |
| `MAX_CANDIDATES_TO_JUDGE_PER_RUN` | 200 | Real ceiling on Scrappa SERP checks (1 credit each) now that judging doesn't stop early. |
| `JUDGE_BATCH_SIZE` | 5 | Keywords sent per DeepSeek judge call. Falls back to one-at-a-time for any keyword a batch response is missing a verdict for. |

Secrets: `DEEPSEEK_API_KEY`, `SCRAPPA_API_KEY`, `SERANKING_API_KEY`,
`SUPABASE_URL`, `SUPABASE_KEY` (service_role). Never committed — see
[`.env.example`](.env.example).

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
./.venv/bin/python run_pipeline.py --skip-credit-check  # start despite low credits
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
