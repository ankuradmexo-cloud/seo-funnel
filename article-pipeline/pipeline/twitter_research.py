"""Step 8b - Twitter/X trend research. DEFERRED - not actually implemented.

Unlike Reddit, X has no free, unauthenticated search path - the old
"scrape the public site" tricks were shut down, and the official API's
search endpoint requires a paid developer tier (Basic or above) to query
recent tweets at all. There is no credential for this in .env, and
provisioning one is a cost/account decision, not something to route around
in code.

This returns an empty result unconditionally so the pipeline's trend-
research step degrades the same way it already does when Reddit is
rate-limited - community_research.py works fine on zero threads from either
source. If a paid X API key becomes available, replace the body of
research_twitter() with a real call; the return shape (a list of dicts with
at least "title" so it's interchangeable with reddit_research's output) is
already what community_research.py expects.
"""


def research_twitter(keyword: str, limit: int = 15) -> list[dict]:
    return []
