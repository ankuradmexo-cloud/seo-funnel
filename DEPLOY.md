# Deployment

Three pieces, two platforms:

| Piece | Where | Why |
|---|---|---|
| Next.js dashboard (`dashboard/`) | Vercel | Static/SSR frontend, Vercel's native target |
| FastAPI (`src/api/`) | Render web service | Holds the Supabase service_role key server-side |
| Pipeline (`run_pipeline.py`) | Render cron job | Runs 15-40 min; serverless timeouts are far shorter |
| Postgres | Supabase | Already hosted |
| Article publishing | n8n | Already hosted separately, unchanged |

## 1. Database

Run these in the Supabase SQL editor, in order:

1. `supabase/schema.sql` - full schema, safe to re-run
2. `supabase/migration_pending.sql` - count views + `api_usage`

## 2. Render (API + cron)

`render.yaml` defines both services. Point Render at this repo and it picks
them up as a Blueprint.

Set these on **both** services (marked `sync: false`, so Render prompts):

- `SUPABASE_URL`, `SUPABASE_KEY` (service_role)
- `DEEPSEEK_API_KEY`, `SCRAPPA_API_KEY`, `SERANKING_API_KEY`

Set on the **web service only**:

- `ALLOWED_ORIGINS` - the Vercel URL, e.g. `https://your-app.vercel.app`.
  Comma-separated for multiple. Without this the dashboard's requests are
  blocked by CORS, since it runs on a different origin.

The cron is scheduled `0 6,18 * * *` (twice daily). Each firing processes one
niche per active website. Two firings gives each site two chances at its
2-keyword daily target.

## 3. Vercel (dashboard)

Set **Root Directory** to `dashboard`. Vercel auto-detects Next.js.

Environment variable:

- `NEXT_PUBLIC_API_URL` - the Render web service URL, e.g.
  `https://keyword-funnel-api.onrender.com` (no trailing slash, no `/api`).

It must be `NEXT_PUBLIC_` because the browser makes these calls directly.
Nothing secret belongs here - the service_role key stays on Render.

## Order of operations

Vercel and Render each need the other's URL, so deploy in this order:

1. Deploy Render first, note the API URL
2. Deploy Vercel with `NEXT_PUBLIC_API_URL` set to it, note the Vercel URL
3. Set `ALLOWED_ORIGINS` on Render to the Vercel URL and redeploy

## Pausing

The dashboard's pause switch does **not** stop Render's scheduler - it can't.
It writes `automation_enabled=false` to `system_config`, and `run_pipeline.py`
checks that on startup and exits before spending any API credits. The cron
still fires; it just does nothing.

For manual runs that should ignore the pause: `python run_pipeline.py --ignore-pause`

## Cost per run

Roughly $0.60-1.00, dominated by SE Ranking `questions` (10 credits per
returned keyword). The dials are `SEEDS_PER_NICHE` and
`QUESTIONS_LIMIT_PER_SEED` - cost scales with their product. The `/usage` page
shows real measured spend per provider and endpoint.
