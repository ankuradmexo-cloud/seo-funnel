import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


class Settings:
    deepseek_api_key: str = os.environ["DEEPSEEK_API_KEY"]
    seranking_api_key: str = os.environ["SERANKING_API_KEY"]

    # How many top organic results to treat as competitors. Raised from 10 to
    # 20 - a single SERP page (~9-10 organic results) routinely has several
    # App Store/Play Store listings and forum threads that get excluded from
    # the structural targets (see competitor_scraping.py's content_type
    # filtering), so page 1 alone left as few as 3 usable competitors on one
    # run. SE Ranking's serp/classic task returns up to 100 organic results
    # in one call, well above this, so no paging is needed to reach it.
    competitor_count: int = int(os.environ.get("COMPETITOR_COUNT", 20))

    # Candidates generated PER OUTLINE SECTION (keyword_expansion.py), not a
    # global total. Replaces the old MAX_KEYWORD_OPPORTUNITIES, which capped
    # one article-wide batch before per-section generation existed - that
    # semantic doesn't apply anymore. 5/section x ~10 sections lands in a
    # similar total ballpark to the old default of 50.
    max_keywords_per_section: int = int(os.environ.get("MAX_KEYWORDS_PER_SECTION", 5))

    # Optional - reddit_research.py degrades to zero threads (as it already
    # does today) when these aren't set, same graceful-empty behavior as
    # before. Set both to switch from unauthenticated old.reddit.com
    # scraping (measured: blocked on every run of a 5-keyword batch) to
    # Reddit's actual OAuth API. Create a "script" type app at
    # reddit.com/prefs/apps - the client_id is the string under the app
    # name, client_secret is labeled "secret". This uses the app-only
    # client_credentials grant (no Reddit username/password needed) which
    # is free for this volume of use.
    reddit_client_id: Optional[str] = os.environ.get("REDDIT_CLIENT_ID")
    reddit_client_secret: Optional[str] = os.environ.get("REDDIT_CLIENT_SECRET")
    reddit_user_agent: str = os.environ.get(
        "REDDIT_USER_AGENT", "article-intelligence-research/0.2 (local SEO content research tool)"
    )

    # Optional - pipeline/hero_image.py degrades to no image (not a crash)
    # when unset, same pattern as the Reddit credentials above.
    pexels_api_key: Optional[str] = os.environ.get("PEXELS_API_KEY")

    # Optional - shares the same Supabase project as the rest of this repo
    # (this file lives at seo-funnel/article-pipeline/, load_dotenv() with no
    # path walks up to seo-funnel/.env automatically). Unset means this run
    # is purely local: no interlinking, no WordPress publish, no automation -
    # everything else in the pipeline works exactly as before, same
    # degrade-gracefully pattern as Reddit/Pexels above.
    supabase_url: Optional[str] = os.environ.get("SUPABASE_URL")
    supabase_key: Optional[str] = os.environ.get("SUPABASE_KEY")

    # 'publish' or 'draft'. Everything ships live by default per the current
    # decision - flip to 'draft' in .env to require manual review in WP
    # admin before anything goes public again.
    wp_publish_status: str = os.environ.get("WP_PUBLISH_STATUS", "publish")

    # How many already-published articles on the same site to consider as
    # interlinking candidates, and the max links a single new article inserts.
    interlinking_candidate_limit: int = int(os.environ.get("INTERLINKING_CANDIDATE_LIMIT", 30))
    interlinking_max_links: int = int(os.environ.get("INTERLINKING_MAX_LINKS", 5))

    # How many older, already-live articles get checked as backlink targets
    # when a new one publishes (see pipeline/backlinking.py).
    backlinking_candidate_limit: int = int(os.environ.get("BACKLINKING_CANDIDATE_LIMIT", 10))


settings = Settings()
