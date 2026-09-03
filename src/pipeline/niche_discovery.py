from src.clients.deepseek_client import DeepSeekClient
from src.models.schemas import NicheDiscoveryOutput

SYSTEM_PROMPT = """You find NEW SEO niches for a small website within its category - niches \
genuinely viable for a small/low-authority site, not high-competition head terms. You'll be \
given the category and every niche already being worked on. Do NOT propose a niche that is \
the same topic reworded (e.g. "Productivity Software for Remote Workers" and "Productivity \
Apps for Remote Workers" are the same niche - only one should exist). Only propose niches that \
cover genuinely different sub-topics, audiences, or angles within the category. It's fine to \
return an empty list if the existing niches already cover the category reasonably well. Return \
JSON matching the required schema only."""


def discover_niches(
    deepseek: DeepSeekClient, category: str, existing_niche_names: list[str]
) -> NicheDiscoveryOutput:
    user_prompt = (
        f"Website category: {category}\n"
        f"Existing niches already being worked on:\n"
        + ("\n".join(f"- {n}" for n in existing_niche_names) or "(none yet)")
        + "\n\nPropose up to 4 genuinely new niches, or none if the category is already well covered."
    )
    return deepseek.structured_call(SYSTEM_PROMPT, user_prompt, NicheDiscoveryOutput)
