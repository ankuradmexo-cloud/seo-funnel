from typing import Literal, Optional
from pydantic import BaseModel, Field

KeywordStatus = Literal[
    "candidate", "deduped", "validated", "judged", "shortlisted", "queued", "published"
]


class Website(BaseModel):
    website_id: int
    name: str
    category: str
    seed_niches: list[str]
    active: bool = True


class Niche(BaseModel):
    niche_id: int
    website_id: int
    name: str
    status: Literal["active", "exhausted", "paused"] = "active"
    source: Literal["seed", "expansion"] = "seed"
    times_processed: int = 0


class NicheDiscoveryOutput(BaseModel):
    """category + existing niche names in -> genuinely NEW niches out.
    Runs occasionally (not every pipeline run), not per-run seed generation."""
    new_niches: list[str] = Field(default_factory=list)


class SeedGenerationOutput(BaseModel):
    """One specific niche in -> seed keywords for it. Scoped to a single
    niche per call now that niche selection/rotation happens separately."""
    seed_keywords: list[str] = Field(min_length=1)


class DiscoveryCandidate(BaseModel):
    keyword: str
    source: list[Literal["autocomplete", "questions", "related"]]


class DemandMetrics(BaseModel):
    keyword: str
    search_volume: Optional[int] = None
    cpc: Optional[float] = None
    competition: Optional[float] = None  # 0-1 paid-search competition
    difficulty: Optional[int] = None  # 0-100 organic ranking difficulty
    intents: list[Literal["I", "C", "T", "L", "N"]] = Field(default_factory=list)
    # I=informational C=commercial T=transactional L=local N=navigational
    history_trend: dict[str, int] = Field(default_factory=dict)  # "YYYY-MM-DD" -> monthly volume, up to 12 months


class SerpResult(BaseModel):
    position: Optional[int] = None
    title: Optional[str] = None
    link: Optional[str] = None
    source: Optional[str] = None
    snippet: Optional[str] = None


class SerpSignal(BaseModel):
    keyword: str
    top_results: list[SerpResult]
    total_results: Optional[int] = None


class SeoJudgeOutput(BaseModel):
    keyword: str
    approve: bool
    score: float = Field(ge=0, le=100)
    rationale: str
    intent_cluster: str


class SemanticExpansionOutput(BaseModel):
    anchor_keyword: str
    adjacent_intents: list[str]
    exhausted: bool  # true when the agent judges there's nothing new left to explore
