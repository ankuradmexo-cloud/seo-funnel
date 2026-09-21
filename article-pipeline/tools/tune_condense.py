"""Tunes pipeline/length_control.py against drafts already on disk, without
re-running the pipeline (no SERP/SE Ranking spend, only DeepSeek
condense calls - about $0.02/pass).

Usage:
    ./.venv/bin/python tools/tune_condense.py logs/<run_id>

Reads that run's 06_article_strategy.json for section specs and pairs each
section with its write_section__*.md draft (in outline order - the writer's
own filenames are truncated/sanitized, so index order is the reliable join,
not a name match). Runs only enforce_section_length against each pair,
prints a before/after table, and writes <run_id>/_condense_tuning/<NN>_before.md
and <NN>_after.md so the actual prose can be read, not just the word counts.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clients.deepseek_client import DeepSeekClient
from pipeline.length_control import ENFORCE_SCALE, count_words, enforce_section_length, retention


def main():
    if len(sys.argv) != 2:
        print("Usage: tools/tune_condense.py logs/<run_id>")
        sys.exit(1)

    run_dir = Path(sys.argv[1])
    outline = json.loads((run_dir / "06_article_strategy.json").read_text())
    draft_files = sorted(run_dir.glob("1*_write_section__*.md")) + sorted(run_dir.glob("2[0-5]_write_section__*.md"))

    if len(draft_files) != len(outline["sections"]):
        print(f"WARNING: {len(outline['sections'])} outline sections but {len(draft_files)} draft files - "
              f"pairing by index anyway, check this is the right run.")

    out_dir = run_dir / "_condense_tuning"
    out_dir.mkdir(exist_ok=True)

    deepseek = DeepSeekClient()
    rows = []

    for i, (section, draft_path) in enumerate(zip(outline["sections"], draft_files)):
        draft = draft_path.read_text()
        enforce_target = round(section["target_word_count"] * ENFORCE_SCALE)
        before_words = count_words(draft)

        text, report = enforce_section_length(
            deepseek, draft, section, enforce_target, keywords=[], covered_terms=[],
        )

        (out_dir / f"{i:02d}_before.md").write_text(draft)
        (out_dir / f"{i:02d}_after.md").write_text(text)
        rows.append(report)

        print(
            f"[{i:02d}] {section['heading'][:40]:<40} "
            f"target={report['target']:>4} enforce={report['enforce_target']:>4} "
            f"before={report['before']:>5} after={report['after']:>5} "
            f"rounds={report['rounds']} retention={report['retention']:.2f} "
            f"status={report['status']}"
        )

    total_before = sum(r["before"] for r in rows)
    total_after = sum(r["after"] for r in rows)
    total_target = sum(r["target"] for r in rows)
    print()
    print(f"TOTAL  before={total_before}  after={total_after}  target={total_target}  "
          f"({(total_after / total_target - 1) * 100:+.0f}% vs target)")
    print(f"DeepSeek calls used: {deepseek.calls_made}  tokens: {deepseek.total_tokens}")
    print(f"Before/after pairs written to {out_dir}")

    (out_dir / "_report.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
