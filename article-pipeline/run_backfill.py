"""One-off backfill: publish one article per day for a website, backdated.

Usage: python run_backfill.py --website-id 1 --start 2026-11-09 --end 2026-11-15
Each day gets one article dated that day at a random daytime hour (UTC). Keywords
come from the same shortlist as run_scheduler.py, with the same retry-on-gate-fail
behaviour, so a failed pick falls through to the next keyword for that day.
"""

import argparse
import random
import sys
from datetime import date, datetime, timedelta, timezone

from clients import db_client
from config import settings
from orchestrator import run_for_keyword

MAX_ATTEMPTS_PER_DAY = 4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--website-id", type=int, required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    args = ap.parse_args()

    day, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    attempted: set[int] = set()
    done, missed = [], []
    while day <= end:
        when = datetime(day.year, day.month, day.day, random.randint(8, 17), random.randint(0, 59),
                        tzinfo=timezone.utc)
        # A timestamp ahead of now would make WordPress schedule the post instead of publishing it.
        when = min(when, datetime.now(timezone.utc) - timedelta(minutes=5))
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S")
        published = False
        for _ in range(MAX_ATTEMPTS_PER_DAY):
            kws = db_client.get_shortlisted_keywords_for_articles(args.website_id, 1, exclude_ids=attempted)
            if not kws:
                break
            kw = kws[0]
            attempted.add(kw["keyword_id"])
            print(f"[{day}] {kw['keyword']!r} (keyword_id={kw['keyword_id']}) dated {stamp}", flush=True)
            try:
                s = run_for_keyword(kw["keyword"], website_id=args.website_id,
                                    keyword_id=kw["keyword_id"], publish_date=stamp)
            except Exception as e:  # noqa: BLE001
                print(f"  FAILED: {e}", flush=True)
                continue
            if s.get("wp_publish_status") == settings.wp_publish_status:
                done.append((str(day), s.get("wp_post_url")))
                published = True
                break
            if s.get("wp_publish_status") == "failed":
                # WordPress itself rejected a gate-passing article; retrying would
                # just burn DeepSeek spend on articles that cannot publish.
                print("  WordPress rejected the post - aborting the backfill.", flush=True)
                print("\nPublished:", *done, sep="\n  ")
                sys.exit(2)
        if not published:
            missed.append(str(day))
        day += timedelta(days=1)
    print("\nPublished:", *done, sep="\n  ")
    print("Missed days:", missed or "none")
    sys.exit(0 if not missed else 1)


if __name__ == "__main__":
    main()
