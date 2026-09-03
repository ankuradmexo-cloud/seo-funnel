import re


def normalize_keyword(keyword: str) -> str:
    k = keyword.lower().strip()
    k = re.sub(r"[^\w\s]", "", k)
    k = re.sub(r"\s+", " ", k)
    return k


def exact_dedup(candidates: list[str], existing_normalized: set[str]) -> list[str]:
    """Drops candidates whose normalized form is already in the corpus/published set."""
    survivors = []
    seen = set()
    for kw in candidates:
        norm = normalize_keyword(kw)
        if norm in existing_normalized or norm in seen:
            continue
        seen.add(norm)
        survivors.append(kw)
    return survivors
