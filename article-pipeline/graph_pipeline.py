"""Phase 1+2 of the LangGraph migration (see README/conversation for the
full scope): the same thirteen steps orchestrator.py runs sequentially,
wired as LangGraph nodes over one shared PipelineState instead of threaded
through positional function arguments.

Deliberately NOT changed in this phase:
- No Postgres/Supabase checkpointer yet - build_graph() takes a checkpointer
  so that swap is a one-line change later, but this phase uses
  MemorySaver (in-process, lost when the run ends) to test the graph
  wiring itself in isolation.
- No parallel section-writing (that's the separate, riskier Phase 3 - it
  changes write_article()'s internals, not just how steps are wired).
- Every pipeline/*.py function is called completely unchanged - this phase
  is pure rewiring, not a logic change, so any behavior difference between
  this and orchestrator.py's output would point at a wiring bug, not a
  prompt/logic difference.

One real structural improvement over orchestrator.py: reddit_research and
twitter_research run as two independent nodes that both feed into
community_research - LangGraph's Pregel executor waits for both branches
to finish before running a node with multiple incoming edges, which is the
fan-out/fan-in orchestrator.py's linear script can't express without extra
threading code.
"""

from typing import Callable, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from graph_state import PipelineState
from pipeline.article_strategy import build_strategy
from pipeline.article_writer import write_article
from pipeline.common_content import find_common_content
from pipeline.community_research import research_community
from pipeline.competitor_research import research_competitors
from pipeline.competitor_scraping import scrape_competitors
from pipeline.content_gap_analysis import analyze_content_gaps
from pipeline.keyword_expansion import expand_keywords_per_section
from pipeline.keyword_validation import regroup_by_section, validate_keywords
from pipeline.reddit_research import research_reddit
from pipeline.search_intent import detect_search_intent
from pipeline.seo_qa import run_qa
from pipeline.serp_research import research_serp
from pipeline.twitter_research import research_twitter
from pipeline.vocabulary_extraction import extract_vocabulary

REDDIT_THREAD_LIMIT = 15


def build_graph(
    deepseek, scrappa, seranking, settings, log_step: Callable, html_dir,
    checkpointer=None,
):
    """log_step and html_dir are passed straight through to the same
    per-step JSON/markdown logging orchestrator.py already does - that
    behavior isn't part of this migration phase, only how steps are wired
    together is. checkpointer defaults to an in-memory saver; pass a
    Postgres-backed one (Phase 5, once Supabase is wired up) without
    touching anything below."""

    def node_serp_research(state: PipelineState) -> dict:
        serp = research_serp(scrappa, state["keyword"], settings.competitor_count)
        log_step("serp_research", serp)
        return {"serp": serp}

    def node_search_intent(state: PipelineState) -> dict:
        search_intent = detect_search_intent(deepseek, state["keyword"], state["serp"])
        log_step("search_intent", search_intent)
        return {"search_intent": search_intent}

    def route_after_search_intent(state: PipelineState) -> str:
        if not state["search_intent"].get("suitable_for_article", True):
            return "rejected"
        return "continue"

    def node_rejected(state: PipelineState) -> dict:
        return {"rejected": True}

    def node_competitor_scraping(state: PipelineState) -> dict:
        scraped = scrape_competitors(state["serp"]["top_results"], html_dir=html_dir)
        log_step("competitor_scraping", scraped)
        return {"scraped_competitors": scraped}

    def node_competitor_research(state: PipelineState) -> dict:
        research = research_competitors(deepseek, state["keyword"], state["scraped_competitors"])
        log_step("competitor_research", research)
        return {"competitor_research": research}

    def node_common_content(state: PipelineState) -> dict:
        common_content = find_common_content(deepseek, state["keyword"], state["scraped_competitors"])
        log_step("common_content", common_content)
        return {"common_content": common_content}

    def node_vocabulary_extraction(state: PipelineState) -> dict:
        vocabulary = extract_vocabulary(deepseek, state["keyword"], state["scraped_competitors"])
        log_step("vocabulary_extraction", vocabulary)
        return {"vocabulary": vocabulary}

    def node_content_gap_analysis(state: PipelineState) -> dict:
        content_gaps = analyze_content_gaps(
            deepseek, state["keyword"], state["competitor_research"],
            state["common_content"], state["vocabulary"], state["scraped_competitors"],
        )
        log_step("content_gap_analysis", content_gaps)
        return {"content_gaps": content_gaps}

    def node_reddit_research(state: PipelineState) -> dict:
        threads = research_reddit(state["keyword"], limit=REDDIT_THREAD_LIMIT)
        log_step("reddit_research", threads)
        return {"reddit_threads": threads}

    def node_twitter_research(state: PipelineState) -> dict:
        threads = research_twitter(state["keyword"], limit=REDDIT_THREAD_LIMIT)
        log_step("twitter_research", threads)
        return {"twitter_threads": threads}

    def node_community_research(state: PipelineState) -> dict:
        threads = (state.get("reddit_threads") or []) + (state.get("twitter_threads") or [])
        community_research = research_community(deepseek, state["keyword"], state["serp"], threads)
        log_step("community_research", community_research)
        return {"community_research": community_research}

    def node_article_strategy(state: PipelineState) -> dict:
        outline = build_strategy(
            deepseek, state["keyword"], state["competitor_research"], state["common_content"],
            state["vocabulary"], state["content_gaps"], state["community_research"],
            state["search_intent"], state["scraped_competitors"],
        )
        log_step("article_strategy", outline)
        return {"outline": outline}

    def node_keyword_expansion(state: PipelineState) -> dict:
        section_candidates = expand_keywords_per_section(
            deepseek, state["keyword"], state["outline"], settings.max_keywords_per_section,
        )
        log_step("keyword_expansion", section_candidates)
        return {"section_candidates": section_candidates}

    def node_keyword_validation(state: PipelineState) -> dict:
        flat_candidates = [c for sc in state["section_candidates"] for c in sc["candidates"]]
        validated = validate_keywords(seranking, flat_candidates)
        section_keyword_map = regroup_by_section(state["section_candidates"], validated)
        log_step("keyword_validation", {"validated": validated, "section_map": section_keyword_map})
        return {"validated_keywords": validated, "section_keyword_map": section_keyword_map}

    def node_article_writing(state: PipelineState) -> dict:
        article, length_report, questions_report = write_article(
            deepseek, state["keyword"], state["outline"], state["content_gaps"],
            state["community_research"], state["vocabulary"], state["search_intent"],
            state["section_keyword_map"],
            log_step=lambda name, text: log_step(name, text),
        )
        log_step("article_full_draft", article)
        log_step("length_enforcement", length_report)
        log_step("questions_repair", questions_report)
        return {"article": article, "length_report": length_report, "questions_report": questions_report}

    def node_seo_qa(state: PipelineState) -> dict:
        qa_report = run_qa(
            deepseek, state["keyword"], state["article"], state["outline"],
            state["community_research"].get("real_questions_to_answer") or [],
            state["section_keyword_map"],
        )
        log_step("seo_qa", qa_report)
        return {"qa_report": qa_report}

    graph = StateGraph(PipelineState)
    graph.add_node("serp_research", node_serp_research)
    graph.add_node("search_intent", node_search_intent)
    graph.add_node("rejected", node_rejected)
    graph.add_node("competitor_scraping", node_competitor_scraping)
    graph.add_node("competitor_research", node_competitor_research)
    graph.add_node("common_content", node_common_content)
    graph.add_node("vocabulary_extraction", node_vocabulary_extraction)
    graph.add_node("content_gap_analysis", node_content_gap_analysis)
    graph.add_node("reddit_research", node_reddit_research)
    graph.add_node("twitter_research", node_twitter_research)
    graph.add_node("community_research", node_community_research)
    graph.add_node("article_strategy", node_article_strategy)
    graph.add_node("keyword_expansion", node_keyword_expansion)
    graph.add_node("keyword_validation", node_keyword_validation)
    graph.add_node("article_writing", node_article_writing)
    graph.add_node("seo_qa", node_seo_qa)

    graph.set_entry_point("serp_research")
    graph.add_edge("serp_research", "search_intent")
    graph.add_conditional_edges(
        "search_intent", route_after_search_intent,
        {"rejected": "rejected", "continue": "competitor_scraping"},
    )
    graph.add_edge("rejected", END)
    graph.add_edge("competitor_scraping", "competitor_research")
    graph.add_edge("competitor_research", "common_content")
    graph.add_edge("common_content", "vocabulary_extraction")
    graph.add_edge("vocabulary_extraction", "content_gap_analysis")
    # Fan-out: both run off content_gap_analysis independently.
    graph.add_edge("content_gap_analysis", "reddit_research")
    graph.add_edge("content_gap_analysis", "twitter_research")
    # Fan-in: community_research waits for both branches above to finish.
    graph.add_edge("reddit_research", "community_research")
    graph.add_edge("twitter_research", "community_research")
    graph.add_edge("community_research", "article_strategy")
    graph.add_edge("article_strategy", "keyword_expansion")
    graph.add_edge("keyword_expansion", "keyword_validation")
    graph.add_edge("keyword_validation", "article_writing")
    graph.add_edge("article_writing", "seo_qa")
    graph.add_edge("seo_qa", END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())
