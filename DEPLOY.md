# Deployment

Three pieces, two platforms:

| Piece | Where | Why |
|---|---|---|
| Next.js dashboard (`dashboard/`) | Vercel | Static/SSR frontend, Vercel's native target |
| FastAPI (`src/api/`) | Render web service | Holds the Supabase service_role key server-side |
| Pipeline (`run_pipeline.py`) | GitHub Actions cron | Runs 15-40 min; Render's free tier has no cron jobs |
| Postgres | Supabase | Already hosted |
| Article publishing | n8n | Already hosted separately, unchanged |

## 1. Database

Run these in the Supabase SQL editor, in order:

1. `supabase/schema.sql` - full schema, safe to re-run
2. `supabase/migration_pending.sql` - count views + `api_usage`

## 2. Render (API)

`render.yaml` defines the web service. Point Render at this repo and it picks
it up as a Blueprint.

The service is on `plan: free`, which spins down when idle - the dashboard's
first request after a quiet period takes ~50s, then behaves normally.

Set these (marked `sync: false`, so Render prompts):

- `SUPABASE_URL`, `SUPABASE_KEY` (service_role)
- `DEEPSEEK_API_KEY`, `SCRAPPA_API_KEY`, `SERANKING_API_KEY`
- `ALLOWED_ORIGINS` - the Vercel URL, e.g. `https://your-app.vercel.app`.
  Comma-separated for multiple. Without this the dashboard's requests are
  blocked by CORS, since it runs on a different origin.

Verify with `curl https://<service>.onrender.com/health`.

## 2b. GitHub Actions (pipeline schedule)

Render's free tier covers web services but **not cron jobs**, so the schedule
lives in `.github/workflows/pipeline.yml`. This repo is public specifically
because public repos get unlimited Actions minutes - two 40-minute runs a day
would blow through a private repo's 2,000 min/month allowance.

Add the five secrets under **Settings > Secrets and variables > Actions**:
`SUPABASE_URL`, `SUPABASE_KEY`, `DEEPSEEK_API_KEY`, `SCRAPPA_API_KEY`,
`SERANKING_API_KEY`. Do not add the tuning values - those are plain `env:`
entries in the workflow.

Scheduled at `0 6,18 * * *` (twice daily). Each firing processes one niche per
active website; two firings gives each site two chances at its 2-keyword daily
target. Use **Actions > keyword-pipeline > Run workflow** to fire one by hand.

Two Actions gotchas worth knowing:

- Scheduled workflows are **disabled automatically after 60 days of repo
  inactivity**. If keywords quietly stop appearing, check the Actions tab
  first.
- Schedules fire on a best-effort basis and can be delayed under load. This
  pipeline is idempotent per day (`MAX_KEYWORDS_PER_SITE_PER_DAY`), so a late
  or doubled firing is harmless.

## 3. Vercel (dashboard)

Set **Root Directory** to `dashboard`. Vercel auto-detects Next.js.

Environment variable:

- `NEXT_PUBLIC_API_URL` - the Render web service URL, e.g.
  `https://keyword-funnel-api.onrender.com` (no trailing slash, no `/api`).

It must be `NEXT_PUBLIC_` because the browser makes these calls directly.
Nothing secret belongs here - the service_role key stays on Render.

## Order of operations

Vercel and Render each need the other's URL, so deploy in this order:

1. Push to GitHub, add the five Actions secrets
2. Deploy Render, note the API URL (set `ALLOWED_ORIGINS` to a placeholder)
3. Deploy Vercel with `NEXT_PUBLIC_API_URL` set to it, note the Vercel URL
4. Set `ALLOWED_ORIGINS` on Render to the Vercel URL and redeploy

## Pausing

The dashboard's pause switch does **not** stop the Actions schedule - it can't.
It writes `automation_enabled=false` to `system_config`, and `run_pipeline.py`
checks that on startup and exits before spending any API credits. The workflow
still fires; it just exits in seconds, costing a negligible slice of Actions
minutes and zero API credits.

For manual runs that should ignore the pause: `python run_pipeline.py --ignore-pause`

## Cost per run

Measured, not estimated: **~$0.19 per website-run**, **~$0.58 per full-fleet
dispatch** of three sites, **~$35/month** at two dispatches a day. Six sites
would run ~$70/month.

Dominated by SE Ranking `questions` (10 credits per returned keyword), so
per-run cost varies more than 6x by niche - a niche that returns a lot of
questions costs a lot more. The dials are `SEEDS_PER_NICHE` and
`QUESTIONS_LIMIT_PER_SEED`; cost scales with their product.

Unit prices (kept in `src/api/routes/usage.py`):
Scrappa $10 = 33,000 credits ($0.000303); SE Ranking $50 = 250,000 credits
($0.0002). The `/usage` page shows real measured spend per provider and
endpoint.
