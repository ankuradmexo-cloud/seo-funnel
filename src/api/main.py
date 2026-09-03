import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import (
    agent_calls, automation, keywords, niches, overview, runs, usage, websites,
)

app = FastAPI(title="Keyword Funnel Dashboard API")

# The dashboard is served from Vercel, the API from Render - different
# origins, so CORS is load-bearing rather than a formality. Set
# ALLOWED_ORIGINS to a comma-separated list of real origins in production;
# the localhost defaults only cover local development.
_origins = os.environ.get(
    "ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins.split(",") if o.strip()],
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(websites.router, prefix="/api")
app.include_router(keywords.router, prefix="/api")
app.include_router(agent_calls.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(niches.router, prefix="/api")
app.include_router(overview.router, prefix="/api")
app.include_router(automation.router, prefix="/api")
app.include_router(usage.router, prefix="/api")


@app.get("/health")
def health():
    return {"ok": True}
