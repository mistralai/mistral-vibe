"""Centralised backend configuration loaded from environment / .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings — values are sourced from env vars or ``.env``."""

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Mistral API ──────────────────────────────────────────────────────
    mistral_api_key: str = Field(default="", description="Mistral La Plateforme API key")

    # Model identifiers
    mistral_large_model: str = "mistral-large-latest"
    mistral_small_model: str = "mistral-small-latest"
    magistral_medium_model: str = "magistral-medium-latest"
    mistral_ocr_model: str = "mistral-ocr-latest"
    mistral_embed_model: str = "mistral-embed"
    mistral_moderation_model: str = "mistral-moderation-latest"
    voxtral_model: str = "mistral-large-latest"  # fallback; real endpoint uses audio API

    # ── Database ─────────────────────────────────────────────────────────
    database_url: str = Field(
        default=f"sqlite:///{_PROJECT_ROOT / 'data' / 'boredgames.db'}",
        description="SQLite connection URL for local persistence",
    )

    # ── Server ───────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"])
    debug: bool = False


settings = Settings()
