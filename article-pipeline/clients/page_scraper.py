"""Fetches and extracts the main readable content, structure, and metadata
from a competitor URL. The SERP API returns titles and snippets only - it
has no full-page-content endpoint - so this does a direct HTTP fetch plus
extraction.

Best-effort by design: many pages block non-browser requests, render their
content via JS, or sit behind a paywall. A failed or too-thin scrape returns
None, and the caller falls back to the SERP snippet rather than failing the
whole competitor-research step - one uncooperative competitor shouldn't cost
the analysis of the other four.
"""

import re
from typing import Optional
import httpx
import trafilatura
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Below this many extracted characters, treat the page as unusable - almost
# always means JS-rendered content, a paywall, or a bot block, not a
# genuinely short article.
MIN_USABLE_CHARS = 200

# A safety valve, not a content limit - full pages measured up to ~35,000
# chars and every downstream step is meant to see the full text. This just
# stops one pathological page (an infinite-scroll feed, a broken extraction)
# from blowing up prompt size.
MAX_CONTENT_CHARS = 60_000


def _heading_tree(flat_headings: list[dict]) -> list[dict]:
    """Nests a flat, document-order h1/h2/h3 sequence into a real tree, so
    "how does this competitor organize its sections" is an inspectable
    structure, not something inferred from a flat list. Standard
    flat-to-tree-by-level algorithm: each heading attaches under the most
    recent heading of a shallower level."""
    root: list[dict] = []
    stack: list[tuple] = []  # (level_int, node_children_list)

    for h in flat_headings:
        level = int(h["level"][1])  # "h1" -> 1, "h2" -> 2, "h3" -> 3
        node = {"level": h["level"], "text": h["text"], "children": []}
        while stack and stack[-1][0] >= level:
            stack.pop()
        (stack[-1][1] if stack else root).append(node)
        stack.append((level, node["children"]))

    return root


_HEADING_LINE_RE = re.compile(r"^#{1,6}\s*")


def _count_paragraphs(extracted_text: str) -> int:
    """Paragraph count from the CLEANED text (post-trafilatura), not raw
    HTML <p> tags - raw HTML counts nav/boilerplate paragraphs trafilatura
    already stripped out. Requires output_format='markdown' (see
    scrape_page) - the default 'txt' format separates blocks with a single
    newline, indistinguishable from a mid-paragraph line wrap, which
    silently collapsed every page to "1 paragraph" regardless of actual
    length until this was caught by a competitor-derived target coming out
    as 1 paragraph for a 6,000-word article."""
    blocks = [b.strip() for b in extracted_text.split("\n\n")]
    return len([b for b in blocks if b and not b.startswith("#")])


def _word_count(extracted_text: str) -> int:
    """A naive .split() on markdown output counts '#' as its own token per
    heading line - strip the marker first so a page with many headings
    doesn't get its word count inflated by one spurious token per heading."""
    stripped = "\n".join(_HEADING_LINE_RE.sub("", ln) for ln in extracted_text.splitlines())
    return len(stripped.split())


def scrape_page(url: str, timeout: float = 15.0) -> Optional[dict]:
    if not url:
        return None
    try:
        resp = httpx.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception:
        return None

    html = resp.text
    # markdown output, not the default 'txt' - trafilatura's txt format
    # separates paragraphs with a single newline, the same as a mid-
    # paragraph line wrap, making paragraph boundaries undetectable. The
    # markdown format uses real blank-line paragraph breaks and real '#'
    # heading markers.
    text = trafilatura.extract(
        html, include_comments=False, include_tables=False, output_format="markdown",
    ) or ""
    if len(text) < MIN_USABLE_CHARS:
        return None

    soup = BeautifulSoup(html, "html.parser")
    flat_headings = [
        {"level": tag.name, "text": stripped}
        for tag in soup.find_all(["h1", "h2", "h3"])
        if (stripped := tag.get_text(strip=True))
    ]

    title_tag = soup.find("title")
    meta_desc_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})

    return {
        "url": url,
        "html": html,  # full, untruncated - the caller decides whether/where to persist it
        "text": text[:MAX_CONTENT_CHARS],
        "text_truncated": len(text) > MAX_CONTENT_CHARS,
        "word_count": _word_count(text),
        "paragraph_count": _count_paragraphs(text),
        "headings": flat_headings,
        "heading_tree": _heading_tree(flat_headings),
        "table_count": len(soup.find_all("table")),
        "meta_title": title_tag.get_text(strip=True) if title_tag else None,
        "meta_description": meta_desc_tag.get("content", "").strip() if meta_desc_tag else None,
    }
