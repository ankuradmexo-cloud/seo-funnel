"""Deterministic length enforcement for generated sections.

Code can count words exactly; the model cannot. Two prompt-only attempts at
hitting a word target failed (+91%, then +73%), and a hard max_tokens cap
truncated mid-sentence. So the target is enforced here instead: measure the
draft, hand the model its own text plus an exact arithmetic delta ("this is
948 words, cut it to 521"), and repeat until it lands in band.

Three things make this safe rather than destructive:

  Staged descent. A 948 -> 322 ask is a 66% cut, which is where the model
  stops editing and regenerates from memory - and regenerating is what
  destroys the product vocabulary the external scorer grades on. Each round
  asks for at most MAX_SINGLE_CUT, so the model always faces an edit.

  Delete, not rewrite. The overlong sections are not padded prose - they are
  dense with brand names, weights and prices. The prompt is framed as a
  deletion task with an explicit cut order and a never-remove list.

  A retention circuit breaker. Proper nouns and number-with-unit tokens are
  counted before and after. A round that drops below MIN_RETENTION is
  discarded and the previous best kept - wholesale regeneration collapses
  retention to ~0.5, so this catches the catastrophic case deterministically.
"""

import re
from typing import Optional

# The writer is asked for target*ENFORCE_SCALE so the condense loop has
# something to trim toward rather than fighting for the last few words.
ENFORCE_SCALE = 1.15

ACCEPT_HIGH = 1.12      # at or under this x enforce_target -> done
ACCEPT_LOW = 0.85       # under this -> flagged, but never expanded
ASK_FACTOR = 0.95       # ask slightly under target; the model overshoots asks
# A single round asking for ~54% (877->402 words) measured as the tipping
# point where the model stops editing and rewrites from memory - the
# retention breaker caught it and the round was discarded, netting zero
# reduction on that section. 0.30 keeps every round in edit territory;
# MAX_ROUNDS raised to compensate with more, gentler steps.
MAX_SINGLE_CUT = 0.30
MAX_ROUNDS = 5
MIN_PROGRESS = 0.06     # a round that cuts less than this is stalling
MIN_RETENTION = 0.85    # entity retention floor vs the ORIGINAL draft
DEADBAND_WORDS = 20     # smaller than this isn't a real reduction

_FENCE_RE = re.compile(r"```.*?```", re.S)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+", re.M)
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_EMPHASIS_RE = re.compile(r"(\*\*|\*|__|_|`)")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", re.M)

# Two-or-more consecutive capitalized words: "Sawyer Squeeze", "NeoAir XLite NXT".
_PROPER_RUN_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:[ -][A-Z][A-Za-z0-9]*)+\b")
# Prices and measurements: "$30", "5 oz", "2.5 lbs", "550 ml".
_NUM_UNIT_RE = re.compile(
    r"(?:\$\s?\d[\d,]*(?:\.\d+)?)"
    r"|(?:\b\d+(?:\.\d+)?\s?(?:oz|ounces?|lbs?|pounds?|g|kg|grams?|ml|l|liters?|"
    r"in|inch(?:es)?|ft|feet|mi|miles?|d|degrees?|%|F|C)\b)",
    re.I,
)


def count_words(markdown: str) -> int:
    """Word count of the prose a reader (or an external scorer) sees, with
    markdown syntax removed. Raw .split() over markdown overcounts - it
    treats '##', '**', and table pipes as words, which is most of the gap
    between our 7,365 and SE Ranking's 7,147 for the same article."""
    text = _FENCE_RE.sub(" ", markdown)
    text = _TABLE_SEP_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _HEADING_RE.sub("", text)
    text = _BULLET_RE.sub("", text)
    text = _EMPHASIS_RE.sub("", text)
    text = text.replace("|", " ")
    return len(text.split())


def count_subheads(markdown: str) -> int:
    return len(re.findall(r"^\s{0,3}###\s+", markdown, re.M))


def count_paragraphs(markdown: str) -> int:
    """Non-heading, non-empty blocks separated by a blank line. Measured and
    reported (see the writer's per-section paragraph guidance and
    _run_summary.json), but not actively repaired the way word count is -
    reliably merging or splitting paragraphs without breaking flow is a
    materially different (and harder) problem than deleting words, and
    scoped out of this pass. If the writer's guidance is followed
    reasonably, the count should track close without a dedicated loop; if
    _run_summary.json shows it consistently missing, that loop is the next
    thing to build."""
    blocks = [b.strip() for b in markdown.split("\n\n")]
    return len([b for b in blocks if b and not b.lstrip().startswith("#")])


def _body_lines(text: str) -> str:
    """Headings excluded: a legitimate subheading merge during condensing
    should not register as lost entities."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def salient_terms(text: str) -> set:
    body = _body_lines(text)
    terms = {m.group(0).strip().lower() for m in _PROPER_RUN_RE.finditer(body)}
    terms |= {re.sub(r"\s+", " ", m.group(0)).strip().lower() for m in _NUM_UNIT_RE.finditer(body)}
    return terms


def retention(before: str, after: str) -> float:
    """Fraction of the original's salient terms still present. 1.0 when the
    original had none to lose."""
    original = salient_terms(before)
    if not original:
        return 1.0
    return len(original & salient_terms(after)) / len(original)


def subheading_budget(target_words: int, requested: int = 3) -> int:
    """The cap is however many subheadings the outline actually asked for
    (`requested`), reduced only if the section's own word target genuinely
    can't support that many at a reasonable ~70 words each - not a flat "3
    max" regardless of what's being asked for, and not gated by an
    unrelated word-count floor.

    Found by testing (twice): a flat min(3, target_words // 250) silently
    dropped 7 of 10 outline-planned subheadings in a 1010-word "10 Best X"
    listicle section (one per item, ~100 words each). Fixed by scaling the
    cap to ~70 words/subheading instead - but left behind a stale "sections
    under 300 words get none" floor from the original version, which then
    zeroed out subheadings for every section in the very common 70-300 word
    range (e.g. a 249-word section that could easily fit 3 subheadings at
    70 words each) - measured directly: an entire article's worth of
    planned subheadings (29 of them) silently vanished this way, with nothing
    catching it because ensure_subheadings_present only checks sections it's
    actually given subheadings to check. 100 words is enough room for one
    subheading (70) plus a minimum of real connecting prose (30) - below
    that, forcing even one subheading fragments the section more than it
    helps."""
    if target_words < 100:
        return 0
    max_supportable = target_words // 70
    return max(0, min(requested, max_supportable))


CONDENSE_SYSTEM_PROMPT = """You are DELETING words from one section of an article. This is an \
editing task, not a rewriting task. Work sentence by sentence, top to bottom: for each sentence, \
decide whether it matches one of the cut categories below and either delete it whole or leave it \
completely untouched. Do not paraphrase, compress, or reword any sentence that survives - a \
surviving sentence must appear character-for-character as it did in the original. If you cannot \
hit the target this way, delete more whole sentences; do not switch to summarizing.

CUT, in this order:
1. Opening sentences that restate the heading or announce what the section will do.
2. Framing and transition sentences carrying no fact ("Not all gear deserves equal investment. \
Here's where to open your wallet:").
3. Any closing summary, takeaway, or bottom-line paragraph.
4. Second and third examples of a point the first example already makes.
5. Anything already covered elsewhere in the article (listed below, if any).
6. Adjectives, intensifiers and hedging clauses that add no information.

NEVER REMOVE:
- Product names, brand names, model numbers.
- Any number with a unit - weights, prices, capacities, temperatures, distances.
- The "## " heading or any "### " subheadings.
- The assigned keywords listed below.
- A concrete fact that appears only once in this section.

Do not add anything new. Do not reorder. Return only the edited markdown, nothing else."""


def _condense_once(
    deepseek, text: str, current_words: int, ask_words: int,
    section: dict, keywords: list, covered_terms: Optional[list] = None,
) -> str:
    covered = ", ".join(covered_terms or []) or "(nothing yet)"
    user_prompt = (
        f"Section heading: {section['heading']}\n"
        f"This section must still cover: {section['covers']}\n"
        f"Assigned keywords (must survive): {', '.join(keywords) or '(none)'}\n"
        f"Already covered elsewhere in the article (cut repeats of these): {covered}\n\n"
        f"This section is currently {current_words} words. "
        f"Cut it to about {ask_words} words - remove roughly {current_words - ask_words} words.\n\n"
        f"--- SECTION ---\n{text}\n--- END ---"
    )
    return deepseek.generate(
        CONDENSE_SYSTEM_PROMPT, user_prompt,
        temperature=0.3,
        max_tokens=max(600, int(ask_words * 2.2)),
        label="condense_section",
    )


def enforce_section_length(
    deepseek, draft: str, section: dict, enforce_target: int,
    keywords: list, covered_terms: Optional[list] = None,
) -> tuple:
    """Returns (text, report). Only ever shortens - there is no expand branch,
    because undershoot has never occurred in measured runs and an expander
    can oscillate against this loop."""
    before_words = count_words(draft)
    report = {
        "heading": section["heading"],
        "target": section["target_word_count"],
        "enforce_target": enforce_target,
        "before": before_words,
        "after": before_words,
        "rounds": 0,
        "retention": 1.0,
        "subheads": count_subheads(draft),
        # Measured, not enforced - see count_paragraphs' docstring.
        "target_paragraphs": section.get("target_paragraph_count"),
        "paragraphs": count_paragraphs(draft),
        "status": "in_band",
    }

    if before_words <= enforce_target * ACCEPT_HIGH:
        if before_words < enforce_target * ACCEPT_LOW:
            report["status"] = "under_target"
        return draft, report

    best, best_words = draft, before_words
    status = "rounds_exhausted"

    for rnd in range(1, MAX_ROUNDS + 1):
        ask = max(
            int(enforce_target * ASK_FACTOR),
            int(best_words * (1 - MAX_SINGLE_CUT)),
        )
        candidate = _condense_once(
            deepseek, best, best_words, ask, section, keywords, covered_terms
        )
        cand_words = count_words(candidate)
        report["rounds"] = rnd

        ret = retention(draft, candidate)
        if ret < MIN_RETENTION:
            # Discard this round entirely and keep the previous best - the
            # model regenerated instead of editing.
            report["retention"] = ret
            status = "entity_rejected"
            break

        if cand_words >= best_words - DEADBAND_WORDS:
            status = "no_reduction"
            break

        progress = (best_words - cand_words) / best_words
        best, best_words = candidate, cand_words
        report["retention"] = ret

        if best_words <= enforce_target * ACCEPT_HIGH:
            status = "in_band"
            break
        if progress < MIN_PROGRESS:
            status = "stalled"
            break

    report["after"] = best_words
    report["subheads"] = count_subheads(best)
    report["paragraphs"] = count_paragraphs(best)
    report["status"] = status
    return best, report


def ensure_exact_phrase(deepseek, text: str, phrase: str, context_note: str, max_words: Optional[int] = None) -> str:
    """Verifies `phrase` appears verbatim (case-insensitive) in `text` - the
    whole thing, or just the first `max_words` words. If missing, asks for
    the smallest possible edit that inserts it, and returns that.

    This exists because "the exact phrase MUST appear" as a plain prompt
    instruction was measured to fail silently: the model consistently wrote
    a natural reordering ("ultralight backpacking gear list" instead of the
    required "backpacking gear list ultralight") without the literal phrase
    ever landing, in both the title and the article's opening, across
    multiple real runs - and nothing was checking for it. Same lesson as
    the rest of this module: an instruction is a request, not a guarantee;
    only measuring and correcting after the fact closes the gap.

    Single-shot, not re-verified after the edit - if the model doesn't
    manage it on the corrective pass either, that's worth knowing (log it),
    not worth an unbounded retry loop for a two-word phrase."""
    window = " ".join(text.split()[:max_words]) if max_words else text
    if phrase.lower() in window.lower():
        return text

    scope = f"the first {max_words} words of " if max_words else ""
    is_title = max_words is None

    if is_title:
        pattern_guidance = (
            f'Lead with it as a noun phrase, then a colon, then the existing hook: '
            f'"{phrase.capitalize()}: <rest of the existing text, adapted>". This works even '
            f"when the phrase alone isn't a clean sentence, because a title can be a label, not "
            f"a sentence. Drop redundant leading words from what follows so it doesn't repeat "
            f"itself. Do not simply append the phrase as a trailing fragment, and do not grow "
            f"the title substantially."
        )
    else:
        pattern_guidance = (
            f'This is flowing prose, not a title - a colon-led label like "{phrase.capitalize()}: '
            f'does X" is WRONG here, it reads as a broken sentence fragment with no subject. '
            f"Instead, rewrite ONE existing sentence so the phrase becomes its actual grammatical "
            f'subject or object, e.g. restructure "The goal is to minimize weight" into '
            f'"The goal of any {phrase} is to minimize weight" - a complete, normal sentence. '
            f"Do not simply append it after an existing sentence, and do not add a whole new "
            f"sentence just to hold it - the result must stay close to the original length."
        )

    fix_prompt = (
        f"{context_note}\n\n"
        f'The exact phrase "{phrase}" does not appear anywhere in {scope}this text, but it must. '
        f"A keyword phrase like this often has unnatural word order for a sentence (adjective "
        f"after the noun, etc.) - do not force it into a clause where it reads awkwardly.\n\n"
        f"{pattern_guidance}\n\n"
        f"--- TEXT ---\n{text}\n--- END ---"
    )
    return deepseek.generate(
        "You edit a text to insert a required exact phrase without growing the text or reading "
        "as keyword-stuffed. Return only the edited text, nothing else - no commentary, no "
        "added quotation marks.",
        fix_prompt, temperature=0.4, label="ensure_exact_phrase",
    ).strip()


# --- readability: measured, not just instructed ---------------------------
#
# "Write for a 6th-grade reader" was a prompt instruction with nothing
# checking whether it happened - same failure shape as word count and the
# exact-keyword phrase before it. Confirmed with a real external tool
# (Hemingway App) against a real generated article: Grade 12, not the
# instructed 6th grade, with 63% of sentences flagged hard or very hard to
# read. This section closes that gap the same way the other two were
# closed: measure the actual grade level, and if it's too high, run a
# bounded simplification loop rather than trust the instruction alone.

_TOKEN_RE = re.compile(r"[A-Za-z]+")
# Split on sentence-ending punctuation, but not between digits ("2.5 oz"
# is not two sentences) and not inside emphasis/link markup.
_SENTENCE_SPLIT_RE = re.compile(r"(?<!\d)[.!?]+(?!\d)(?:\s+|$)")

READABILITY_TARGET_GRADE = 6
# Some slack above the literal target - forcing exactly grade 6.0 on
# content that necessarily includes specs, weights and prices is not
# realistic, and chasing the last half-grade isn't worth another round.
READABILITY_ACCEPT_GRADE = 8
READABILITY_MAX_ROUNDS = 2
READABILITY_MIN_RETENTION = 0.85

SIMPLIFY_SYSTEM_PROMPT = """You are simplifying one section of an article to a lower reading \
grade level. Keep every fact, number, product name, and assigned keyword exactly as they are - \
simplify HOW it's said, not WHAT is said.

- Break long, multi-clause sentences into two or more short ones.
- Replace complex or rare words with common, everyday alternatives wherever a simpler word means \
the same thing.
- Cut hedging and filler words that add no information ("quite", "rather", "essentially", "in \
order to" becomes "to").
- Do not remove any concrete fact, number, or named product - only the way each sentence is \
phrased.

Return only the simplified markdown, nothing else."""


def _count_syllables(word: str) -> int:
    """Standard vowel-group heuristic - not linguistically exact, but
    accurate enough to separate "Grade 6" from "Grade 12" reliably, which
    is the actual bar here."""
    word = re.sub(r"[^a-z]", "", word.lower())
    if not word:
        return 0
    if len(word) <= 3:
        return 1
    word = re.sub(r"e$", "", word)  # silent e
    groups = re.findall(r"[aeiouy]+", word)
    return max(1, len(groups))


def count_sentences(text: str) -> int:
    body = _LINK_RE.sub(r"\1", _body_lines(text))
    body = _EMPHASIS_RE.sub("", body)
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
    return max(1, len(sentences))


def flesch_kincaid_grade(text: str) -> float:
    body = _LINK_RE.sub(r"\1", _body_lines(text))
    tokens = _TOKEN_RE.findall(body)
    n_words = len(tokens)
    n_sentences = count_sentences(text)
    if n_words == 0:
        return 0.0
    n_syllables = sum(_count_syllables(w) for w in tokens)
    grade = 0.39 * (n_words / n_sentences) + 11.8 * (n_syllables / n_words) - 15.59
    return round(max(0.0, grade), 1)


def _simplify_once(deepseek, text: str, section: dict, keywords: list) -> str:
    keyword_block = ", ".join(keywords) or "(none)"
    user_prompt = (
        f"Section heading: {section['heading']}\n"
        f"Assigned keywords (must survive, verbatim): {keyword_block}\n\n"
        f"--- SECTION ---\n{text}\n--- END ---"
    )
    return deepseek.generate(
        SIMPLIFY_SYSTEM_PROMPT, user_prompt, temperature=0.3,
        max_tokens=max(600, int(len(text.split()) * 2.2)),
        label="simplify_readability",
    )


def simplify_for_readability(deepseek, text: str, section: dict, keywords: list) -> tuple:
    """Returns (text, report). Mirrors enforce_section_length's shape:
    measure, correct, verify entities survive each round, bounded rounds,
    never trust the instruction alone."""
    before_grade = flesch_kincaid_grade(text)
    report = {
        "grade_before": before_grade,
        "grade_after": before_grade,
        "rounds": 0,
        "retention": 1.0,
        "status": "in_band",
    }
    if before_grade <= READABILITY_ACCEPT_GRADE:
        return text, report

    best, best_grade = text, before_grade
    status = "rounds_exhausted"

    for rnd in range(1, READABILITY_MAX_ROUNDS + 1):
        candidate = _simplify_once(deepseek, best, section, keywords)
        ret = retention(text, candidate)
        report["rounds"] = rnd

        if ret < READABILITY_MIN_RETENTION:
            report["retention"] = ret
            status = "entity_rejected"
            break

        cand_grade = flesch_kincaid_grade(candidate)
        best, best_grade = candidate, cand_grade
        report["retention"] = ret

        if best_grade <= READABILITY_ACCEPT_GRADE:
            status = "in_band"
            break

    report["grade_after"] = best_grade
    report["status"] = status
    return best, report


# --- assigned-keyword coverage: measured, not just instructed ---------------
#
# The writer prompt says "use the assigned keywords where they fit
# naturally" - which the SEO QA step repeatedly found didn't happen: a
# keyword listed as assigned to a section would sometimes never appear in
# that section's text at all. Same gap as the other three; closed the same
# way.

KEYWORD_COVERAGE_MIN_RETENTION = 0.85


def ensure_keywords_present(deepseek, text: str, keywords: list, section: dict) -> tuple:
    """keywords is a list of assigned-keyword dicts (with 'keyword', plus
    volume/difficulty for context - only the 'keyword' string is checked).
    Single-shot, not multi-round: if a keyword still doesn't fit on the
    corrective pass, that's worth knowing (report it), not worth forcing."""
    kw_strings = [k["keyword"] for k in keywords] if keywords else []
    missing_before = [k for k in kw_strings if k.lower() not in text.lower()]

    report = {
        "assigned": len(kw_strings),
        "missing_before": len(missing_before),
        "missing_after": len(missing_before),
        "retention": 1.0,
        "status": "in_band",
    }
    if not missing_before:
        return text, report

    missing_block = ", ".join(missing_before)
    fix_prompt = (
        f"Section heading: {section['heading']}\n\n"
        f"These assigned keywords do not appear anywhere in this section, but they should: "
        f"{missing_block}\n\n"
        f"Weave each one in naturally, verbatim, wherever it genuinely fits the existing "
        f"content - try each one seriously, but don't force one into a sentence where it "
        f"reads as stuffed. Do not remove or change any existing fact, number, or product "
        f"name, and do not rewrite the whole section - make the smallest edits needed.\n\n"
        f"--- SECTION ---\n{text}\n--- END ---"
    )
    candidate = deepseek.generate(
        "You edit a text to naturally include specific required keywords that are currently "
        "missing, without removing or rewriting unrelated content. Return only the edited "
        "text, nothing else.",
        fix_prompt, temperature=0.4,
        max_tokens=max(600, int(len(text.split()) * 2.2)),
        label="ensure_keywords_present",
    ).strip()

    ret = retention(text, candidate)
    report["retention"] = ret
    if ret < KEYWORD_COVERAGE_MIN_RETENTION:
        report["status"] = "entity_rejected"
        return text, report

    missing_after = [k for k in missing_before if k.lower() not in candidate.lower()]
    report["missing_after"] = len(missing_after)
    report["status"] = "fixed" if len(missing_after) < len(missing_before) else "unchanged"
    return candidate, report


# --- subheading presence: measured, not just instructed ---------------------
#
# The writer prompt says "use exactly these subheadings" - measured to fail
# silently on short sections (150-250 words): the model would write normal
# flowing prose covering the same content and never emit the "### " lines at
# all, especially when asked for 2+ subheadings in a tight word budget. This
# mattered because article_strategy.py's heading target is computed assuming
# every planned subheading actually renders - a real run planned 31 total
# headings and only ~17 survived into the article, sections short. Same gap
# as keyword coverage and readability; closed the same way: check what
# actually rendered, and if short, ask for a restructuring pass rather than
# trust the instruction alone.

SUBHEADING_MIN_RETENTION = 0.85


def _has_subheading(text: str, subheading: str) -> bool:
    needle = f"### {subheading}".strip().lower()
    return needle in text.lower()


def ensure_subheadings_present(deepseek, text: str, subheadings: list, section: dict) -> tuple:
    """subheadings is the exact list assigned to this section in the
    outline. Single-shot, not multi-round - if the restructure still misses
    one, that's worth reporting, not worth forcing further."""
    missing_before = [sh for sh in subheadings if not _has_subheading(text, sh)]

    report = {
        "assigned": len(subheadings),
        "missing_before": len(missing_before),
        "missing_after": len(missing_before),
        "retention": 1.0,
        "status": "in_band",
    }
    if not missing_before:
        return text, report

    missing_block = "\n".join(f'- "{sh}"' for sh in missing_before)
    fix_prompt = (
        f"Section heading: {section['heading']}\n\n"
        f"This section is missing these required subheadings as literal \"### \" lines: "
        f"{missing_block}\n\n"
        f"Restructure the section so each missing subheading appears as its own \"### \" line, "
        f"with the existing content split under the most appropriate one. This is a "
        f"restructuring task, not a rewriting task - every fact, number, and sentence already "
        f"in the section must still appear somewhere afterward; do not delete, invent, or "
        f"summarize away content to make it fit under the new headers.\n\n"
        f"--- SECTION ---\n{text}\n--- END ---"
    )
    candidate = deepseek.generate(
        "You restructure a text to insert specific required \"### \" subheadings, splitting "
        "existing content under them, without deleting or inventing content. Return only the "
        "restructured text, nothing else.",
        fix_prompt, temperature=0.3,
        max_tokens=max(600, int(len(text.split()) * 2.2)),
        label="ensure_subheadings_present",
    ).strip()

    ret = retention(text, candidate)
    report["retention"] = ret
    if ret < SUBHEADING_MIN_RETENTION:
        report["status"] = "entity_rejected"
        return text, report

    missing_after = [sh for sh in missing_before if not _has_subheading(candidate, sh)]
    report["missing_after"] = len(missing_after)
    report["status"] = "fixed" if len(missing_after) < len(missing_before) else "unchanged"
    return candidate, report
