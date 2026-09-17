from typing import Optional
from pydantic import BaseModel, Field


# --- step 2: search intent -------------------------------------------------

class SearchIntent(BaseModel):
    primary_intent: str  # informational, commercial, transactional, navigational, mixed
    rationale: str
    # What "answering this intent first" concretely means for this keyword -
    # feeds the BLUF instruction directly into the writer, e.g. "state the
    # single best product/answer in the first sentence, then justify it."
    bluf_guidance: str
    # False for local/"near me"-style queries where the real winning SERP
    # format is a Google Maps pack or a store-locator page, not an article -
    # no amount of writing quality can outrank a map. Found by testing:
    # "plus size clothing stores nearby" hit every structural target
    # (word count, headings) and the score barely moved, because the
    # problem was never the article's execution - it's that this query
    # can't be won with content at all.
    #
    # Judge this from the keyword's own wording FIRST ("near me", "nearby",
    # "in [city]", "open now", "hours", "directions to" are reliable signals
    # on their own) - do not require SERP confirmation. Found by testing:
    # Scrappa's SERP for this exact keyword came back completely unrelated
    # (results about an unrelated organization that happened to share the
    # word "plus") on one run, and a classifier that needed the SERP to
    # agree missed an obviously local-intent keyword because the SERP data
    # was garbage that day. The keyword's own plain meaning is the more
    # reliable signal; the SERP is corroborating evidence, not the source
    # of truth.
    suitable_for_article: bool = True
    unsuitability_reason: Optional[str] = None  # set only when suitable_for_article is False
    # True unless the SERP is genuinely unrelated to the keyword (a scraping/
    # search hiccup, not a real signal about the query). Surfaced so a run
    # where this went False is visible in the logs, not silently trusted.
    serp_matches_keyword: bool = True


# --- step 4: structure & quality analysis -----------------------------------

class CompetitorEntry(BaseModel):
    title: str
    content_type: str  # guide, listicle, comparison, review, product_page, forum_thread, other
    angle: str
    structure_notes: str  # how it organizes headings/tables - flat list, deep nesting, comparison tables, etc.
    quality_notes: str  # thin vs. thorough, current vs. stale, generic vs. specific
    # False for a page that merely ranks in the same general category
    # ("backpacking 101") without actually answering THIS keyword's specific
    # request. Gates whether this competitor's word/paragraph/heading counts
    # count toward the hard structural targets in article_strategy.py - an
    # off-topic page that happens to rank tells you nothing about how long a
    # genuine answer to this keyword should be.
    directly_addresses_keyword: bool


class CompetitorResearch(BaseModel):
    competitors: list[CompetitorEntry]
    dominant_format: str
    format_rationale: str


# --- step 5: common content (the ~70% core signal) --------------------------

class CoreTopic(BaseModel):
    topic: str
    covered_by_count: int  # how many of the scraped competitors cover this
    why_essential: str


class CommonContentAnalysis(BaseModel):
    core_topics: list[CoreTopic]


# --- step 6: common language & vocabulary ------------------------------------

class VocabularyTerm(BaseModel):
    term: str
    category: str  # entity, concept, term, phrase
    document_frequency: int  # how many competitors' text this appeared in (code-computed, carried through)


class TopicVocabulary(BaseModel):
    terms: list[VocabularyTerm]


# --- step 7: content gap analysis --------------------------------------------

class ContentGap(BaseModel):
    gap: str
    why_it_matters: str
    opportunity_type: str  # underexplained, missing_comparison, outdated, no_examples, other


class ContentGapAnalysis(BaseModel):
    gaps: list[ContentGap]
    differentiation_angle: str


# --- step 8: community / trend research ---------------------------------

class CommunityInsight(BaseModel):
    theme: str
    evidence: str  # which PAA question, related search, or social thread this came from


class CommunityResearch(BaseModel):
    insights: list[CommunityInsight]
    real_questions_to_answer: list[str]
    # Framings/phrasing patterns actually resonating in current discussion -
    # feeds the title-crafting (hook) instruction directly. Empty when no
    # social threads were found this run, which is fine; the title still
    # gets written, just without this signal.
    trending_angles: list[str] = Field(default_factory=list)


# --- step 9: outline creation ------------------------------------------------

class OutlineSection(BaseModel):
    heading: str  # phrased as the question users actually ask, where one fits
    subheadings: list[str] = Field(default_factory=list)
    covers: str  # what this section must accomplish
    target_word_count: int
    target_paragraph_count: int
    # True for core-topic sections (the ~70%): omitting one is a hard
    # failure, not a style choice. False for gap/trend-driven sections
    # (the ~30%) - valuable, but the budget that can flex.
    is_core: bool = True


class ArticleOutline(BaseModel):
    title: str  # the hook - trend/sentiment-informed, must contain the exact keyword verbatim
    meta_description: str
    sections: list[OutlineSection]
    # 2-4 generic visual search terms for a stock-photo hero image - grounded
    # in the title/angle already decided here, not the raw keyword (a
    # branded/narrow keyword like "wild casino withdrawal time" returns
    # nothing on a stock site; "online casino payment" does).
    image_search_terms: str = ""


# --- step 9b: heading count repair -------------------------------------------
# Mirrors step 11's "measure, don't just instruct" pattern: _rescale_targets
# in article_strategy.py counts the outline's actual total headings (each
# section's own heading plus its subheadings) against the competitor-derived
# range and, when short, asks for exactly this many additional real
# subheadings rather than leaving the gap unfilled.

class HeadingAddition(BaseModel):
    section_heading: str  # must exactly match one of the outline's existing section headings
    new_subheadings: list[str]


class HeadingPaddingResult(BaseModel):
    additions: list[HeadingAddition]


# --- step 10: per-section keyword expansion -----------------------------------

class SectionKeywordCandidates(BaseModel):
    section_heading: str
    candidates: list[str]


class SectionKeywordExpansion(BaseModel):
    sections: list[SectionKeywordCandidates]


# --- step 11: article writing - unanswered-question repair -------------------
# The SEO QA step (12) was repeatedly finding required questions were never
# explicitly answered anywhere in the article, despite being on the
# real_questions_to_answer list every section had access to. This closes
# that gap the same way word count and keyword-phrase compliance were
# closed: measure whether it actually happened, and if not, inject a
# targeted fix rather than trust the "answer it where natural" instruction.

class UnansweredQuestionFix(BaseModel):
    question: str
    target_section_heading: str  # must exactly match one of the outline's section headings
    answer: str  # 2-4 sentences, grounded in facts already in the article


class SkippedQuestion(BaseModel):
    question: str
    why_not_relevant: str


class UnansweredQuestionsReport(BaseModel):
    fixes: list[UnansweredQuestionFix]
    # Questions that only superficially matched this keyword (a word like
    # "plus" triggering an unrelated PAA result about an insurance trade
    # org, or "LGBTQ+") - forcing an answer for these was measured to
    # inject genuinely wrong content into the article. Recorded explicitly
    # rather than silently omitted, so the decision to skip is visible in
    # the logs, not indistinguishable from a bug.
    skipped: list[SkippedQuestion] = Field(default_factory=list)


# --- step 12: SEO QA -------------------------------------------------------

class SeoQaIssue(BaseModel):
    severity: str  # high, medium, low
    issue: str
    suggestion: str


class SeoQaReport(BaseModel):
    issues: list[SeoQaIssue]
    covers_all_outline_sections: bool
    covers_paa_questions: bool
    keyword_usage_assessment: str
    overall_assessment: str


# --- step 14: interlinking / backlinking -------------------------------------
# Same "measure, don't just instruct" shape as UnansweredQuestionFix above:
# the LLM only proposes an anchor + URL pair, never rewrites article text
# itself - code deterministically finds the anchor_text verbatim and wraps
# it, and rejects any url that isn't one of the candidates it was actually
# given. This is what keeps a hallucinated URL out of a real WordPress post.

class InternalLinkPlacement(BaseModel):
    anchor_text: str  # must be an exact substring already present in the article
    url: str          # must be one of the candidate URLs given - never invented


class InternalLinkingResult(BaseModel):
    links: list[InternalLinkPlacement]
