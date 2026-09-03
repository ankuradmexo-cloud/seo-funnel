import argparse
import sys
from datetime import datetime, timezone

from src.clients import balances
from src.clients import supabase_client as db
from src.pipeline.orchestrator import run_all_active_websites, run_for_website


def main():
    parser = argparse.ArgumentParser(description="Run the SEO keyword funnel.")
    parser.add_argument(
        "--website_id", type=int, default=None,
        help="Run a single website by id (for testing/debugging).",
    )
    parser.add_argument(
        "--ignore-pause", action="store_true",
        help="Run even if automation is paused in the dashboard.",
    )
    parser.add_argument(
        "--skip-credit-check", action="store_true",
        help="Start even if a provider is short on credits (debugging only).",
    )
    args = parser.parse_args()

    # The scheduler (Render cron / GitHub Actions) can't be stopped from the
    # dashboard, so the pause switch is enforced here instead: the job still
    # fires on schedule but exits before spending any API credits.
    if not args.ignore_pause and not db.automation_enabled():
        print("Automation is paused in the dashboard - exiting without running.")
        sys.exit(0)

    # Credits are checked before the first provider call, not lazily. The
    # cheap provider runs first - a run spends ~330 Scrappa credits on
    # autocomplete before SE Ranking is touched - so discovering an empty SE
    # Ranking balance mid-run means that Scrappa spend bought nothing.
    if not args.skip_credit_check:
        pre = balances.preflight()
        pre["checked_at"] = datetime.now(timezone.utc).isoformat()
        try:
            db.set_config("credit_preflight", pre)
        except Exception as e:  # noqa: BLE001 - never let bookkeeping block the run
            print(f"warning: could not record preflight result: {e}", file=sys.stderr)
        if not pre["ok"]:
            print(f"Insufficient credits - not starting. {pre['reason']}")
            sys.exit(0)

    if args.website_id is not None:
        website = next(
            (w for w in db.get_all_websites() if w.website_id == args.website_id), None
        )
        if website is None:
            print(f"No website with id {args.website_id}.")
            sys.exit(1)
        if not website.active:
            print(f"Website {website.name} is paused (active=false) - skipping.")
            sys.exit(0)
        result = run_for_website(website)
    else:
        result = run_all_active_websites()

    print(result)


if __name__ == "__main__":
    main()
