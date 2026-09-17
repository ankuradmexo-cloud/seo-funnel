"""Daily automation: for each website with articles enabled, top up to that
site's articles_per_day quota by running the pipeline against its
best-judged shortlisted keywords. Meant to be cron-triggered (see
.github/workflows/article_pipeline.yml), the same shape as seo-funnel's own
run_pipeline.py for keyword shortlisting - a global pause switch plus a
per-site one, checked before any API credits get spent.

Two independent pause gates, deliberately separate from keyword shortlisting's:
- system_config.article_automation_enabled (global, dashboard-toggleable)
- websites.article_automation_enabled (per-site, dashboard-toggleable)
Pausing one pipeline never pauses the other - see supabase/migration_wordpress_publishing.sql.
"""

import argparse
import json
import sys

from clients import db_client
from orchestrator import run_for_keyword


def main():
    parser = argparse.ArgumentParser(description="Run the article pipeline's daily scheduler.")
    parser.add_argument(
        "--ignore-pause", action="store_true",
        help="Run even if article automation is paused globally in the dashboard.",
    )
    args = parser.parse_args()

    if not db_client.configured():
        print("SUPABASE_URL/SUPABASE_KEY not set - scheduler needs Supabase to know which "
              "websites/keywords to run. Exiting without running.")
        sys.exit(0)

    if not args.ignore_pause and not db_client.article_automation_enabled():
        print("Article automation is paused globally in the dashboard - exiting without running.")
        sys.exit(0)

    websites = db_client.get_active_article_websites()
    if not websites:
        print("No websites have article automation enabled - nothing to do.")
        return

    results = []
    for website in websites:
        website_id = website["website_id"]
        quota = website.get("articles_per_day", 2)
        already_today = db_client.articles_published_today(website_id)
        remaining = max(0, quota - already_today)
        print(f"\n=== {website['name']} (website_id={website_id}): "
              f"{already_today}/{quota} published today, {remaining} to go ===")
        if remaining == 0:
            continue

        keywords = db_client.get_shortlisted_keywords_for_articles(website_id, remaining)
        if not keywords:
            print("  No shortlisted keywords available for this site right now.")
            continue

        for kw in keywords:
            print(f"  Running: {kw['keyword']!r} (keyword_id={kw['keyword_id']})")
            try:
                summary = run_for_keyword(kw["keyword"], website_id=website_id, keyword_id=kw["keyword_id"])
                results.append({"website_id": website_id, "keyword": kw["keyword"], "summary": summary})
            except Exception as e:  # noqa: BLE001 - one bad keyword must not abort the whole day's run
                print(f"  FAILED: {kw['keyword']!r} - {e}")
                results.append({"website_id": website_id, "keyword": kw["keyword"], "error": str(e)})

    print("\n--- Scheduler run summary ---")
    print(json.dumps(
        [
            {
                "website_id": r["website_id"], "keyword": r["keyword"],
                "wp_publish_status": r.get("summary", {}).get("wp_publish_status"),
                "quality_gate": r.get("summary", {}).get("quality_gate", {}).get("verdict"),
                "error": r.get("error"),
            }
            for r in results
        ],
        indent=2,
    ))


if __name__ == "__main__":
    main()
