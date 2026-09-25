import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # .strip() on every secret - a Render/GitHub env var pasted with a
    # trailing newline is otherwise invisible until it breaks an HTTP header
    # ("Illegal header value b'Token ...\n'") deep inside a client call, real
    # incident against SERANKING_API_KEY.
    deepseek_api_key: str = os.environ["DEEPSEEK_API_KEY"].strip()
    scrappa_api_key: str = os.environ["SCRAPPA_API_KEY"].strip()
    seranking_api_key: str = os.environ["SERANKING_API_KEY"].strip()
    supabase_url: str = os.environ["SUPABASE_URL"].strip()
    supabase_key: str = os.environ["SUPABASE_KEY"].strip()

    # DeepSeek call budget for the run (seed generation + relevance filter +
    # judge calls). Judge calls are batched now (judge_batch_size keywords
    # per call, see seo_judge.judge_keywords_batch), so this rarely binds in
    # practice even with every demand-validated candidate getting judged -
    # e.g. 45 candidates costs ~9 judge calls at batch size 5, not 45.
    max_tool_calls_per_run: int = int(os.environ.get("MAX_TOOL_CALLS_PER_RUN", 50))

    # How many keywords go into one SEO Judge DeepSeek call. Revived
    # 2026-09-24 alongside "judge every demand-validated candidate, not just
    # until the daily target" - batching amortizes the judge's fixed system
    # prompt (~1,850 of a call's ~2,000 tokens, measured directly) across
    # several keywords instead of paying for it per keyword.
    judge_batch_size: int = int(os.environ.get("JUDGE_BATCH_SIZE", 5))
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

    # Seeds per niche. Cost scales roughly linearly with this: each seed
    # costs ~10-11 Scrappa autocomplete calls (~$0.003-0.0033, see
    # discovery.py's BFS) plus questions_limit_per_seed x 10 SE Ranking
    # credits. Demand validation stays one flat call regardless.
    seeds_per_niche: int = int(os.environ.get("SEEDS_PER_NICHE", 30))

    questions_limit_per_seed: int = int(os.environ.get("QUESTIONS_LIMIT_PER_SEED", 15))
    # `related` defaults OFF: ~98% real-volume hit rate, but it returns broad
    # head terms and produced 0 approvals across every test so far while
    # costing ~$0.30/run. Set >0 to re-enable if later data shows it converting.
    related_limit_per_seed: int = int(os.environ.get("RELATED_LIMIT_PER_SEED", 0))



settings = Settings()
