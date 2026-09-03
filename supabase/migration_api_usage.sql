-- Per-run API usage, written once at the end of each run (not per call - a
-- single run makes ~330 Scrappa requests).
create table if not exists api_usage (
    usage_id serial primary key,
    run_id integer references pipeline_runs(run_id),
    website_id integer references websites(website_id),
    provider text not null,          -- scrappa | seranking | deepseek
    endpoint text not null,
    calls integer not null default 0,
    credits numeric not null default 0,   -- 0 for deepseek, which bills by token
    tokens integer not null default 0,    -- 0 for credit-based providers
    created_at timestamptz not null default now()
);

create index if not exists idx_api_usage_run on api_usage (run_id);
create index if not exists idx_api_usage_provider on api_usage (provider, created_at desc);

-- Totals and per-run averages per provider. Averaging over distinct runs
-- rather than over rows, since one run writes several endpoint rows.
create or replace view api_usage_totals as
select
    provider,
    sum(calls)::int                                   as total_calls,
    sum(credits)::numeric                             as total_credits,
    sum(tokens)::bigint                               as total_tokens,
    count(distinct run_id)::int                       as runs,
    round(sum(credits) / nullif(count(distinct run_id), 0), 1) as avg_credits_per_run,
    round(sum(calls)::numeric / nullif(count(distinct run_id), 0), 1) as avg_calls_per_run
from api_usage
group by provider;

create or replace view api_usage_by_endpoint as
select
    provider, endpoint,
    sum(calls)::int       as total_calls,
    sum(credits)::numeric as total_credits,
    sum(tokens)::bigint   as total_tokens,
    count(distinct run_id)::int as runs
from api_usage
group by provider, endpoint;

NOTIFY pgrst, 'reload schema';
