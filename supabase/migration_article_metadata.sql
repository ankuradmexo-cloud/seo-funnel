-- Quality gate verdict, real cost, and hero image weren't persisted to
-- published_articles before - only ever written to article-pipeline's
-- local run_summary.json files, invisible outside that machine. Adding
-- them here is what makes a real "Articles" dashboard page possible
-- instead of having to dig through local logs for this data.

alter table published_articles add column if not exists quality_gate_verdict text;
alter table published_articles add column if not exists cost_usd numeric;
alter table published_articles add column if not exists hero_image_url text;

NOTIFY pgrst, 'reload schema';
