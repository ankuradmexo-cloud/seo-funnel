from src.clients.deepseek_client import DeepSeekClient
from src.config import settings
from src.models.schemas import SeedGenerationOutput

SYSTEM_PROMPT = """You generate SHORT seed keywords for SEO keyword research tools.

These seeds get fed into keyword databases (SE Ranking related/questions) and Google \
autocomplete, which are indexed on HEAD TERMS. Long specific phrases return almost nothing - \
measured: the seed "best productivity apps for remote workers" returned 1 keyword, while the \
short seed "productivity software" returned 15.

So: produce SHORT head terms, 2-4 words. Not full descriptive queries.

But they must also be UNAMBIGUOUS about the domain, because expansion tools drift badly on \
generic terms. Measured examples of that drift: the seed "communication tools" returned nursing \
and stroke-patient queries; "process automation" returned engineering job listings and salary \
searches; "time tracking" returned dictionary/translation results. Each of those wasted an \
entire run.

So anchor every seed to its actual domain - usually by including a word like software, app, \
tool, platform, or the specific product category.

Good: "team communication software", "workflow automation software", "travel journal app",
      "password manager", "time tracking app"
Bad (too long): "best travel journal app for solo backpackers in 2026"
Bad (too ambiguous): "communication tools", "process automation", "time tracking"

Return JSON matching the required schema only."""


def generate_seeds(deepseek: DeepSeekClient, category: str, niche_name: str) -> SeedGenerationOutput:
    user_prompt = (
        f"Website category: {category}\n"
        f"Niche: {niche_name}\n"
        f"Generate {settings.seeds_per_niche} short head-term seed keywords (2-4 words each) "
        f"for this niche, each unambiguously anchored to this niche's domain. Cover distinct "
        f"sub-angles of the niche rather than rewording the same idea - these all get expanded "
        f"separately, so near-duplicate seeds waste a whole expansion pass each."
    )
    return deepseek.structured_call(SYSTEM_PROMPT, user_prompt, SeedGenerationOutput)
