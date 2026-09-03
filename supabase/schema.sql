-- Websites being run through the funnel
create table if not exists websites (
    website_id serial primary key,
    name text not null,
    category text not null,
    seed_niches text[] not null default '{}',
    active boolean not null default true,
    created_at timestamptz not null default now()
);

-- Canonical niches per website. Seed generation checks this list before
-- inventing new niches, and each pipeline run processes exactly one niche
-- (whichever has waited longest), so niches rotate through fairly instead
-- of all being (re)processed - and re-worded - every run.
create table if not exists niches (
    niche_id serial primary key,
    website_id integer not null references websites(website_id),
    name text not null,
    status text not null default 'active',  -- active | exhausted | paused
    source text not null default 'seed',    -- seed | expansion
    times_processed integer not null default 0,
    last_processed_at timestamptz,
    created_at timestamptz not null default now(),
    unique (website_id, name)
);

create index if not exists idx_niches_website_status on niches (website_id, status, last_processed_at);

-- One row per pipeline execution (a scheduled run, or a manual --website_id run)
create table if not exists pipeline_runs (
    run_id serial primary key,
    website_id integer not null references websites(website_id),
    niche_id integer references niches(niche_id),
    status text not null default 'running',
    -- running -> success | failed
    candidates_found integer not null default 0,
    shortlisted_count integer not null default 0,
    error_message text,
    started_at timestamptz not null default now(),
    finished_at timestamptz
);

create index if not exists idx_runs_website on pipeline_runs (website_id, started_at desc);

-- Keyword corpus + shortlist, consumed by n8n
create table if not exists keywords (
    keyword_id serial primary key,
    website_id integer not null references websites(website_id),
    run_id integer references pipeline_runs(run_id),
    niche_id integer not null references niches(niche_id),
    keyword text not null,
    normalized_keyword text not null,
    search_volume integer,
    cpc numeric,
    competition numeric,  -- 0-1 paid-search competition
    difficulty integer,   -- 0-100 organic ranking difficulty
    intents text[],        -- I/C/T/L/N search intent codes from SE Ranking
    history_trend jsonb,   -- {"YYYY-MM-DD": monthly_volume}, up to 12 months
    source text[] not null default '{}',
    status text not null default 'candidate',
    -- deduped -> validated -> judged -> shortlisted -> queued -> published
    -- ('candidate' is the column default but no longer written - dedup is
    -- exact-match only now, so a survivor is 'deduped' from its first insert)
    judge_score numeric,
    judge_rationale text,
    intent_cluster text,
    target_url text,
    expanded boolean not null default false,  -- already used as a semantic-expansion anchor?
    expanded_at timestamptz,
    first_seen timestamptz not null default now(),
    last_updated timestamptz not null default now(),
    unique (website_id, normalized_keyword)
);

create index if not exists idx_keywords_website_status on keywords (website_id, status);
create index if not exists idx_keywords_run on keywords (run_id);
-- idx_keywords_niche is created at the end of this file, after the migration
-- block below guarantees niche_id exists (it won't yet on an existing DB
-- where the CREATE TABLE above was a no-op).

-- Every agent decision, for tracing/eval/audit
create table if not exists agent_calls (
    call_id serial primary key,
    website_id integer references websites(website_id),
    keyword_id integer references keywords(keyword_id),
    stage text not null,
    -- niche_discovery | seed_generation | discovery | exact_dedup |
    -- demand_validation | serp_validation | seo_judge | store | semantic_expansion
    prompt_version text,
    input jsonb,
    output jsonb,
    latency_ms integer,
    tokens_used integer,
    created_at timestamptz not null default now()
);

-- One row per semantic-expansion round on a proven-keyword anchor. Lets the
-- dashboard show which anchors have already been mined and how far.
create table if not exists expansions (
    expansion_id serial primary key,
    anchor_keyword_id integer not null references keywords(keyword_id),
    round integer not null,
    new_keywords_found integer not null default 0,
    exhausted boolean not null default false,
    created_at timestamptz not null default now()
);

create index if not exists idx_expansions_anchor on expansions (anchor_keyword_id);

-- ============================================================
-- MIGRATION for existing deployments (safe to re-run)
-- Brings a DB created before the niches table existed up to the shape above.
-- ============================================================

-- 1. Backfill niches from whatever distinct (website_id, niche) strings
--    already exist in keywords, before keywords.niche (text) is dropped.
do $$
begin
    if exists (select 1 from information_schema.columns
               where table_name = 'keywords' and column_name = 'niche') then

        insert into niches (website_id, name)
        select distinct website_id, niche from keywords
        on conflict (website_id, name) do nothing;

        alter table keywords add column if not exists niche_id integer references niches(niche_id);

        update keywords k
        set niche_id = n.niche_id
        from niches n
        where n.website_id = k.website_id and n.name = k.niche and k.niche_id is null;

        alter table keywords alter column niche_id set not null;
        alter table keywords drop column niche;
    end if;
end $$;

alter table keywords add column if not exists expanded boolean not null default false;
alter table keywords add column if not exists expanded_at timestamptz;
alter table keywords add column if not exists intents text[];
alter table keywords add column if not exists history_trend jsonb;
alter table pipeline_runs add column if not exists niche_id integer references niches(niche_id);

drop index if exists idx_keywords_website_niche;
create index if not exists idx_keywords_niche on keywords (niche_id);

-- Simple key/value control plane, so the dashboard can pause the pipeline
-- without touching the scheduler. A Render/GitHub cron job cannot be stopped
-- from the app, but the run itself checks this flag and exits immediately.
create table if not exists system_config (
    key text primary key,
    value jsonb not null,
    updated_at timestamptz not null default now()
);

insert into system_config (key, value) values ('automation_enabled', 'true'::jsonb)
on conflict (key) do nothing;
