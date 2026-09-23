import re

# Small and deliberately conservative - only words that never carry the
# keyword's actual search intent, so stripping them can't collapse two
# genuinely different keywords together.
_STOPWORDS = {
    "a", "an", "the", "with", "for", "to", "in", "of", "on", "and", "or",
    "is", "are", "at", "by", "from",
}


def normalize_keyword(keyword: str) -> str:
    k = keyword.lower().strip()
    k = re.sub(r"[^\w\s]", "", k)
    k = re.sub(r"\s+", " ", k)
    return k


def dedup_key(keyword: str) -> str:
    """Word-order/stopword-insensitive canonical form - catches
    near-duplicates like "group ordering restaurants" vs "restaurants with
    group ordering" (same words, same search intent, different order) that
    normalize_keyword's literal string match misses. Measured directly: both
    of those got shortlisted as separate keywords and each separately spent
    SE Ranking demand-validation + SERP-check credits on what is, for
    ranking purposes, one keyword.

    This is deliberately NOT the LLM semantic dedup already tried and cut
    elsewhere in this pipeline (see README's "What was tried and dropped") -
    sorting a small stopword-stripped word set is free, deterministic, and
    can't hallucinate a false match the way a fuzzy semantic comparison
    could."""
    words = [w for w in normalize_keyword(keyword).split() if w not in _STOPWORDS]
    return " ".join(sorted(words))


def exact_dedup(candidates: list[str], existing_normalized: set[str]) -> list[str]:
    """Drops candidates whose dedup key - word-order/stopword-insensitive -
    is already in the corpus/published set."""
    survivors = []
    seen = set()
    for kw in candidates:
        key = dedup_key(kw)
        if key in existing_normalized or key in seen:
            continue
        seen.add(key)
        survivors.append(kw)
    return survivors
