import argparse
import json

from graph_orchestrator import run_for_keyword_graph


def main():
    parser = argparse.ArgumentParser(
        description="Generate one SEO article from one keyword, via the LangGraph pipeline."
    )
    parser.add_argument("--keyword", required=True, help="The target keyword, e.g. 'best travel journal app'")
    args = parser.parse_args()

    summary = run_for_keyword_graph(args.keyword)
    print("\n--- Run summary ---")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
