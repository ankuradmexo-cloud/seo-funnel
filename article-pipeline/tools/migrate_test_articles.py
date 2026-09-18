"""One-off migration: republish the 14 articles generated during WordPress-
integration testing (currently live on the disposable test droplet) to
their real target websites, using the already-real keyword_id/website_id
mapping that was true even during testing - only the WP target changes.

No regeneration, no new DeepSeek writing calls - reconstructs each post
from its original local output/{slug}.md + logs/{run}/10_article_strategy.json
+ logs/{run}/*_hero_image.json, run through the CURRENT (already-fixed)
rendering code (orchestrator._wp_body_html), so bugs fixed after the
original test run (duplicate title/image, over-length meta description)
don't need to be separately patched - they just don't happen this time.
Not sourced from the live test site's HTML, since some of those posts
still carry manual patches applied directly via the API earlier and the
test site's Application Password was already overwritten with the real
sites' credentials by the time this runs.

Two passes per website, mirroring the original run order:
1. Publish each article (in original chronological order), rewriting any
   forward-interlinks already baked into the markdown from old test-site
   URLs to the new real URLs as each new mapping becomes known.
2. Run backlinking for real, in the same order - each article (after the
   first per site) gets checked against the real, now-migrated earlier
   articles on the same site, exactly like a normal run would.
"""

import json
import random
import re
import sys
from glob import glob
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clients import db_client, wordpress_client
from clients.deepseek_client import DeepSeekClient
from orchestrator import _wp_body_html
from pipeline.article_strategy import _fix_meta_description
from pipeline.backlinking import backlink_older_articles
from pipeline.categorize import choose_category

OUTPUT_DIR = Path(__file__).parent.parent / "output"
LOGS_DIR = Path(__file__).parent.parent / "logs"


def _load_run_data(slug: str) -> dict:
    matches = sorted(glob(str(LOGS_DIR / f"*{slug}*")))
    if not matches:
        raise FileNotFoundError(f"No log dir found for slug {slug!r}")
    run_dir = Path(matches[-1])
    outline = json.loads((run_dir / "10_article_strategy.json").read_text())
    summary = json.loads((run_dir / "_run_summary.json").read_text())
    hero_files = sorted(run_dir.glob("*_hero_image.json"))
    hero_image = json.loads(hero_files[-1].read_text()) if hero_files else None
    # orchestrator.py logs {"found": False} (no "url" key) when Pexels found
    # nothing for that run - not a real image dict, treat the same as None.
    if hero_image and "url" not in hero_image:
        hero_image = None
    article_markdown = (OUTPUT_DIR / f"{slug}.md").read_text()
    return {
        "outline": outline, "summary": summary, "hero_image": hero_image,
        "article_markdown": article_markdown,
    }


def migrate_website(website_id: int, articles: list[dict], deepseek: DeepSeekClient) -> None:
    wp_config = db_client.get_website_wp_config(website_id)
    if not wp_config or not wp_config.get("wp_base_url"):
        print(f"  SKIP website_id={website_id}: no WordPress config")
        return
    author_pool = wp_config.get("wp_author_ids") or []
    available_categories = db_client.get_website_categories(website_id)

    old_to_new_url: dict[str, str] = {}
    migrated: list[dict] = []  # [{article_id, wp_post_id, wp_post_url, title}, ...] in order

    print(f"\n=== Pass 1: publishing website_id={website_id} ({wp_config['name']}) ===")
    for row in articles:
        slug = row["slug"]
        print(f"  {row['title'][:60]!r}...")
        data = _load_run_data(slug)
        outline = data["outline"]
        summary = data["summary"]
        hero_image = data["hero_image"]
        keyword = summary.get("keyword", slug.replace("-", " "))
        article_markdown = data["article_markdown"]

        # Rewrite any forward-interlinks already baked into this article's
        # markdown from old test-site URLs to the new real URLs published
        # so far this pass.
        for old_url, new_url in old_to_new_url.items():
            article_markdown = article_markdown.replace(old_url, new_url)

        fixed_meta = _fix_meta_description(deepseek, outline["meta_description"], keyword)

        category_id = None
        if available_categories:
            chosen = choose_category(deepseek, outline["title"], keyword, available_categories)
            category_id = wordpress_client.get_or_create_category(wp_config, chosen)

        author_id = random.choice(author_pool) if author_pool else None

        media_id = None
        if hero_image:
            media_id = wordpress_client.upload_featured_image(wp_config, hero_image, outline["title"])

        html_content = _wp_body_html(article_markdown, outline["title"], hero_image)

        post = wordpress_client.create_post(
            wp_config, outline["title"], html_content, fixed_meta,
            media_id, status="publish", slug=slug,
            category_id=category_id, author_id=author_id,
        )
        if not post:
            print(f"    FAILED to create post for {slug}")
            continue

        meta_ok = wordpress_client.set_seo_meta(wp_config, post["id"], outline["title"], fixed_meta)

        old_url = row["wp_post_url"]
        if old_url:
            old_to_new_url[old_url] = post["link"]

        db_client.update_published_article(row["article_id"], {
            "wp_post_id": post["id"], "wp_post_url": post["link"],
            "quality_gate_verdict": summary.get("quality_gate", {}).get("verdict"),
            "cost_usd": summary.get("cost", {}).get("total_cost_usd"),
            "hero_image_url": (hero_image or {}).get("url"),
        })

        migrated.append({
            "article_id": row["article_id"], "wp_post_id": post["id"],
            "wp_post_url": post["link"], "title": outline["title"],
        })
        print(f"    -> {post['link']} (meta_ok={meta_ok}, category_id={category_id}, author_id={author_id})")

    print(f"\n=== Pass 2: backlinking website_id={website_id} ===")
    for i, article in enumerate(migrated):
        older = [
            {"wp_post_id": m["wp_post_id"], "wp_post_url": m["wp_post_url"], "title": m["title"]}
            for m in migrated[:i]
        ]
        if not older:
            continue
        report = backlink_older_articles(
            article["title"], article["wp_post_url"], older, wp_config, deepseek, limit=10,
        )
        print(f"  {article['title'][:50]!r}: checked={report['checked']} linked={report['linked']}")


def main():
    deepseek = DeepSeekClient()
    rows = db_client.list_all_published_articles()

    by_website: dict[int, list[dict]] = {}
    for row in rows:
        by_website.setdefault(row["website_id"], []).append(row)

    for website_id, articles in by_website.items():
        migrate_website(website_id, articles, deepseek)


if __name__ == "__main__":
    main()
