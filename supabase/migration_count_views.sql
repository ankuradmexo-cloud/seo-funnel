-- Aggregate views for the dashboard.
--
-- PostgREST caps an unbounded select at 1000 rows, so tallying statuses
-- client-side silently undercounts once a table passes that (2443 keywords
-- were being reported as 1000). Counting server-side per website/status pair
-- is correct but needs ~30 round trips, which made /api/overview take 10.8s.
-- These views do the GROUP BY in Postgres: one query, correct, and they
-- return only a handful of rows so the 1000-row cap is never in play.

create or replace view keyword_status_counts as
select website_id, status, count(*)::int as count
from keywords
group by website_id, status;

create or replace view niche_status_counts as
select website_id, status, count(*)::int as count
from niches
group by website_id, status;

NOTIFY pgrst, 'reload schema';
