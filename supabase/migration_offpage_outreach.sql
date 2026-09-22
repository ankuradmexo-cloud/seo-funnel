-- Off-page SEO automation: research + drafted outreach for directories,
-- guest posts, and social/forum opportunities (backlink gap already has its
-- own tables - backlink_gap_jobs/backlink_candidates - this only adds an
-- outreach draft to that existing flow). Nothing here ever sends an email
-- or posts anywhere automatically - drafts are for a human to review and
-- send/post themselves.

-- Backlink gap candidates can now carry a drafted outreach email, generated
-- on demand (tools/backlink_outreach_draft.py) from the candidate's own
-- data plus real published articles as portfolio proof.
alter table backlink_candidates add column if not exists outreach_draft text;

-- Job tracking for the three new channels, identical shape to
-- backlink_gap_jobs/pipeline_runs.
create table if not exists outreach_jobs (
    job_id serial primary key,
    website_id integer not null references websites(website_id),
    channel text not null,  -- 'directory' | 'guest_post' | 'social'
    status text not null default 'running',  -- running -> success | failed
    error_message text,
    started_at timestamptz not null default now(),
    finished_at timestamptz
);

create index if not exists idx_outreach_jobs_website on outreach_jobs (website_id);

-- One row per surviving opportunity from a completed job. signal_summary is
-- free text rather than normalized columns because the three channels carry
-- genuinely different signals (a directory's relevance, a guest-post page's
-- submission guidelines, a forum thread's activity) - trying to force one
-- schema onto all three would mean mostly-null columns either way.
create table if not exists outreach_opportunities (
    opportunity_id serial primary key,
    job_id integer not null references outreach_jobs(job_id),
    website_id integer not null references websites(website_id),
    channel text not null,  -- 'directory' | 'guest_post' | 'social'
    target_url text,
    target_domain text,
    title text,
    signal_summary text,
    contact_info text,
    outreach_draft text,
    status text not null default 'new',  -- new -> contacted -> replied -> won | rejected
    found_at timestamptz not null default now()
);

create index if not exists idx_outreach_opportunities_website on outreach_opportunities (website_id, channel);

NOTIFY pgrst, 'reload schema';
