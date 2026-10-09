"""Read-only audit: scans every published article's live page for internal
links that fail pipeline.interlinking.anchor_is_acceptable. Prints findings;
changes nothing. Usage: python tools/audit_internal_links.py [--website-id N]"""

import argparse
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clients import db_client  # noqa: E402
from pipeline.interlinking import anchor_is_acceptable  # noqa: E402


def find_bad_links(website_id=None) -> list[dict]:
    q = db_client._client.table("published_articles").select(
        "article_id,website_id,wp_post_id,title,wp_post_url")
    if website_id:
        q = q.eq("website_id", website_id)
    arts = q.execute().data
    all_arts = db_client._client.table("published_articles").select("title,wp_post_url").execute().data
    title_by_url = {a["wp_post_url"].rstrip("/"): a["title"] for a in all_arts if a.get("wp_post_url")}
    bad = []
    for a in sorted(arts, key=lambda x: x["article_id"]):
        if not a.get("wp_post_url"):
            continue
        html = httpx.get(a["wp_post_url"], follow_redirects=True, timeout=30).text
        m = re.search(r'<div class="[^"]*entry-content[^"]*">(.*?)<footer|<article.*?</article>', html, re.S)
        body = m.group(0) if m else html
        own = a["wp_post_url"].rstrip("/")
        for href, txt in re.findall(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.S):
            href_n = href.rstrip("/")
            txt = re.sub(r"<[^>]+>", "", txt).strip()
            if href_n == own or href_n not in title_by_url or not txt:
                continue
            if not anchor_is_acceptable(txt, title_by_url[href_n], href):
                bad.append({**a, "anchor": txt, "href": href, "dest_title": title_by_url[href_n]})
    return bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--website-id", type=int)
    for b in find_bad_links(ap.parse_args().website_id):
        print(f"#{b['article_id']} {b['title'][:45]!r}: {b['anchor']!r} -> {b['dest_title'][:50]!r}")
