"""FastAPI application — BoardGame AI Moderator backend.

Start with::

    uvicorn backend.main:app --reload --port 8000

Or::

    python -m backend.main
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.database import init_db
from backend.routers import games, moderation, rules, sessions, voice

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)

app = FastAPI(
    title="BoardGame AI Moderator API",
    description=(
        "Backend API for the BoardGame AI Moderator — powered by Mistral AI. "
        "Provides game search, rulebook OCR, interactive Q&A, house-rule "
        "validation, live moderation, dispute resolution, voice transcription, "
        "and content safety."
    ),
    version="1.0.0",
)

# ── CORS — allow the local frontend dev server ──────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register route modules ──────────────────────────────────────────────
app.include_router(games.router)
app.include_router(sessions.router)
app.include_router(rules.router)
app.include_router(moderation.router)
app.include_router(voice.router)


@app.on_event("startup")
async def _startup() -> None:
    init_db()
    logging.getLogger(__name__).info("Database initialised — server ready")


@app.get("/api/health")
async def health():
    """Simple health-check endpoint."""
    return {"status": "ok", "api_key_configured": bool(settings.mistral_api_key)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
