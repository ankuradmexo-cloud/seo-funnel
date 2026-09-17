"""Publishes a finished article to WordPress via the REST API, using
Application Password Basic Auth (base64(username:app_password) built inline,
never logged - see _auth_header). Every function here returns None/False on
any failure (network, 4xx/5xx, missing config) rather than raising - a
publish failure must never crash a run that otherwise produced a good
article; it just means the run summary records it as generated-but-not-published.
"""

import base64
import io
import re
from typing import Optional

import httpx
from PIL import Image

HERO_WIDTH = 1200
HERO_HEIGHT = 600
_TIMEOUT = 30


def _auth_header(wp_config: dict) -> dict:
    token = base64.b64encode(
        f"{wp_config['wp_username']}:{wp_config['wp_app_password']}".encode()
    ).decode()
    return {"Authorization": f"Basic {token}"}


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "article"


def upload_featured_image(wp_config: dict, image: dict, article_title: str) -> Optional[int]:
    """Downloads the Pexels hero image (never hotlinks it into WordPress),
    crops/resizes to exactly 1200x600, and uploads it under a filename and
    alt text derived from the article - not Pexels' random filename/alt.
    Pexels' own attribution requirement is satisfied separately, in the
    article body text (see orchestrator.py) - it doesn't need to ride on
    the image file itself."""
    if not wp_config.get("wp_base_url"):
        return None
    try:
        raw = httpx.get(image["url"], timeout=_TIMEOUT).content
        photo = Image.open(io.BytesIO(raw)).convert("RGB")
        photo = _crop_to(photo, HERO_WIDTH, HERO_HEIGHT)
        buf = io.BytesIO()
        photo.save(buf, format="JPEG", quality=85)
        buf.seek(0)

        filename = f"{_slugify(article_title)}.jpg"
        alt_text = f"{article_title} - hero image"

        resp = httpx.post(
            f"{wp_config['wp_base_url'].rstrip('/')}/wp-json/wp/v2/media",
            headers={
                **_auth_header(wp_config),
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": "image/jpeg",
            },
            content=buf.read(),
            timeout=_TIMEOUT,
        )
        if resp.status_code not in (200, 201):
            return None
        media_id = resp.json()["id"]

        httpx.post(
            f"{wp_config['wp_base_url'].rstrip('/')}/wp-json/wp/v2/media/{media_id}",
            headers=_auth_header(wp_config),
            json={"alt_text": alt_text},
            timeout=_TIMEOUT,
        )
        return media_id
    except Exception:
        return None


def _crop_to(img: Image.Image, width: int, height: int) -> Image.Image:
    target_ratio = width / height
    src_ratio = img.width / img.height
    if src_ratio > target_ratio:
        new_width = int(img.height * target_ratio)
        left = (img.width - new_width) // 2
        img = img.crop((left, 0, left + new_width, img.height))
    else:
        new_height = int(img.width / target_ratio)
        top = (img.height - new_height) // 2
        img = img.crop((0, top, img.width, top + new_height))
    return img.resize((width, height), Image.LANCZOS)


def create_user(
    wp_config: dict, username: str, email: str, first_name: str, last_name: str,
    role: str = "author",
) -> Optional[int]:
    """One-time setup helper for author-rotation personas, not called on
    every run - see scripts that seed a website's wp_author_ids. Requires
    the authenticated account to have create_users capability
    (administrator). WordPress requires a password even for an account
    that will never log in - a random one is generated and discarded."""
    import secrets

    if not wp_config.get("wp_base_url"):
        return None
    try:
        resp = httpx.post(
            f"{wp_config['wp_base_url'].rstrip('/')}/wp-json/wp/v2/users",
            headers=_auth_header(wp_config),
            json={
                "username": username,
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "name": f"{first_name} {last_name}",
                "password": secrets.token_urlsafe(24),
                "roles": [role],
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code not in (200, 201):
            return None
        return resp.json()["id"]
    except Exception:
        return None


def get_or_create_category(wp_config: dict, name: str) -> Optional[int]:
    """WordPress categories are looked up by name first (so repeated
    articles in "Software" reuse the same category instead of creating
    duplicates) and created only if genuinely new. Returns None on any
    failure - a missing category must not block publishing."""
    if not wp_config.get("wp_base_url") or not name:
        return None
    base = wp_config["wp_base_url"].rstrip("/")
    headers = _auth_header(wp_config)
    try:
        resp = httpx.get(f"{base}/wp-json/wp/v2/categories", headers=headers, params={"search": name}, timeout=_TIMEOUT)
        if resp.status_code == 200:
            for cat in resp.json():
                if cat["name"].strip().lower() == name.strip().lower():
                    return cat["id"]
        create = httpx.post(f"{base}/wp-json/wp/v2/categories", headers=headers, json={"name": name}, timeout=_TIMEOUT)
        if create.status_code in (200, 201):
            return create.json()["id"]
        return None
    except Exception:
        return None


def create_post(
    wp_config: dict, title: str, html_content: str, excerpt: str,
    featured_media_id: Optional[int], status: str = "publish", slug: Optional[str] = None,
    category_id: Optional[int] = None, author_id: Optional[int] = None,
) -> Optional[dict]:
    """Without an explicit `slug`, WordPress derives one from the FULL
    title - measured on a real post: a title with a subtitle produced a
    102-character URL, which RankMath flags as too long. Passing the
    keyword's own short slug (already computed for the local .md/.html
    filenames - see orchestrator.py) avoids that instead of fixing it after
    the fact."""
    if not wp_config.get("wp_base_url"):
        return None
    try:
        body = {"title": title, "content": html_content, "excerpt": excerpt, "status": status}
        if slug:
            body["slug"] = slug
        if featured_media_id:
            body["featured_media"] = featured_media_id
        if category_id:
            body["categories"] = [category_id]
        if author_id:
            body["author"] = author_id
        resp = httpx.post(
            f"{wp_config['wp_base_url'].rstrip('/')}/wp-json/wp/v2/posts",
            headers=_auth_header(wp_config),
            json=body,
            timeout=_TIMEOUT,
        )
        if resp.status_code not in (200, 201):
            return None
        data = resp.json()
        return {"id": data["id"], "link": data["link"], "slug": data["slug"]}
    except Exception:
        return None


def set_seo_meta(wp_config: dict, post_id: int, meta_title: str, meta_description: str) -> bool:
    """Branches on the site's configured seo_plugin. Yoast/RankMath both
    block their meta fields from REST writes by default - each site needs a
    one-time snippet (registering these meta keys with show_in_rest=True)
    before this succeeds; until then this returns False, which is logged in
    the run summary as wp_seo_meta_set=False rather than failing the run."""
    plugin = (wp_config.get("seo_plugin") or "none").lower()
    if not wp_config.get("wp_base_url"):
        return False
    try:
        base = wp_config["wp_base_url"].rstrip("/")
        if plugin == "yoast":
            meta = {"_yoast_wpseo_title": meta_title, "_yoast_wpseo_metadesc": meta_description}
        elif plugin == "rankmath":
            meta = {"rank_math_title": meta_title, "rank_math_description": meta_description}
        else:
            resp = httpx.post(
                f"{base}/wp-json/wp/v2/posts/{post_id}",
                headers=_auth_header(wp_config),
                json={"excerpt": meta_description},
                timeout=_TIMEOUT,
            )
            return resp.status_code in (200, 201)

        resp = httpx.post(
            f"{base}/wp-json/wp/v2/posts/{post_id}",
            headers=_auth_header(wp_config),
            json={"meta": meta},
            timeout=_TIMEOUT,
        )
        if resp.status_code not in (200, 201):
            return False
        # WordPress silently ignores unregistered meta keys instead of
        # erroring - a 200 doesn't prove the write actually landed, so read
        # the post back and check.
        check = httpx.get(f"{base}/wp-json/wp/v2/posts/{post_id}", headers=_auth_header(wp_config), timeout=_TIMEOUT)
        if check.status_code != 200:
            return False
        saved_meta = (check.json() or {}).get("meta") or {}
        return all(saved_meta.get(k) == v for k, v in meta.items())
    except Exception:
        return False


def get_post_content(wp_config: dict, wp_post_id: int) -> Optional[str]:
    """Fetches an OLD post's current live content, for backlinking - it may
    have been hand-edited since we published it, so this reads fresh rather
    than trusting whatever this pipeline last wrote."""
    if not wp_config.get("wp_base_url"):
        return None
    try:
        resp = httpx.get(
            f"{wp_config['wp_base_url'].rstrip('/')}/wp-json/wp/v2/posts/{wp_post_id}?context=edit",
            headers=_auth_header(wp_config),
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        return (resp.json().get("content") or {}).get("raw") or (resp.json().get("content") or {}).get("rendered")
    except Exception:
        return None


def update_post_content(wp_config: dict, wp_post_id: int, new_html_content: str) -> bool:
    """Overwrites an already-live post's content - used only by backlinking
    to insert a link to a brand-new article into an older one. There is no
    review step before this lands (see orchestrator.py/backlinking.py), so
    the caller is responsible for only ever changing a verbatim substring
    match, never regenerating the whole body."""
    if not wp_config.get("wp_base_url"):
        return False
    try:
        resp = httpx.post(
            f"{wp_config['wp_base_url'].rstrip('/')}/wp-json/wp/v2/posts/{wp_post_id}",
            headers=_auth_header(wp_config),
            json={"content": new_html_content},
            timeout=_TIMEOUT,
        )
        return resp.status_code in (200, 201)
    except Exception:
        return False
