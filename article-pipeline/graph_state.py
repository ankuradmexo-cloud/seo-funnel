"""Shared state schema for the LangGraph pipeline (graph_pipeline.py).

One field per value that used to be threaded manually through
orchestrator.py's function calls (build_strategy() alone took 9 positional
args). Every node reads what it needs from this dict and returns only the
keys it adds - LangGraph merges each node's return value into the running
state, so a node never has to pass along values it didn't touch.

total=False because the state is built up incrementally - early in a run,
most keys genuinely don't exist yet, and requiring every key up front would
fight that.
"""

from typing import Any, Optional, TypedDict


class PipelineState(TypedDict, total=False):
    keyword: str

    serp: dict
    search_intent: dict
    rejected: bool

    scraped_competitors: list
    competitor_research: dict
    common_content: dict
    vocabulary: dict
    content_gaps: dict

    reddit_threads: list
    twitter_threads: list
    community_research: dict

    outline: dict

    section_candidates: list
    validated_keywords: list
    section_keyword_map: dict

    article: str
    length_report: list
    questions_report: dict

    qa_report: dict
