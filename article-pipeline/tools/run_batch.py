"""Runs the pipeline for multiple keywords sequentially - for batch testing
(e.g. checking Hemingway/scorer consistency across several real articles)
without one CLI invocation per keyword.

Sequential, not parallel: SE Ranking's demand endpoint has a measured
1-request/second limit, and running several full pipelines concurrently
against shared provider rate limits is asking for cross-run interference
for a test that's about output quality, not throughput.

One keyword's failure doesn't stop the batch - caught, logged, and the
next keyword still runs.

Usage:
    ./.venv/bin/python tools/run_batch.py "keyword one" "keyword two" ...
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator import run_for_keyword


def main():
    keywords = sys.argv[1:]
    if not keywords:
        print('Usage: tools/run_batch.py "keyword one" "keyword two" ...')
        sys.exit(1)

    results = []
    for i, kw in enumerate(keywords, 1):
        print(f"\n{'=' * 70}\n[{i}/{len(keywords)}] {kw}\n{'=' * 70}")
        started = time.monotonic()
        try:
            summary = run_for_keyword(kw)
            results.append({"keyword": kw, "ok": True, "summary": summary})
        except Exception as e:  # noqa: BLE001 - one bad keyword must not kill the batch
            print(f"FAILED: {kw} -> {type(e).__name__}: {e}")
            results.append({"keyword": kw, "ok": False, "error": str(e)})
        print(f"  ({time.monotonic() - started:.0f}s)")

    print(f"\n{'=' * 70}\nBATCH DONE: {sum(1 for r in results if r['ok'])}/{len(results)} succeeded\n{'=' * 70}")
    for r in results:
        status = "OK" if r["ok"] else f"FAILED: {r.get('error')}"
        print(f"  {r['keyword']:<45} {status}")


if __name__ == "__main__":
    main()
