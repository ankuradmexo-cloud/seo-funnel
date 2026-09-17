"""Step 6 - common language & vocabulary.

Extracts the topic's vocabulary: frequent meaningful phrases, entities,
recurring concepts, and topic-specific terms shared across competitors.
Extraction is CODE, not an LLM call - a document-frequency count across N
competitors is a precise signal an LLM would only eyeball, and code doesn't
normalize brand spellings or miss instances. The LLM is used once, only to
FILTER the code's candidate list down to genuinely meaningful terms and
categorize them - it never has to find the terms itself.

This is what pipeline/article_strategy.py and pipeline/article_writer.py
draw on to ground subheadings and body text in real competitor vocabulary
instead of an LLM's own guess at relevant terms.
"""

import re
from collections import Counter

from clients.deepseek_client import DeepSeekClient
from models import TopicVocabulary

# Common English function words - n-grams starting or ending on one of these
# are rejected outright ("the best way", "way to the") since a real term or
# entity essentially never starts or ends mid-function-word.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "at",
    "for", "with", "by", "from", "as", "is", "are", "was", "were", "be",
    "been", "being", "this", "that", "these", "those", "it", "its", "you",
    "your", "we", "our", "they", "their", "he", "she", "his", "her", "i",
    "my", "not", "no", "so", "than", "then", "too", "very", "can", "will",
    "just", "up", "out", "about", "into", "over", "after", "before", "how",
    "what", "when", "where", "why", "which", "who", "all", "some", "any",
    "one", "two", "s", "t", "re", "ve", "ll", "d", "m",
}

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'-]*")

MIN_TERM_DOC_FREQ = 2   # must appear in at least this many competitors...
MIN_SINGLE_DOC_FREQ = 4  # ...unless it appears at least this many times in just one
CANDIDATE_LIMIT = 80     # how many candidates go to the LLM filter pass


def _body_text(scraped_competitor: dict) -> str:
    """Text with heading lines excluded - a term that only ever appears in
    headings would otherwise dominate the count without reflecting how often
    it's actually discussed in prose."""
    text = scraped_competitor.get("text") or ""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _ngrams(tokens: list, n: int) -> list:
    return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def _extract_candidates(scraped_competitors: list) -> list:
    """Returns [(phrase, document_frequency, total_frequency), ...], ranked
    with multi-word phrases first, capped at CANDIDATE_LIMIT.

    Multi-word phrases are ranked ahead of single words on purpose: a common
    single word ("make", "want", "here") is frequent because it's common
    English, regardless of topic - it tells you nothing. An exact 2-3 word
    phrase repeating across independently-written competitor pages is very
    unlikely by chance, so it's a much stronger topical signal. A single
    word already covered by a kept multi-word phrase (e.g. "pad" once
    "sleeping pad" is kept) is dropped as redundant."""
    per_doc_counts = []  # one Counter per competitor
    for c in scraped_competitors:
        if not c.get("scraped"):
            continue
        tokens = [t.lower() for t in _WORD_RE.findall(_body_text(c))]
        counts = Counter()
        for n in (1, 2, 3):
            for gram in _ngrams(tokens, n):
                if gram[0] in _STOPWORDS or gram[-1] in _STOPWORDS:
                    continue
                if n == 1 and len(gram[0]) < 4:  # single short words are almost always noise
                    continue
                counts[" ".join(gram)] += 1
        per_doc_counts.append(counts)

    doc_freq = Counter()
    total_freq = Counter()
    for counts in per_doc_counts:
        total_freq.update(counts)
        doc_freq.update(counts.keys())

    qualifying = [
        phrase for phrase in doc_freq
        if doc_freq[phrase] >= MIN_TERM_DOC_FREQ or total_freq[phrase] >= MIN_SINGLE_DOC_FREQ
    ]
    ranked = sorted(
        qualifying,
        key=lambda p: (-len(p.split()), -doc_freq[p], -total_freq[p]),
    )

    kept, covered_words = [], set()
    for phrase in ranked:
        words = phrase.split()
        if len(words) == 1 and phrase in covered_words:
            continue
        kept.append(phrase)
        if len(words) > 1:
            covered_words.update(words)
        if len(kept) >= CANDIDATE_LIMIT:
            break

    return [(p, doc_freq[p], total_freq[p]) for p in kept]


SYSTEM_PROMPT = """These phrases were extracted by counting document frequency (how many \
competitor pages mention each one) across pages ranking for a keyword. The extraction is \
mechanical - it does NOT know which candidates are meaningful topic vocabulary versus noise \
(navigation text, generic filler, accidental word combinations). Your job is only to filter and \
categorize, not to find new terms.

Keep only candidates that are genuinely meaningful for this topic: product/brand names, \
technical terms, specific concepts, or phrases that satisfy what someone searching this keyword \
wants to know. Drop anything that is boilerplate, a generic phrase with no topical content, or \
an accidental fragment. Categorize each kept term as entity (a named product/brand/place), \
concept (an idea or technique), term (a technical/domain word), or phrase (a multi-word unit that \
doesn't fit the other three). Keep the given document_frequency number unchanged for each term \
you keep.

Return JSON matching the required schema only."""


def extract_vocabulary(deepseek: DeepSeekClient, keyword: str, scraped_competitors: list) -> dict:
    candidates = _extract_candidates(scraped_competitors)
    if not candidates:
        return {"terms": []}

    listing = "\n".join(f"- {phrase} (document_frequency={df}, total_frequency={tf})" for phrase, df, tf in candidates)
    user_prompt = f"Keyword: {keyword}\n\nCandidates:\n{listing}"
    result = deepseek.structured_call(SYSTEM_PROMPT, user_prompt, TopicVocabulary, label="vocabulary_extraction")
    return result.model_dump()
