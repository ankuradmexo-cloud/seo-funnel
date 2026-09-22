"""One-off: attach a real Pexels hero image + credit line to posts that
published without one, because PEXELS_API_KEY was never set as a GitHub
Actions secret (confirmed 2026-09-21 - local runs always had it in .env, so
this was silent in every GH-Actions-triggered publish since the WordPress
feature shipped). Not part of the regular pipeline - run by hand, once.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

from clients import db_client, wordpress_client
from clients.pexels_client import search_hero_image

TARGETS = [
    (1, "what software do djs use", "what-software-do-djs-use"),
    (3, "chumba casino withdrawal time", "chumba-casino-withdrawal-time"),
    (1, "alternatives to mailchimp", "alternatives-to-mailchimp"),
    (3, "problem gambling screening tools", "problem-gambling-screening-tools"),
    (2, "plus size dresses sale", "plus-size-dresses-sale"),
    (2, "plus size rain jacket", "plus-size-rain-jacket"),
]


def _hero_credit_html(image: dict) -> str:
    if not image.get("photographer"):
        return ""
    credit_link = (
        f'<a href="{image["photographer_url"]}">{image["photographer"]}</a>'
        if image.get("photographer_url") else image["photographer"]
    )
    pexels_link = f' on <a href="{image["pexels_url"]}">Pexels</a>' if image.get("pexels_url") else ""
    return f'<p class="hero-credit">Photo by {credit_link}{pexels_link}</p>\n'


def backfill(website_id: int, keyword: str, slug: str) -> None:
    wp_config = db_client.get_website_wp_config(website_id)
    if not wp_config:
        print(f"  SKIP {keyword}: no wp_config for website_id={website_id}")
        return

    post_type = wp_config.get("wp_post_type") or "posts"
    base = wp_config["wp_base_url"].rstrip("/")
    resp = httpx.get(
        f"{base}/wp-json/wp/v2/{post_type}",
        params={"slug": slug},
        auth=None,
        headers=wordpress_client._auth_header(wp_config),
        timeout=30,
    )
    posts = resp.json()
    if not posts:
        print(f"  SKIP {keyword}: no live post found for slug={slug}")
        return
    post = posts[0]
    post_id = post["id"]
    title = post["title"]["rendered"]

    if post.get("featured_media"):
        print(f"  SKIP {keyword}: already has featured_media={post['featured_media']}")
        return

    image = search_hero_image(keyword)
    if not image:
        print(f"  FAIL {keyword}: no Pexels result")
        return

    media_id = wordpress_client.upload_featured_image(wp_config, image, title)
    if not media_id:
        print(f"  FAIL {keyword}: image upload failed")
        return

    patch = httpx.post(
        f"{base}/wp-json/wp/v2/{post_type}/{post_id}",
        headers=wordpress_client._auth_header(wp_config),
        json={"featured_media": media_id},
        timeout=30,
    )
    if patch.status_code not in (200, 201):
        print(f"  FAIL {keyword}: featured_media PATCH returned {patch.status_code}")
        return

    content = wordpress_client.get_post_content(wp_config, post_id) or ""
    credit = _hero_credit_html(image)
    if credit and "hero-credit" not in content:
        updated = credit + content
        wordpress_client.update_post_content(wp_config, post_id, updated)

    print(f"  OK {keyword}: media_id={media_id}, {image['url']}")


if __name__ == "__main__":
    for website_id, keyword, slug in TARGETS:
        print(f"{keyword} (website_id={website_id})")
        backfill(website_id, keyword, slug)
