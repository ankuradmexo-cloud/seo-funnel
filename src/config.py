import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # .strip() on every secret - a Render/GitHub env var pasted with a
    # trailing newline is otherwise invisible until it breaks an HTTP header
    # ("Illegal header value b'Token ...\n'") deep inside a client call, real
    # incident against SERANKING_API_KEY.
    deepseek_api_key: str = os.environ["DEEPSEEK_API_KEY"].strip()
    seranking_api_key: str = os.environ["SERANKING_API_KEY"].strip()
    supabase_url: str = os.environ["SUPABASE_URL"].strip()
    supabase_key: str = os.environ["SUPABASE_KEY"].strip()

    # DeepSeek calls per run: 1 seed generation + up to
    # max_candidates_to_judge_per_run SEO Judge calls. LLM bulk keyword
    # generation was removed (0-0.8% real-volume hit rate across four tests),
    # so this no longer needs the large headroom it did.
    #
    # Also doubles as the worst-case cap on SE Ranking SERP checks (one per
    # judged candidate, see serp_validation.py) - this was fine at 100 back
    # when Scrappa billed ~1 credit/check, but SE Ranking's serp/classic
    # task bills 50 credits/check, a 50x jump per unit. Left at the old
    # value, the worst case alone was 5,000 credits; halved here so the
    # same safety margin costs proportionally less now that each check is
    # far more expensive.
    max_tool_calls_per_run: int = int(os.environ.get("MAX_TOOL_CALLS_PER_RUN", 50))
    max_keywords_per_site_per_day: int = int(
        os.environ.get("MAX_KEYWORDS_PER_SITE_PER_DAY", 2)
    )
    # Safety rail only - not an active filter. The judge loop's real stopping
    # conditions are the daily target and
    # max_tool_calls_per_run. At 50 this was silently truncating a 137-candidate
    # pool down to 50 and discarding usable keywords.
    max_candidates_to_judge_per_run: int = int(
        os.environ.get("MAX_CANDIDATES_TO_JUDGE_PER_RUN", 200)
    )

    # Candidates harder than this never reach the judge. A small/low-authority
    # site does not win difficulty>40 head terms: every such candidate in run 16
    # (difficulty 52-69) was judged and rejected, wasting a SERP + judge call
    # each. Set to 100 to disable this cutoff entirely.
    max_difficulty_to_judge: int = int(os.environ.get("MAX_DIFFICULTY_TO_JUDGE", 40))

    # Seeds per niche. Cost scales roughly linearly with this: each seed costs
    # (similar_limit_per_seed + questions_limit_per_seed) x 10 SE Ranking
    # credits. Demand validation stays one flat call regardless.
    seeds_per_niche: int = int(os.environ.get("SEEDS_PER_NICHE", 30))

    # Replaces the old Scrappa autocomplete BFS as the discovery volume
    # driver - SE Ranking's semantically-similar-keywords endpoint, a single
    # flat call per seed instead of a multi-level BFS. Originally defaulted
    # to 30 to match autocomplete's old breadth - a real mistake: Scrappa
    # billed ~1 credit/call flat regardless of volume, but this endpoint
    # bills 10 credits per RETURNED keyword, so the same number meant a 30x
    # bigger bill for a source with zero measured yield data. Cut to 5 -
    # still gives a real breadth signal per seed - until a real batch
    # through the judge shows it's worth spending more on.
    similar_limit_per_seed: int = int(os.environ.get("SIMILAR_LIMIT_PER_SEED", 5))

    questions_limit_per_seed: int = int(os.environ.get("QUESTIONS_LIMIT_PER_SEED", 15))
    # `related` defaults OFF: ~98% real-volume hit rate, but it returns broad
    # head terms and produced 0 approvals across every test so far while
    # costing ~$0.30/run. Set >0 to re-enable if later data shows it converting.
    related_limit_per_seed: int = int(os.environ.get("RELATED_LIMIT_PER_SEED", 0))



settings = Settings()
