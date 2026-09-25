import re

# Small and deliberately conservative - only words that never carry the
# keyword's actual search intent, so stripping them can't collapse two
# genuinely different keywords together.
_STOPWORDS = {
    "a", "an", "the", "with", "for", "to", "in", "of", "on", "and", "or",
    "is", "are", "at", "by", "from",
}

# A handful of extremely common gender-term variants that show up
# interchangeably in ecommerce/fashion keyword sets ("mens" vs "male").
# Deliberately tiny and hand-picked, not a general synonym dictionary - the
# risk of an LLM/fuzzy-semantic dedup collapsing two genuinely different
# keywords is exactly what this repo's README documents avoiding.
_WORD_SYNONYMS = {
    "men": "male", "mens": "male", "man": "male",
    "women": "female", "womens": "female", "woman": "female",
}


def normalize_keyword(keyword: str) -> str:
    k = keyword.lower().strip()
    k = re.sub(r"[^\w\s]", "", k)
    k = re.sub(r"\s+", " ", k)
    return k


def _stem(word: str) -> str:
    """Deterministic, conservative singular/gerund folding - catches
    "apps"/"app", "casinos"/"casino", "traveling"/"travel" without any
    dictionary lookup or LLM call. Only ever strips a suffix outright, so it
    can under-merge (miss a real duplicate) but can't fabricate a match
    between two words that don't already share a long common prefix."""
    word = _WORD_SYNONYMS.get(word, word)
    if len(word) > 5 and word.endswith("ing"):
        word = word[:-3]
    if len(word) > 3 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 3 and word.endswith(("ses", "xes", "ches", "shes")):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return word


def dedup_key(keyword: str) -> str:
    """Word-order/stopword/plural/gerund-insensitive canonical form - catches
    near-duplicates like "group ordering restaurants" vs "restaurants with
    group ordering" (word order) and "traveling map apps" vs "map travel app"
    (plural + gerund forms) that a literal string match misses. Measured
    directly: keyword pairs shaped exactly like this got shortlisted as
    separate keywords and each separately spent SE Ranking demand-validation
    + Scrappa SERP-check credits on what is, for ranking purposes, one
    keyword.

    This is deliberately NOT the LLM semantic dedup already tried and cut
    elsewhere in this pipeline (see README's "What was tried and dropped") -
    sorting a small stopword-stripped, stemmed word set is free,
    deterministic, and can't hallucinate a false match the way a fuzzy
    semantic comparison could."""
    words = [
        _stem(w) for w in normalize_keyword(keyword).split() if w not in _STOPWORDS
    ]
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
