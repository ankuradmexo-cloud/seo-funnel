import argparse
import json

from orchestrator import run_for_keyword


def main():
    parser = argparse.ArgumentParser(
        description="Generate one SEO article from one keyword, logging every step."
    )
    parser.add_argument("--keyword", required=True, help="The target keyword, e.g. 'best travel journal app'")
    parser.add_argument(
        "--website-id", type=int, default=None,
        help="seo-funnel website_id to publish to and scope interlinking/backlinking to. "
             "Omit to run purely locally (no Supabase, no WordPress, no interlinking).",
    )
    parser.add_argument(
        "--keyword-id", type=int, default=None,
        help="seo-funnel keyword_id, if this run corresponds to a real shortlisted keyword row - "
             "on a successful publish, that keyword's status/target_url get updated.",
    )
    args = parser.parse_args()

    summary = run_for_keyword(args.keyword, website_id=args.website_id, keyword_id=args.keyword_id)
    print("\n--- Run summary ---")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
