-- WordPress publishing + interlinking + independent per-site automation for
-- the article pipeline (article-pipeline/). Keyword shortlisting already had
-- a per-site pause (websites.active) and a global one (system_config.automation_enabled);
-- article generation gets the same two-gate shape, kept as separate columns/keys
-- so pausing one pipeline never pauses the other.

alter table websites add column if not exists domain text;
alter table websites add column if not exists wp_base_url text;
alter table websites add column if not exists wp_username text;
alter table websites add column if not exists wp_app_password text;
-- 'yoast' | 'rankmath' | 'none' - which plugin (if any) owns SEO meta title/
-- description on this site's WordPress install. Explicit per-site, not
-- auto-detected, since a fleet can run a mix of plugins.
alter table websites add column if not exists seo_plugin text not null default 'none';
alter table websites add column if not exists articles_per_day integer not null default 2;
-- Per-site pause for article generation, independent of `active` (which only
-- pauses keyword shortlisting for that site).
alter table websites add column if not exists article_automation_enabled boolean not null default true;

-- One row per article actually published to WordPress. This is both the
-- publish record AND the interlinking candidate pool - a new article looks
-- up other rows here for the same website_id to decide what to link to.
create table if not exists published_articles (
    article_id serial primary key,
    keyword_id integer references keywords(keyword_id),
    website_id integer not null references websites(website_id),
    wp_post_id integer,
    wp_post_url text,
    title text,
    slug text,
    published_at timestamptz not null default now()
);

create index if not exists idx_published_articles_website on published_articles (website_id, published_at desc);

-- Global kill switch for article automation, mirroring the existing
-- 'automation_enabled' key used for keyword shortlisting.
insert into system_config (key, value) values ('article_automation_enabled', 'true'::jsonb)
on conflict (key) do nothing;

NOTIFY pgrst, 'reload schema';
