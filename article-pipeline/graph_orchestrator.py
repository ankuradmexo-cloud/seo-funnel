"""Graph-based counterpart to orchestrator.py - same run, same per-step
logging, same final run_summary/output files, but driven by
graph_pipeline.py's LangGraph instead of a linear sequence of function
calls. Kept as a separate entry point (run_graph.py) rather than replacing
orchestrator.py outright, so the two can be run side by side on the same
keyword and compared while this migration is still being verified.

Reuses orchestrator.py's _slugify/_write_html rather than duplicating them -
those are pure output-formatting helpers with no pipeline logic in them.
"""

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from clients.deepseek_client import DeepSeekClient
from clients.scrappa_client import ScrappaClient
from clients.seranking_client import SERankingClient
from config import settings
from graph_pipeline import build_graph
from orchestrator import _slugify, _write_html
from pipeline.cost_tracking import compute_run_cost
from pipeline.length_control import count_paragraphs, count_words, flesch_kincaid_grade
from pipeline.quality_gate import evaluate_run

LOGS_DIR = Path(__file__).parent / "logs"
OUTPUT_DIR = Path(__file__).parent / "output"


def run_for_keyword_graph(keyword: str) -> dict:
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{_slugify(keyword)}_graph"
    run_dir = LOGS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    step_times: dict = {}

    def log_step(name: str, data) -> None:
        # See orchestrator.py's log_step - name can embed a section heading
        # containing "/", which Path would otherwise treat as a missing
        # subdirectory.
        safe_name = re.sub(r"[/\\]", "-", name)
        ext = "md" if isinstance(data, str) else "json"
        path = run_dir / f"{len(step_times):02d}_{safe_name}.{ext}"
        payload = data if isinstance(data, str) else json.dumps(data, indent=2, default=str)
        path.write_text(payload)
        step_times[name] = time.monotonic()
        print(f"  [{len(step_times):02d}] {name} -> {path.name}")

    deepseek = DeepSeekClient()
    scrappa = ScrappaClient()
    seranking = SERankingClient()

    started = time.monotonic()
    print(f"Run {run_id} (graph engine)")

    graph = build_graph(
        deepseek, scrappa, seranking, settings, log_step, html_dir=run_dir / "html",
    )
    thread_id = str(uuid.uuid4())
    final_state = graph.invoke(
        {"keyword": keyword},
        config={"configurable": {"thread_id": thread_id}},
    )

    elapsed = time.monotonic() - started

    if final_state.get("rejected"):
        search_intent = final_state.get("search_intent") or {}
        reason = search_intent.get("unsuitability_reason") or "not specified"
        print(f"  REJECTED: not suitable for a long-form article - {reason}")
        run_summary = {
            "run_id": run_id,
            "keyword": keyword,
            "rejected": True,
            "rejection_reason": reason,
            "search_intent": search_intent.get("primary_intent"),
            "elapsed_seconds": round(elapsed, 1),
            "deepseek_calls": deepseek.calls_made,
            "scrappa_calls": scrappa.calls_made,
            "seranking_calls": seranking.calls_made,
            "log_dir": str(run_dir),
        }
        (run_dir / "_run_summary.json").write_text(json.dumps(run_summary, indent=2))
        print(f"\nRejected in {elapsed:.0f}s - {deepseek.calls_made} DeepSeek call(s), no article written.")
        return run_summary

    outline = final_state["outline"]
    article = final_state["article"]
    length_report = final_state["length_report"]
    questions_report = final_state["questions_report"]
    qa_report = final_state["qa_report"]
    scraped_competitors = final_state["scraped_competitors"]
    scraped_count = sum(1 for c in scraped_competitors if c["scraped"])
    reddit_threads = final_state.get("reddit_threads") or []
    twitter_threads = final_state.get("twitter_threads") or []
    section_candidates = final_state["section_candidates"]
    total_candidates = sum(len(sc["candidates"]) for sc in section_candidates)
    validated = final_state["validated_keywords"]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slugify(keyword)
    md_output_path = OUTPUT_DIR / f"{slug}-graph.md"
    md_output_path.write_text(article)

    html_output_path = OUTPUT_DIR / f"{slug}-graph.html"
    _write_html(article, outline["title"], outline["meta_description"], html_output_path)

    hard_word_total = sum(s["target_word_count"] for s in outline["sections"])
    statuses = [r["status"] for r in length_report]
    hard_targets_used = {
        "target_word_count": hard_word_total,
        "target_paragraph_count": sum(s["target_paragraph_count"] for s in outline["sections"]),
        "target_heading_count": sum(len(s.get("subheadings") or []) for s in outline["sections"]),
    }
    run_summary = {
        "run_id": run_id,
        "keyword": keyword,
        "engine": "langgraph",
        "elapsed_seconds": round(elapsed, 1),
        "deepseek_calls": deepseek.calls_made,
        "deepseek_tokens": deepseek.total_tokens,
        "scrappa_calls": scrappa.calls_made,
        "seranking_calls": seranking.calls_made,
        "search_intent": final_state["search_intent"].get("primary_intent"),
        "reddit_threads_found": len(reddit_threads),
        "twitter_threads_found": len(twitter_threads),
        "competitors_scraped": f"{scraped_count}/{len(scraped_competitors)}",
        "core_topics_found": len(final_state["common_content"].get("core_topics") or []),
        "vocabulary_terms_kept": len(final_state["vocabulary"].get("terms") or []),
        "outline_sections": len(outline["sections"]),
        "length_target_reliable": outline.get("length_target_reliable"),
        "length_target_sample_size": outline.get("length_target_sample_size"),
        "hard_targets": hard_targets_used,
        "candidates_generated": total_candidates,
        "candidates_validated": len(validated),
        "article_word_count": len(article.split()),
        "article_word_count_clean": count_words(article),
        "article_paragraph_count": count_paragraphs(article),
        "article_reading_grade": flesch_kincaid_grade(article),
        "sections_readability_fixed": sum(1 for r in length_report if r.get("readability", {}).get("rounds", 0) > 0),
        "sections_readability_rejected": sum(1 for r in length_report if r.get("readability", {}).get("status") == "entity_rejected"),
        "keywords_missing_before": sum(r.get("keyword_coverage", {}).get("missing_before", 0) for r in length_report),
        "keywords_missing_after": sum(r.get("keyword_coverage", {}).get("missing_after", 0) for r in length_report),
        "subheadings_missing_before": sum(r.get("subheading_coverage", {}).get("missing_before", 0) for r in length_report),
        "subheadings_missing_after": sum(r.get("subheading_coverage", {}).get("missing_after", 0) for r in length_report),
        "questions_required": questions_report["checked"],
        "questions_fixed": questions_report["fixed"],
        "questions_unfixable": questions_report["unfixable"],
        "questions_skipped_off_topic": questions_report["skipped"],
        "enforce_target_words": sum(r["enforce_target"] for r in length_report),
        "condense_calls": sum(r["rounds"] for r in length_report),
        "truncation_retries": deepseek.truncation_retries,
        "sections_in_band": statuses.count("in_band"),
        "sections_stalled": statuses.count("stalled") + statuses.count("rounds_exhausted"),
        "sections_entity_rejected": statuses.count("entity_rejected"),
        "sections_under_target": statuses.count("under_target"),
        "qa_issue_count": len(qa_report.get("issues") or []),
        "title": outline["title"],
        "meta_description": outline["meta_description"],
        "md_output_path": str(md_output_path),
        "html_output_path": str(html_output_path),
        "log_dir": str(run_dir),
    }
    run_summary["quality_gate"] = evaluate_run(run_summary)
    run_summary["cost"] = compute_run_cost(deepseek.call_log, scrappa.calls_made, seranking.calls_made)
    (run_dir / "_run_summary.json").write_text(json.dumps(run_summary, indent=2))

    gate = run_summary["quality_gate"]
    cost = run_summary["cost"]
    print(f"\nDone in {elapsed:.0f}s (graph engine). Quality gate: {gate['verdict'].upper()}")
    for reason in gate["reasons"]:
        print(f"  - {reason}")
    print(
        f"Cost: ${cost['total_cost_usd']:.4f} total "
        f"(DeepSeek ${cost['deepseek']['cost_usd']:.4f} / {cost['deepseek']['total_tokens']} tokens, "
        f"Scrappa ${cost['scrappa']['cost_usd']:.4f}, SE Ranking ${cost['seranking']['cost_usd']:.4f})"
    )
    print(f"Markdown: {md_output_path}")
    print(f"HTML: {html_output_path}")
    print(f"Logs: {run_dir}")
    return run_summary
