"""Step 9 - outline creation.

Builds the section-by-section outline that everything downstream depends
on. Two things are deliberately NOT left to the LLM's judgment when reliable
competitor data exists:

1. The article's total word count, paragraph count, and heading count are
   computed in CODE from competitors that DIRECTLY address this keyword
   (see pipeline/competitor_scraping.py's compute_competitor_stats) - the
   rule is that competitor structure governs length, not an LLM's estimate.
   The LLM distributes these totals across sections; a post-processing pass
   then rescales its per-section numbers so they sum to EXACTLY the
   competitor-derived totals, because an LLM's arithmetic across many
   sections is not reliably exact.

   This hard-enforcement only applies when compute_competitor_stats reports
   `reliable: True` - at least MIN_RELEVANT_COMPETITORS pages that actually
   answer this specific keyword, not just the same general category. When
   it's False (measured: one run's SERP surfaced mostly generic "backpacking
   101" content for "ultralight backpacking gear list", and their median
   word count was a real but meaningless number), the hard requirement is
   dropped entirely - length becomes soft guidance driven by what the
   content actually needs (core topic count, gap complexity, search intent),
   the same way it worked before competitor-derived targets existed.

2. Which topics are mandatory. Core topics (the ~70% signal - see
   pipeline/common_content.py) get is_core=True and cannot be dropped; gap-
   and trend-driven sections (the ~30%) get is_core=False and are the part
   that can flex if the word budget is tight. This applies regardless of
   whether the length target is hard or soft.
"""

from clients.deepseek_client import DeepSeekClient
from models import ArticleOutline, HeadingPaddingResult
from pipeline.competitor_scraping import (
    MIN_RELEVANT_COMPETITORS,
    compute_competitor_stats,
    format_competitors_for_prompt,
)
from pipeline.length_control import ensure_exact_phrase

_SHARED_PROMPT = """Design an article outline for the target keyword using every research input \
provided: the full competitor content and structure, the core topics competitors consistently \
cover, the topic's vocabulary (real entities/terms/phrases from competitors), the content gaps \
identified, the community/trend research, and the search intent.

- Roughly 70% of the article (by content, not necessarily exact word count) should be the core \
topics - mark these sections is_core=true. The rest can come from the content gaps and trend \
research - mark these is_core=false. This 70/30 split is a guideline, not a hard percentage; \
adjust it if the research clearly calls for something different.
- Phrase headings as the actual question a user would ask, where a real question from the \
community research or PAA data fits - not a generic topic label when a question is available.
- Subheadings must name the real, specific vocabulary from competitor content provided (product \
names, specific terms) rather than generic category labels, for any section long enough to need \
subheadings.
- A "Frequently Asked Questions" section (or any section that exists specifically to answer \
several distinct real_questions) MUST have one subheading per question, each phrased as the \
actual question verbatim or near-verbatim - never leave this section's subheadings empty. \
Measured directly: a FAQ section written as flowing paragraphs with no question headings left \
readers with no way to tell which paragraph answers which question - the answers were fine, the \
missing question labels made the section unusable. This is a hard requirement for any FAQ-type \
section, not a stylistic nice-to-have.

TITLE - the hook:
Craft a specific, clickable title - not a generic "Ultimate Guide to X" template unless \
genuinely earned. Use a trending angle from the community research where it fits naturally; a \
title that draws on a real current pain point or debate beats a generic one. The title MUST \
contain the exact primary keyword phrase, verbatim - this is a hard requirement.

INTENT: honor the bluf_guidance provided - the outline's section order and what each section \
opens with should already be shaped to answer the intent first, not bury it.

HERO IMAGE: give image_search_terms - 2-4 generic, visual words for a stock-photo search, based \
on the title/angle you just chose, not the raw keyword. A stock site returns nothing useful for a \
narrow or branded keyword ("wild casino withdrawal time") but returns something real for a \
generic visual concept behind it ("online casino chips" or "person on phone paying"). Describe a \
scene or subject a photographer would actually shoot, not an abstract concept a camera can't \
capture.

Return JSON matching the required schema only."""

SYSTEM_PROMPT_HARD = f"""{_SHARED_PROMPT}

STRUCTURE - hard requirements, not suggestions:
- The article's total word count, paragraph count, and heading count are given below as hard \
target RANGES, computed directly from competitors that genuinely answer this keyword and are \
real articles (not product/store pages). Distribute totals across sections so the sums land \
within those ranges - you do not need to hit an exact number, a rescale pass will correct the \
arithmetic toward the middle of each range, but get reasonably close."""

SYSTEM_PROMPT_SOFT = f"""{_SHARED_PROMPT}

STRUCTURE - length is NOT competitor-derived this time:
Too few competitors actually answer this specific keyword directly (most of what's ranking is \
broader, adjacent content) - their word/paragraph counts would not be a meaningful target, so \
none is being enforced. Instead, size the article by what the content genuinely needs: give \
every core topic enough room to actually answer it, cover the gap/trend sections without padding, \
and satisfy the search intent completely. If unsure, aim for roughly 2500-4000 words total - \
comprehensive does not mean padded, and a shorter, sharper article that fully satisfies intent \
beats a longer one that pads to hit a number nobody asked for."""


def _relevance_flags(competitor_research: dict, scraped_competitors: list[dict]) -> list:
    """Positional match between competitor_research's judgments and the
    scraped competitor list they were derived from - both are built by
    iterating scraped_competitors in the same order (see
    format_competitors_for_prompt and this prompt's explicit "same order"
    instruction). A length mismatch (the LLM returning a different count
    than it was given) defaults the unmatched entries to False rather than
    risk trusting an unverified page's stats."""
    entries = competitor_research.get("competitors") or []
    flags = [bool(e.get("directly_addresses_keyword")) for e in entries]
    if len(flags) < len(scraped_competitors):
        flags += [False] * (len(scraped_competitors) - len(flags))
    return flags[: len(scraped_competitors)]


def _content_types(competitor_research: dict, scraped_competitors: list[dict]) -> list:
    """Same positional-match pattern as _relevance_flags, but carrying each
    competitor's content_type (guide, listicle, product_page, etc.) so
    compute_competitor_stats can exclude non-article pages - an App Store
    listing or store page scraped alongside genuine guides shouldn't drag
    the structural targets toward its own shape. Unmatched entries default
    to "other" (kept in the pool) rather than "product_page" (excluded) -
    a missing classification isn't evidence the page is non-article."""
    entries = competitor_research.get("competitors") or []
    types = [e.get("content_type") or "other" for e in entries]
    if len(types) < len(scraped_competitors):
        types += ["other"] * (len(scraped_competitors) - len(types))
    return types[: len(scraped_competitors)]


RANGE_TARGET_RATIO = 0.8


def _range_target(min_v: float, max_v: float) -> int:
    """A point biased toward the top of the competitor-derived range, not
    the midpoint. Measured directly against real SE Ranking briefs: the
    displayed target for words, paragraphs, and headings consistently sits
    at or near the top of what real competitors do - the fuller,
    comprehensive pages - not the average of a thin outlier and a thorough
    one. Landing exactly on the range's midpoint hit the word target
    early on (competitors run a wide 1,100-4,300 words) but consistently
    left headings and paragraphs short even after they were both correctly
    computed and correctly rendered - the range itself was right, the point
    picked inside it wasn't. 80%, not 100%, so one unusually exhaustive
    competitor doesn't single-handedly set the bar."""
    return round(min_v + RANGE_TARGET_RATIO * (max_v - min_v))


def _rescale_targets(outline: dict, hard_targets: dict) -> dict:
    """Deterministically rescales the LLM's per-section word/paragraph
    targets so they sum to a point near the top of the competitor-derived
    min-max range (see _range_target) - an LLM's own arithmetic across many
    sections is not reliable enough to trust as-is (this is the same lesson
    as length_control.py: code measures, code corrects, the model only
    estimates). Only called when hard_targets is reliable - see module
    docstring."""
    sections = outline["sections"]
    if not sections:
        return outline

    for field, total_key in (
        ("target_word_count", "target_word_count"),
        ("target_paragraph_count", "target_paragraph_count"),
    ):
        # Every section needs at least 1. Floor the total at the section
        # count so that minimum is always achievable - found by testing: a
        # paragraph-count bug upstream once produced a hard total of 1 for
        # 17 sections, and dumping the resulting -16 drift onto one section
        # gave it a target of -15 paragraphs. Flooring here means a bad
        # upstream number gets absorbed as "1 per section", not propagated
        # as a negative target.
        range_target = _range_target(
            hard_targets[f"{total_key}_min"], hard_targets[f"{total_key}_max"]
        )
        target_total = max(range_target, len(sections))
        current_sum = sum(s[field] for s in sections)

        if current_sum <= 0:
            base, remainder = divmod(target_total, len(sections))
            for i, s in enumerate(sections):
                s[field] = base + (1 if i < remainder else 0)
            continue

        scale = target_total / current_sum
        rescaled = [max(1, round(s[field] * scale)) for s in sections]

        # Apply rounding drift one unit at a time, largest section first,
        # never taking any section below 1 - safe regardless of how large
        # the drift is relative to the number of sections (unlike dumping
        # the whole remainder on a single section, which can go negative).
        drift = target_total - sum(rescaled)
        step = 1 if drift > 0 else -1
        order = sorted(range(len(rescaled)), key=lambda i: -rescaled[i])
        remaining, guard = abs(drift), abs(drift) + len(order) * 3
        i = 0
        while remaining > 0 and guard > 0:
            idx = order[i % len(order)]
            if step > 0 or rescaled[idx] > 1:
                rescaled[idx] += step
                remaining -= 1
            i += 1
            guard -= 1

        for s, val in zip(sections, rescaled):
            s[field] = val

    # Total headings = each section's own heading (an H2 in the final
    # article) PLUS its subheadings (H3s) - the competitor-derived range is
    # a count of both together (that's what SE Ranking's own heading count
    # measures), not subheadings alone. Comparing the range only against
    # total_subheads was a bug: a 16-section outline with a handful of
    # subheadings could sit at "17 total headings" while looking like it had
    # room to add 10+ more, because the 16 H2s themselves were never counted.
    total_headings = len(sections) + sum(len(s.get("subheadings") or []) for s in sections)
    heading_max = hard_targets["target_heading_count_max"]
    heading_target = _range_target(hard_targets["target_heading_count_min"], heading_max)

    if total_headings > heading_max:
        _trim_subheadings(sections, total_headings - heading_max)
        outline["heading_shortfall"] = 0
    elif total_headings < heading_target:
        # Under-budget used to be left as-is on the theory that inventing
        # generic subheadings would be worse than having fewer, genuinely
        # useful ones. True for arbitrary padding, but not for a real gap
        # against a competitor-measured target - build_strategy() closes
        # this with a targeted LLM call (_pad_headings) instead of silently
        # accepting the shortfall. Measured against heading_target (near
        # the top of the range), not the range's bare minimum - "inside the
        # range" was too easy a bar to clear and consistently left real
        # articles well short of what SE Ranking's own brief wanted.
        outline["heading_shortfall"] = heading_target - total_headings
    else:
        outline["heading_shortfall"] = 0

    return outline


def _trim_subheadings(sections: list[dict], excess: int) -> None:
    """Removes `excess` subheadings total, from the sections with the most
    first. Unlike the old version, this can cut a section to zero
    subheadings - its own section heading still counts as one heading, so
    zero subheadings doesn't mean zero headings for that section."""
    order = sorted(range(len(sections)), key=lambda i: -len(sections[i].get("subheadings") or []))
    for i in order:
        if excess <= 0:
            break
        subs = sections[i].get("subheadings") or []
        cut = min(excess, len(subs))
        if cut:
            sections[i]["subheadings"] = subs[: len(subs) - cut]
            excess -= cut


PADDING_SYSTEM_PROMPT = """The article outline below is short of the competitor-derived heading \
count target by exactly {shortfall} headings. Add {shortfall} new subheadings TOTAL across the \
sections, distributed only to sections that genuinely have more ground to cover - not spread \
evenly for its own sake, and not forced onto a section that's already complete. Each new \
subheading must be as specific as the outline's existing ones: real entities/terms from the \
topic vocabulary provided, phrased as an actual question where one fits naturally - never a \
generic filler heading like "Additional Considerations" or "Other Factors". section_heading in \
your response must exactly match one of the outline's existing section headings, verbatim.

Return JSON matching the required schema only."""


def _pad_headings(
    deepseek: DeepSeekClient, keyword: str, outline: dict, shortfall: int, vocab_terms: str,
) -> dict:
    """Mirror of _trim_subheadings for the under-budget case: asks for real,
    specific subheadings rather than leaving a competitor-measured heading
    gap unfilled. See _rescale_targets's heading_shortfall."""
    sections_block = "\n".join(
        f"- \"{s['heading']}\" (covers: {s.get('covers', '')}) - current subheadings: "
        f"{s.get('subheadings') or '(none)'}"
        for s in outline["sections"]
    )
    user_prompt = (
        f"Keyword: {keyword}\n\nCurrent outline:\n{sections_block}\n\n"
        f"Topic vocabulary: {vocab_terms or '(none extracted)'}"
    )
    result = deepseek.structured_call(
        PADDING_SYSTEM_PROMPT.format(shortfall=shortfall), user_prompt, HeadingPaddingResult,
        label="pad_headings",
    )
    additions = {a.section_heading: a.new_subheadings for a in result.additions}
    for s in outline["sections"]:
        extra = additions.get(s["heading"])
        if extra:
            s["subheadings"] = (s.get("subheadings") or []) + extra
    return outline


_FAQ_HEADING_MARKERS = ("frequently asked question", "faq")


def _backfill_faq_subheadings(outline: dict, community_research: dict) -> None:
    """Deterministic backstop for the prompt instruction above - measured
    directly: a FAQ section written as flowing paragraphs with zero
    subheadings left readers with no way to tell which paragraph answers
    which question. No LLM call needed: real_questions_to_answer already
    has the exact question text, so this is a direct assignment, not a
    generation task."""
    questions = community_research.get("real_questions_to_answer") or []
    if not questions:
        return
    for s in outline["sections"]:
        heading_lower = s["heading"].lower()
        if any(marker in heading_lower for marker in _FAQ_HEADING_MARKERS) and not s.get("subheadings"):
            s["subheadings"] = list(questions)


META_DESCRIPTION_MAX_CHARS = 155


def _fix_meta_description(deepseek, description: str, keyword: str) -> str:
    """RankMath's "Focus Keyword not found in your SEO Meta Description"
    check (and Google's own SERP snippet) effectively cuts a description
    off around ~155 characters - a keyword phrase placed after that point
    might as well not be there. Measured on a real 14-article batch:
    several descriptions ran 185-243 characters, well past that cutoff,
    which explains "not found" flags even on descriptions that technically
    contained the phrase later in the text. ensure_exact_phrase() alone
    (used for the title) only checks presence, not length - this enforces
    both in one corrective pass."""
    if len(description) <= META_DESCRIPTION_MAX_CHARS and keyword.lower() in description.lower():
        return description

    system_prompt = (
        "Rewrite this SEO meta description so it is AT MOST 155 characters total "
        "(count every character including spaces and punctuation) AND contains the "
        f'exact phrase "{keyword}" verbatim, case-insensitive. Keep the same core '
        "claim/hook - trim or rephrase around it, don't invent new facts. Return "
        "ONLY the corrected description text, nothing else - no quotes, no preamble."
    )
    fixed = deepseek.generate(
        system_prompt, description, temperature=0.3, label="fix_meta_description",
    ).strip().strip('"')

    if len(fixed) > META_DESCRIPTION_MAX_CHARS or keyword.lower() not in fixed.lower():
        # Absolute safety net if the corrective pass still overshoots -
        # the keyword goes at the very front, where truncation can never
        # cut it off, rather than trusting the model a second time.
        fixed = f"{keyword.capitalize()}: {description}"[:META_DESCRIPTION_MAX_CHARS].rsplit(" ", 1)[0]
    return fixed


def build_strategy(
    deepseek: DeepSeekClient,
    keyword: str,
    competitor_research: dict,
    common_content: dict,
    vocabulary: dict,
    content_gaps: dict,
    community_research: dict,
    search_intent: dict,
    scraped_competitors: list[dict],
) -> dict:
    relevance_flags = _relevance_flags(competitor_research, scraped_competitors)
    content_types = _content_types(competitor_research, scraped_competitors)
    hard_targets = compute_competitor_stats(scraped_competitors, relevance_flags, content_types)
    vocab_terms = ", ".join(t["term"] for t in vocabulary.get("terms") or [])

    if hard_targets["reliable"]:
        system_prompt = SYSTEM_PROMPT_HARD
        length_block = (
            f"HARD structural targets (computed from {hard_targets['sample_size']} competitors "
            f"that directly answer this keyword and are genuine articles, min-max range):\n"
            f"  Total word count: {hard_targets['target_word_count_min']}-{hard_targets['target_word_count_max']}\n"
            f"  Total paragraph count: {hard_targets['target_paragraph_count_min']}-{hard_targets['target_paragraph_count_max']}\n"
            f"  Total heading count: {hard_targets['target_heading_count_min']}-{hard_targets['target_heading_count_max']}"
        )
    else:
        system_prompt = SYSTEM_PROMPT_SOFT
        length_block = (
            f"Length guidance: NOT competitor-derived this run - only {hard_targets['sample_size']} "
            f"scraped competitors directly address this keyword (need {MIN_RELEVANT_COMPETITORS}). "
            f"Size the article by what the content needs; see the system instructions."
        )

    user_prompt = (
        f"Keyword: {keyword}\n\n"
        f"{length_block}\n\n"
        f"Search intent: {search_intent.get('primary_intent')}\n"
        f"BLUF guidance: {search_intent.get('bluf_guidance')}\n\n"
        f"Format overview: {competitor_research.get('dominant_format')} - "
        f"{competitor_research.get('format_rationale')}\n\n"
        f"Core topics (covered by most/all competitors - the ~70% signal):\n"
        + "\n".join(
            f"  - {t['topic']} (covered by {t['covered_by_count']} competitors): {t['why_essential']}"
            for t in common_content.get("core_topics") or []
        )
        + "\n\n"
        f"Topic vocabulary (real terms from competitor content):\n{vocab_terms or '(none extracted)'}\n\n"
        f"Content gaps (the ~30% differentiation signal):\n{content_gaps}\n\n"
        f"Community/trend research:\n{community_research}\n\n"
        f"Full competitor content and structure:\n{format_competitors_for_prompt(scraped_competitors)}"
    )
    result = deepseek.structured_call(system_prompt, user_prompt, ArticleOutline, label="article_strategy")
    outline = result.model_dump()
    _backfill_faq_subheadings(outline, community_research)

    outline["title"] = ensure_exact_phrase(
        deepseek, outline["title"], keyword,
        "This is an article's title/headline - it must stay a single short, clickable line.",
    )
    outline["meta_description"] = _fix_meta_description(deepseek, outline["meta_description"], keyword)

    if hard_targets["reliable"]:
        outline = _rescale_targets(outline, hard_targets)
        shortfall = outline.pop("heading_shortfall", 0)
        if shortfall > 0:
            outline = _pad_headings(deepseek, keyword, outline, shortfall, vocab_terms)
            # The LLM's padding count is guidance, not exact - if it
            # overshot, re-apply the same trim used in _rescale_targets
            # rather than leaving the range's ceiling unenforced.
            total_headings = len(outline["sections"]) + sum(
                len(s.get("subheadings") or []) for s in outline["sections"]
            )
            heading_max = hard_targets["target_heading_count_max"]
            if total_headings > heading_max:
                _trim_subheadings(outline["sections"], total_headings - heading_max)
    else:
        # No hard target to rescale to - still fill in a target_paragraph_count
        # per section if the LLM omitted one, so length_control.py and the
        # writer's paragraph guidance always have a usable number.
        for s in outline["sections"]:
            if not s.get("target_paragraph_count"):
                s["target_paragraph_count"] = max(1, round(s["target_word_count"] / 90))

    outline["length_target_reliable"] = hard_targets["reliable"]
    outline["length_target_sample_size"] = hard_targets["sample_size"]
    return outline
