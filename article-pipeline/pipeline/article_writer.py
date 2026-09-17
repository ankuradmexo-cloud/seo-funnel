"""Step 12 - article writing.

Written section by section, one generation per outline section - no separate
intro pass (the first section IS the article's opening).

Length mechanism (unchanged from earlier tuning - see length_control.py and
README's "Length: what was tried and what actually worked"): word targets
are enforced AFTER generation by measuring and condensing, not by asking the
model to hit a number from nothing; prior sections are passed as a compact
digest, not full prose, because full prose was itself the mechanism that
made overshoot compound section over section.

New in this version, per the writing-technique spec:

- BLUF (bottom-line-up-front): every section opens with the direct answer,
  driven by the exact bluf_guidance search_intent.py produced for this
  keyword - not a generic "lead with the answer" instruction, a specific one.
- AIDA for the opening section specifically (attention/interest/desire/
  action), since a hook and a BLUF answer are two different jobs the first
  section has to do at once.
- Paragraph count/length are no longer a fixed stylistic rule - they're
  derived from the section's own target_paragraph_count, which is itself
  rescaled from competitor medians in article_strategy.py. Whatever ratio
  that produces is what gets asked for, not a fixed "2-3 sentences."
- Assigned keywords now carry real search volume/difficulty (from bulk
  validation), not just a bare word list - the writer is told to use the
  ones worth the reward (real volume, fits naturally) and skip the rest,
  rather than being handed a flat list with no signal to judge by.
- Topic vocabulary (entities/terms/phrases real competitors use) is passed
  as a standing list to draw from across every section - this is what
  reaches actual body prose, not just outline subheadings.
- Plain language: written so it's followable in one read by a 6th-grade
  reader - short sentences, no unexplained jargon.
"""

from clients.deepseek_client import DeepSeekClient
from pipeline.length_control import (
    ENFORCE_SCALE,
    count_paragraphs,
    count_words,
    ensure_exact_phrase,
    ensure_keywords_present,
    ensure_subheadings_present,
    enforce_section_length,
    salient_terms,
    simplify_for_readability,
    subheading_budget,
)

_SHARED_RULES = """Write naturally for a human reader first - clear, specific, useful.

Lead with the bottom line: the BLUF guidance below tells you exactly what this section's \
opening sentence(s) must state directly before any nuance or explanation - follow it precisely, \
not just in spirit.

Keep language plain enough that a 6th-grade reader follows it in one read: short sentences, \
common words, and any technical term explained the moment it's first used. This does not mean \
dumbing down the content - it means not making the reader work to parse the sentence.

Paragraph rhythm: aim for the paragraph count and average paragraph length given below - these \
come from what actually ranks for this keyword, not a fixed style rule.

Weave in the topic vocabulary list where it fits this section's content - use the specific real \
terms (product names, technical terms) rather than a generic paraphrase of them. Use the \
assigned keywords that are worth it: each has real search volume and difficulty listed, so favor \
the ones with decent volume that fit naturally, and skip low-value or awkward ones rather than \
forcing every one in. Never keyword-stuff.

Use only the subheadings you are given, if any - do not invent additional "### " headings \
beyond that list."""

FIRST_SECTION_SYSTEM_PROMPT = f"""You are writing the opening of a long-form SEO article. Start \
with a "# " heading using the exact article title given, verbatim, then write this section's \
content under a "## " heading for the section itself.

This opening has two jobs at once: hook the reader (AIDA - grab Attention in the first \
sentence, build Interest by naming the real stakes, create Desire by showing what they'll walk \
away with, and an implicit Action of continuing to read) AND state the BLUF answer directly \
within the first few sentences - these are not in tension, the hook can BE the direct answer \
stated compellingly.

The literal primary keyword phrase MUST appear somewhere in the first 100 words - work it in \
naturally, but it must be the exact phrase, not a rephrased or reordered version of it.

{_SHARED_RULES}

Do not summarize sections that come later; that's their job, not this one's. Do not end with a \
takeaway or summary paragraph - this is the opening, not the conclusion."""

SECTION_SYSTEM_PROMPT = f"""You are writing one section of a long-form SEO article that has \
already been introduced. Write only this section's content in Markdown, starting with its \
heading as "## ". Do not restate the article title, and do not repeat points already covered \
elsewhere in the article (a list of what's already been covered is provided below).

{_SHARED_RULES}

Answer any of the article's target questions that this section is the natural place to answer. \
Do not add a conclusion, takeaway, or summary paragraph unless this section IS the conclusion."""


def _max_tokens_for(target_words: int) -> int:
    """A runaway guard, not a length control - the actual target is enforced
    after generation by length_control.py. Set high (3.0x) so it essentially
    never binds - see README for why a tighter cap made things worse, not
    better."""
    return max(900, int(target_words * 3.0))


def _assigned_subheadings(section: dict) -> list:
    requested = section.get("subheadings") or []
    n = subheading_budget(section["target_word_count"], requested=len(requested))
    return requested[:n]


def _subheading_instruction(given: list) -> str:
    if given:
        return (
            f'Use exactly these subheadings, and no others: {", ".join(given)}. '
            f'Do not invent additional "### " headings beyond this list.'
        )
    return 'Do not use any "### " subheadings in this section - flowing prose under the "## " heading only.'


def _keyword_block(assigned_keywords: list) -> str:
    if not assigned_keywords:
        return "(none assigned - write for the topic directly)"
    return ", ".join(
        f"{k['keyword']} (volume: {k.get('search_volume')}, difficulty: {k.get('difficulty')})"
        for k in assigned_keywords
    )


def _paragraph_guidance(section: dict) -> str:
    words = section["target_word_count"]
    paras = max(1, section.get("target_paragraph_count") or 1)
    return f"~{paras} paragraphs, averaging ~{round(words / paras)} words each"


def _build_digest(outline: dict, sections_done: list, texts_done: list) -> str:
    """A compact structural summary of everything written so far, replacing
    full prior prose. See module docstring - full prose was the compounding
    mechanism, not a neutral continuity aid."""
    if not texts_done:
        return "(nothing written yet - this is the article's opening)"

    lines = [f"Title: {outline['title']}", ""]
    covered_terms = set()
    for sec, text in zip(sections_done, texts_done):
        lines.append(f"## {sec['heading']}")
        for sh in sec.get("subheadings") or []:
            lines.append(f"  ### {sh}")
        lines.append(f"  covered: {sec['covers']}")
        covered_terms |= salient_terms(text)

    if covered_terms:
        lines.append("")
        lines.append(
            "Terms/products already named in the article - only re-mention one of these if "
            "you're adding a NEW fact about it (a different weight, price, or tradeoff), not "
            "repeating what's already been said:"
        )
        lines.append(", ".join(sorted(covered_terms)))

    tail = " ".join(texts_done[-1].split()[-120:])
    lines.append("")
    lines.append("--- End of the immediately preceding section, verbatim (for a smooth transition) ---")
    lines.append(tail)

    return "\n".join(lines)


def _ensure_title_heading(text: str, title: str) -> str:
    """See write_section's is_first block - a deterministic fix for a
    missing/wrong "# " title heading, no LLM call needed since there's
    exactly one correct value."""
    stripped = text.lstrip()
    expected = f"# {title}"
    parts = stripped.split("\n", 1)
    first_line = parts[0].strip()
    rest = parts[1] if len(parts) > 1 else ""
    if first_line == expected:
        return text
    if first_line.startswith("# "):
        return f"{expected}\n{rest}"
    return f"{expected}\n\n{stripped}"


def write_section(
    deepseek: DeepSeekClient,
    keyword: str,
    section: dict,
    assigned_keywords: list,
    real_questions: list,
    vocabulary_terms: list,
    bluf_guidance: str,
    digest: str,
    covered_terms: set,
    is_first: bool,
    title: str = "",
    differentiation_angle: str = "",
) -> tuple:
    """Returns (text, report) - report is length_control.py's per-section
    outcome, logged by the caller so a bad run is diagnosable from
    _run_summary.json rather than by reading the whole article."""
    questions_block = "\n".join(f"- {q}" for q in real_questions) or "(none)"
    vocab_block = ", ".join(vocabulary_terms) or "(none extracted)"
    enforce_target = round(section["target_word_count"] * ENFORCE_SCALE)
    assigned_subheadings = _assigned_subheadings(section)

    header = f"Primary keyword: {keyword}\nBLUF guidance for this section: {bluf_guidance}\n\n"
    if is_first:
        header += (
            f'Article title (use verbatim as the "# " heading): {title}\n'
            f"Differentiation angle: {differentiation_angle or '(none provided)'}\n\n"
        )

    user_prompt = (
        header
        + f"This section:\n"
        f"  Heading: {section['heading']}\n"
        f"  Subheadings: {_subheading_instruction(assigned_subheadings)}\n"
        f"  Must cover: {section['covers']}\n"
        f"  Target length: about {section['target_word_count']} words "
        f"({_paragraph_guidance(section)})\n"
        f"  Assigned keywords: {_keyword_block(assigned_keywords)}\n\n"
        f"Topic vocabulary to draw from where it fits: {vocab_block}\n\n"
        f"Real questions the article should answer (answer here only if this section is the "
        f"natural place to):\n{questions_block}\n\n"
        f"--- What's already in the article ---\n{digest}\n--- end ---"
    )
    system_prompt = FIRST_SECTION_SYSTEM_PROMPT if is_first else SECTION_SYSTEM_PROMPT
    draft = deepseek.generate_uncapped_if_truncated(
        system_prompt, user_prompt, temperature=0.5,
        max_tokens=_max_tokens_for(section["target_word_count"]),
        label=f"write_section:{section['heading'][:40]}",
    )
    keyword_strings = [k["keyword"] for k in assigned_keywords]
    text, report = enforce_section_length(
        deepseek, draft, section, enforce_target, keyword_strings, sorted(covered_terms),
    )

    # Assigned-keyword coverage, measured - the SEO QA step repeatedly found
    # a keyword listed as "assigned" to a section would sometimes never
    # appear in that section's text at all, despite the prompt asking for
    # it. Runs before readability so any newly-inserted sentence still gets
    # simplified along with the rest.
    text, keyword_report = ensure_keywords_present(deepseek, text, assigned_keywords, section)
    report["keyword_coverage"] = keyword_report
    if keyword_report["status"] == "fixed":
        report["after"] = count_words(text)
        report["paragraphs"] = count_paragraphs(text)

    # Subheading presence, measured - the prompt instruction to "use exactly
    # these subheadings" was found to fail silently on short sections: the
    # model would write flowing prose covering the same content and never
    # emit the "### " lines at all. article_strategy.py's heading target
    # assumes every assigned subheading renders, so this closes the gap the
    # same way keyword coverage does, right before readability so the
    # simplification pass runs on the final structure, not a stale one.
    if assigned_subheadings:
        text, subheading_report = ensure_subheadings_present(deepseek, text, assigned_subheadings, section)
        report["subheading_coverage"] = subheading_report
        if subheading_report["status"] == "fixed":
            report["after"] = count_words(text)
            report["paragraphs"] = count_paragraphs(text)

    # Readability, measured after length is already settled - a real
    # external check (Hemingway App) against a real generated article
    # measured Grade 12 output against a 6th-grade instruction that nothing
    # was verifying. Runs before the keyword-phrase check below so
    # simplification can't strip that phrase back out after it's inserted.
    text, readability_report = simplify_for_readability(deepseek, text, section, keyword_strings)
    report["readability"] = readability_report
    if readability_report["rounds"] > 0:
        # Simplification can shift word count slightly (shorter sentences
        # aren't always fewer words) - keep the log accurate rather than
        # stale from before this pass ran.
        report["after"] = count_words(text)
        report["paragraphs"] = count_paragraphs(text)

    if is_first:
        # Title heading presence, measured - the prompt says to start with
        # a "# " heading using the exact title, but that instruction was
        # measured to fail silently on one run: the model skipped straight
        # to the section's own "## " heading and never wrote the "# " title
        # line at all, leaving the finished article with no H1 in it.
        # Deterministic fix, not an LLM round-trip - unlike a missing
        # subheading (which needs prose restructured around it), a missing
        # title heading has exactly one correct fix: prepend it verbatim.
        before_title_fix = text
        text = _ensure_title_heading(text, title)
        if text != before_title_fix:
            report["title_heading_fix_applied"] = True
            report["after"] = count_words(text)

        # Checked last, after condensing - nothing downstream can remove the
        # phrase again. The prompt instruction alone was measured to fail
        # silently (the model reliably wrote "ultralight backpacking gear
        # list" instead of the required "backpacking gear list ultralight"),
        # with nothing verifying it landed - see ensure_exact_phrase.
        fixed = ensure_exact_phrase(
            deepseek, text, keyword,
            "This is the opening of a long-form article.", max_words=100,
        )
        if fixed != text:
            text = fixed
            report["keyword_phrase_fix_applied"] = True
            report["after"] = count_words(text)
    return text, report


UNANSWERED_QUESTIONS_SYSTEM_PROMPT = """Given a finished article and a list of questions it was \
supposed to answer, identify which questions are NOT explicitly and directly answered anywhere \
in the article - a question that's only implied or partially touched on counts as unanswered.

Before writing a fix for any unanswered question, check whether it is actually relevant to THIS \
keyword's real topic and audience. These questions were pulled from Google's "People Also Ask" \
data, which matches on surface wording - a keyword containing "plus" can surface a PAA question \
about the Professional Liability Underwriting Society or "LGBTQ+" that has nothing to do with \
plus-size clothing. Forcing an answer for a question like that injects genuinely wrong, off-topic \
content into the article - worse than leaving it unanswered. If a question is not genuinely \
relevant, put it in `skipped` with a one-line reason instead of writing a fix for it.

Some of these questions may be near-duplicate phrasings of each other (e.g. "creative ideas for \
X" and "unique ideas for X" asking the same thing two ways) - treat them as ONE question with ONE \
fix, not two separate insertions, even if both appear in the list below. Before writing a fix, \
check whether the article already substantively covers it under different wording - "not \
explicitly answered verbatim" is not the same as "not answered." If two or more questions would \
be satisfied by essentially the same answer, write that answer ONCE, for whichever single question \
best represents the group, and skip the rest as already covered by that fix - two near-identical \
paragraphs restating the same examples with a different opening sentence is a worse outcome than \
leaving a synonymous question technically unfixed.

For every question that IS relevant, distinct, and unanswered, pick the SINGLE existing section \
(by its exact heading, from the list given) where the answer fits best, and write a direct, 2-4 \
sentence answer to insert - grounded in facts already established in the article where possible, \
not invented new claims.

If every question is already answered or none are relevant, return empty lists for both.

Return JSON matching the required schema only."""


def _fix_unanswered_questions(
    deepseek: DeepSeekClient, sections: list, texts: list, real_questions: list, keyword: str,
) -> tuple:
    """Runs once, after every section is written - a question can be
    answered by any section, not a fixed one, so this can't run per-section
    the way keyword coverage does. The SEO QA step (13) repeatedly found
    5-6 required questions per article going completely unanswered despite
    every section having the full list available; this closes that gap the
    same way the others were closed - measure, then inject a targeted fix.

    Returns (texts, report). texts is the same list, mutated in place where
    a fix landed."""
    if not real_questions:
        return texts, {"checked": 0, "fixed": 0, "unfixable": 0, "skipped": 0, "skipped_questions": []}

    from models import UnansweredQuestionsReport  # local import - avoid a cycle at module load

    heading_list = "\n".join(f"- {s['heading']}" for s in sections)
    questions_block = "\n".join(f"- {q}" for q in real_questions)
    full_article = "\n\n".join(texts)

    user_prompt = (
        f"Keyword: {keyword}\n\n"
        f"Available section headings (pick from these exactly):\n{heading_list}\n\n"
        f"Required questions:\n{questions_block}\n\n"
        f"--- ARTICLE ---\n{full_article}\n--- END ---"
    )
    result = deepseek.structured_call(
        UNANSWERED_QUESTIONS_SYSTEM_PROMPT, user_prompt, UnansweredQuestionsReport,
        label="fix_unanswered_questions",
    )
    parsed = result.model_dump()
    fixes = parsed["fixes"]
    skipped = parsed.get("skipped") or []

    heading_to_index = {s["heading"]: i for i, s in enumerate(sections)}
    fixed_count, unfixable = 0, 0
    for fix in fixes:
        idx = heading_to_index.get(fix["target_section_heading"])
        if idx is None:
            unfixable += 1
            continue
        texts[idx] = f"{texts[idx]}\n\n{fix['answer']}"
        fixed_count += 1

    return texts, {
        "checked": len(real_questions),
        "fixed": fixed_count,
        "unfixable": unfixable,
        "skipped": len(skipped),
        "skipped_questions": skipped,
    }


def write_article(
    deepseek: DeepSeekClient,
    keyword: str,
    outline: dict,
    content_gaps: dict,
    community_research: dict,
    vocabulary: dict,
    search_intent: dict,
    section_keyword_map: dict,
    log_step: callable,
) -> tuple:
    """Returns (article, length_report, questions_report). log_step(name,
    output) is called for both the pre-enforcement draft and the final
    text of every section, so a condense pass is a readable diff on disk,
    not a black box."""
    keywords_by_section = {
        a["section_heading"]: a["keywords"] for a in section_keyword_map.get("assignments") or []
    }
    real_questions = community_research.get("real_questions_to_answer") or []
    differentiation_angle = content_gaps.get("differentiation_angle", "")
    vocabulary_terms = [t["term"] for t in vocabulary.get("terms") or []]
    bluf_guidance = search_intent.get("bluf_guidance") or "Lead with the direct answer, then explain."

    article = ""
    sections_done, texts_done = [], []
    covered_terms = set()
    length_report = []

    for i, section in enumerate(outline["sections"]):
        digest = _build_digest(outline, sections_done, texts_done)
        keywords = keywords_by_section.get(section["heading"], [])

        text, report = write_section(
            deepseek, keyword, section, keywords, real_questions, vocabulary_terms, bluf_guidance,
            digest, covered_terms,
            is_first=(i == 0), title=outline["title"], differentiation_angle=differentiation_angle,
        )
        log_step(f"write_section__{section['heading'][:40]}", text)
        length_report.append(report)

        article = f"{article}\n\n{text}" if article else text
        sections_done.append(section)
        texts_done.append(text)
        covered_terms |= salient_terms(text)

    texts_done, questions_report = _fix_unanswered_questions(
        deepseek, sections_done, texts_done, real_questions, keyword,
    )
    article = "\n\n".join(texts_done)

    return article, length_report, questions_report
