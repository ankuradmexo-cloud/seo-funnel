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

import alerts
import health
from clients import db_client
from config import settings
from orchestrator import run_for_keyword

# Bounded extra attempts per site beyond its daily quota, so a site whose
# candidates are all failing the quality gate can't burn unlimited real
# money (each attempt costs real DeepSeek/SE Ranking spend even on a gate
# fail) in a single scheduler run. Measured 2026-09-24: dealstackpro's only
# two picked keywords both failed the gate (0 relevant competitors found for
# either), and with no fallback the site published nothing that day despite
# having 20 other shortlisted keywords sitting unused.
MAX_EXTRA_ATTEMPTS_PER_WEBSITE = 3


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

    try:
        alerts.sync_balances(health.check_balances(), scope="article")
    except Exception as e:  # noqa: BLE001 - monitoring must never block the day's run
        print(f"balance check/alerting failed: {e}")

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

        # Keeps pulling fresh shortlisted keywords as long as the quota
        # isn't met, instead of trying exactly `remaining` candidates once
        # and giving up for the day if any fail the quality gate. A failed
        # keyword reverts to 'shortlisted' (release_keyword) and stays
        # top-ranked, so attempted_ids is what stops it being re-selected
        # immediately within this same run.
        attempted_ids: set[int] = set()
        extra_attempts = 0
        published_this_site = 0
        wp_failed_detail = None

        while remaining > 0 and extra_attempts <= MAX_EXTRA_ATTEMPTS_PER_WEBSITE:
            keywords = db_client.get_shortlisted_keywords_for_articles(
                website_id, remaining, exclude_ids=attempted_ids
            )
            if not keywords:
                if attempted_ids:
                    print("  No more unused shortlisted keywords available for this site.")
                else:
                    print("  No shortlisted keywords available for this site right now.")
                break

            for kw in keywords:
                attempted_ids.add(kw["keyword_id"])
                print(f"  Running: {kw['keyword']!r} (keyword_id={kw['keyword_id']})")
                try:
                    summary = run_for_keyword(kw["keyword"], website_id=website_id, keyword_id=kw["keyword_id"])
                    results.append({"website_id": website_id, "keyword": kw["keyword"], "summary": summary})
                    if summary.get("wp_publish_status") == settings.wp_publish_status:
                        published_this_site += 1
                        remaining -= 1
                    else:
                        extra_attempts += 1
                        if summary.get("wp_publish_status") == "failed":
                            wp_failed_detail = kw["keyword"]
                except Exception as e:  # noqa: BLE001 - one bad keyword must not abort the whole day's run
                    print(f"  FAILED: {kw['keyword']!r} - {e}")
                    results.append({"website_id": website_id, "keyword": kw["keyword"], "error": str(e)})
                    extra_attempts += 1

                if remaining == 0 or extra_attempts > MAX_EXTRA_ATTEMPTS_PER_WEBSITE:
                    break

        if remaining > 0 and published_this_site == 0 and attempted_ids:
            print(f"  Gave up after {len(attempted_ids)} attempt(s) - "
                  f"{MAX_EXTRA_ATTEMPTS_PER_WEBSITE} extra-attempt cap reached or no candidates left.")

        # Alerts (best-effort, see alerts.py). 'Published nothing' and 'WordPress
        # rejected the post' are separate problems with separate fixes.
        name = website["name"]
        site_key = f"article:site{website_id}"
        if remaining > 0 and published_this_site == 0:
            if attempted_ids:
                alerts.raise_issue(
                    f"{site_key}:no_publish", f"{name}: no article published today",
                    f"{len(attempted_ids)} keyword(s) tried, none published (quality-gate fails, errors or WordPress rejections). "
                    "Check the Actions run log.", "warn")
            else:
                alerts.raise_issue(
                    f"{site_key}:no_keywords", f"{name}: no shortlisted keywords left",
                    "The article pipeline has nothing to write for this site - run the keyword pipeline or add keywords.", "warn")
        else:
            alerts.resolve_issue(f"{site_key}:no_publish")
            alerts.resolve_issue(f"{site_key}:no_keywords")
        if wp_failed_detail and published_this_site == 0:
            alerts.raise_issue(
                f"{site_key}:wp_failed", f"{name}: WordPress rejected the post",
                f"Publishing {wp_failed_detail!r} to WordPress failed. Check WP credentials/role and the site's health.")
        elif published_this_site > 0:
            alerts.resolve_issue(f"{site_key}:wp_failed")

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
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - report the crash, then fail the run as before
        alerts.raise_issue("article:crash", "Article scheduler crashed", f"{type(e).__name__}: {str(e)[:500]}")
        raise
    else:
        alerts.resolve_issue("article:crash")
