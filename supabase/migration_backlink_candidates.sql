-- Backlink gap analysis, triggered on-demand from the dashboard (a button
-- per keyword/article, not automatic - real SE Ranking credits per run,
-- ~600 on average, see tools/backlink_gap.py in article-pipeline/).
-- Job tracking mirrors the existing pipeline_runs/article_jobs pattern:
-- the dashboard polls status here rather than blocking on the request,
-- since one analysis takes a couple of minutes.

create table if not exists backlink_gap_jobs (
    job_id serial primary key,
    keyword_id integer not null references keywords(keyword_id),
    website_id integer not null references websites(website_id),
    status text not null default 'running',  -- running -> success | failed
    error_message text,
    started_at timestamptz not null default now(),
    finished_at timestamptz
);

create index if not exists idx_backlink_gap_jobs_keyword on backlink_gap_jobs (keyword_id);

-- One row per surviving candidate domain from a completed job - already
-- passed the tool's full filter chain (domain-rank floor, spam-anchor
-- check, aggregator check, LLM topical-relevance check), so every row
-- here is meant to be a genuinely plausible outreach target, not a raw
-- unfiltered backlink list.
create table if not exists backlink_candidates (
    candidate_id serial primary key,
    job_id integer not null references backlink_gap_jobs(job_id),
    keyword_id integer not null references keywords(keyword_id),
    website_id integer not null references websites(website_id),
    referring_domain text not null,
    domain_inlink_rank integer,
    competitors_linked_count integer,
    sample_links jsonb,  -- [{linked_to_competitor, from_page, from_page_title, anchor, nofollow}, ...]
    status text not null default 'new',  -- new -> contacted -> replied -> linked | rejected
    found_at timestamptz not null default now()
);

create index if not exists idx_backlink_candidates_keyword on backlink_candidates (keyword_id);

NOTIFY pgrst, 'reload schema';
