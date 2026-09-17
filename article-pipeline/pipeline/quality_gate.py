"""Automated pass/fail gate for a finished run - the piece that lets this
pipeline run unattended.

SE Ranking's Content Editor has no API (confirmed directly - checked their
docs) - "paste it in and read the score" can never be part of an automated
pipeline, it's a human-only step. So "good enough to publish" has to be
judged from signals this pipeline already computes for itself, right after
generation, not from a number only available by hand.

This is deliberately NOT a score predictor - this week's runs showed our
own structural signals (reliable target, subheadings rendered, keywords
covered) don't reliably predict SE Ranking's actual number (construction
software hit every one of these clean and still only scored 57-61, because
the word-count overshoot problem lives outside what these checks measure).
What this DOES catch reliably is the difference between "generation
completed cleanly" and "something is known to be broken" - every case this
week where a run had one of the FAIL conditions below traces back to an
actual defect we found and fixed (missing subheadings, an unreliable soft
target, a rejected correction pass). A PASS is not a promise of a good
score; a FAIL is a promise something is actually wrong.
"""

from typing import Any

# Below this reading grade target + this much slack, readability
# enforcement is considered to have failed outright rather than just
# landed a bit high - simplify_for_readability targets 6th grade;
# anything past 9 despite that pass running means the enforcement loop
# gave up rather than converged.
MAX_READING_GRADE = 9.0

# A reliable target needs at least this many relevant competitors even
# though MIN_RELEVANT_COMPETITORS (3) already gates "reliable" - a 3-4
# sample size passed that floor in several runs this week and still
# produced a noisy, unstable range (wild casino). Below this, flag for
# review rather than trust the target blindly.
MIN_TRUSTWORTHY_SAMPLE_SIZE = 5

# qa_issue_count varied 5-28 across runs this week with no clean
# correlation to score on its own - used here only as a coarse "did the QA
# pass find an unusual pile of problems" flag, not a precise threshold.
MAX_QA_ISSUES_WARN = 15

# A stray missing keyword or two is normal, not a defect - measured
# directly: the single best-scoring run this week (67, well above the
# competitor average) had exactly 1 missing and it clearly wasn't fatal.
# ensure_keywords_present already tries every assigned keyword and only
# leaves one out when it genuinely can't fit without stuffing; a handful
# missing across a whole article's worth of assignments is expected
# behavior, not a broken run. Above this count, treat it as a real gap.
MAX_KEYWORDS_MISSING_WARN = 2

# Same lesson, caught a run late: a re-run of that exact 67-scoring
# keyword hit 1 missing subheading and 1 entity-rejected section out of a
# 20-section article and still scored 67 - identical to the run that
# didn't have either issue. Treating any nonzero count as automatic FAIL
# was over-calibrated from a small sample size, not from evidence that
# one miss actually hurts the score. One or two is noise across a
# full-length article; only a real pile of them (several sections, not
# one) is worth blocking on.
MAX_SUBHEADINGS_MISSING_WARN = 2
MAX_ENTITY_REJECTED_WARN = 2


def evaluate_run(run_summary: dict[str, Any]) -> dict:
    """Returns {"verdict": "pass" | "warn" | "fail", "reasons": [...]}.
    reasons is always populated for warn/fail, explaining exactly which
    check(s) fired - empty on a clean pass. Called on a completed run's
    summary dict (the same one written to _run_summary.json); a rejected
    keyword (run_summary["rejected"] True) is a fail before this even
    needs to run - callers should check that separately."""
    fail_reasons: list[str] = []
    warn_reasons: list[str] = []

    if not run_summary.get("length_target_reliable"):
        fail_reasons.append(
            "length target is soft/LLM-judged, not competitor-derived - "
            f"only {run_summary.get('length_target_sample_size', 0)} relevant competitors found"
        )

    subheadings_missing = run_summary.get("subheadings_missing_after", 0)
    if subheadings_missing > MAX_SUBHEADINGS_MISSING_WARN:
        fail_reasons.append(f"{subheadings_missing} planned subheading(s) never rendered")
    elif subheadings_missing > 0:
        warn_reasons.append(f"{subheadings_missing} planned subheading(s) never rendered")

    keywords_missing = run_summary.get("keywords_missing_after", 0)
    if keywords_missing > MAX_KEYWORDS_MISSING_WARN:
        fail_reasons.append(f"{keywords_missing} assigned keyword(s) missing from the article")
    elif keywords_missing > 0:
        warn_reasons.append(f"{keywords_missing} assigned keyword(s) missing from the article")

    if run_summary.get("questions_unfixable", 0) > 0:
        fail_reasons.append(
            f"{run_summary['questions_unfixable']} required question(s) could not be answered"
        )

    entity_rejected = run_summary.get("sections_entity_rejected", 0)
    if entity_rejected > MAX_ENTITY_REJECTED_WARN:
        fail_reasons.append(
            f"{entity_rejected} section(s) had a correction pass rejected for losing too much real content"
        )
    elif entity_rejected > 0:
        warn_reasons.append(
            f"{entity_rejected} section(s) had a correction pass rejected for losing too much real content"
        )

    grade = run_summary.get("article_reading_grade")
    if grade is not None and grade > MAX_READING_GRADE:
        fail_reasons.append(f"reading grade {grade} is well above the 6th-grade target")

    sample_size = run_summary.get("length_target_sample_size", 0)
    if run_summary.get("length_target_reliable") and sample_size < MIN_TRUSTWORTHY_SAMPLE_SIZE:
        warn_reasons.append(
            f"length target is technically reliable but from only {sample_size} competitors - "
            "thin sample, treat the range with caution"
        )

    if run_summary.get("sections_stalled", 0) > 0:
        warn_reasons.append(
            f"{run_summary['sections_stalled']} section(s) stalled or exhausted their length-fix rounds"
        )

    qa_issues = run_summary.get("qa_issue_count", 0)
    if qa_issues > MAX_QA_ISSUES_WARN:
        warn_reasons.append(f"QA pass flagged {qa_issues} issues - unusually high")

    if fail_reasons:
        return {"verdict": "fail", "reasons": fail_reasons + warn_reasons}
    if warn_reasons:
        return {"verdict": "warn", "reasons": warn_reasons}
    return {"verdict": "pass", "reasons": []}
