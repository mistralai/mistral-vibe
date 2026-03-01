"""Rulebook OCR processing — extract rules from uploaded images.

Implements PRD §5.1‑A *Find Game by Image* using ``mistral-ocr-2512``
for document extraction and ``mistral-large-2512`` for normalisation.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.models.game import GameSchema, OCRUploadResponse
from backend.services.mistral_client import chat_completion, get_client

logger = logging.getLogger(__name__)

_NORMALISE_SYSTEM = """\
You are a board game rule normaliser. Given raw OCR text extracted from a
rulebook, produce a clean JSON game schema with these keys:
{
  "game_name": "...",
  "player_count": "...",
  "components": [{"name": "...", "description": "...", "quantity": null}],
  "setup": "...",
  "turn_structure": [{"name": "...", "description": "...", "actions": ["..."]}],
  "victory_conditions": ["..."],
  "edge_cases": ["..."],
  "scoring": "... or null"
}
Fix OCR artefacts, correct spelling, and fill in implied information.
Return ONLY valid JSON.
"""


async def process_rulebook_image(image_bytes: bytes, filename: str = "rulebook.jpg") -> OCRUploadResponse:
    """Run OCR on an uploaded rulebook image and normalise the result."""
    extracted = await _run_ocr(image_bytes, filename)
    schema = await _normalise_ocr_text(extracted)
    return OCRUploadResponse(extracted_text=extracted, game_schema=schema)


async def _run_ocr(image_bytes: bytes, filename: str) -> str:
    """Call Mistral OCR endpoint or fall back to mock."""
    client = get_client()
    if client is None:
        return _mock_ocr_text(filename)

    try:
        b64 = base64.standard_b64encode(image_bytes).decode()
        mime = "image/jpeg" if filename.lower().endswith((".jpg", ".jpeg")) else "image/png"
        data_url = f"data:{mime};base64,{b64}"

        response = client.ocr.process(
            model=settings.mistral_ocr_model,
            document={"type": "image_url", "image_url": data_url},
        )
        # The OCR response contains pages with markdown text
        pages_text = []
        for page in response.pages:
            pages_text.append(page.markdown)
        return "\n\n".join(pages_text)
    except Exception:
        logger.exception("OCR processing failed")
        return _mock_ocr_text(filename)


async def _normalise_ocr_text(raw_text: str) -> GameSchema:
    """Normalise raw OCR text into a structured game schema."""
    raw = await chat_completion(
        messages=[
            {"role": "system", "content": _NORMALISE_SYSTEM},
            {"role": "user", "content": f"Normalise this rulebook text:\n\n{raw_text[:8000]}"},
        ],
        model=settings.mistral_large_model,
        temperature=0.1,
        max_tokens=8192,
    )

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "mock" in data:
            return _mock_schema(raw_text)
        return GameSchema.model_validate(data)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("Failed to normalise OCR text")
        return _mock_schema(raw_text)


def _mock_ocr_text(filename: str) -> str:
    return (
        f"[Mock OCR output for {filename}]\n\n"
        "Game Setup: Place the board in the center. Each player takes a set of pieces.\n"
        "Turns: Players take turns clockwise. On your turn, roll the dice and move.\n"
        "Winning: First player to reach the goal wins."
    )


def _mock_schema(raw_text: str) -> GameSchema:
    first_line = raw_text.split("\n")[0].strip() if raw_text else "Unknown Game"
    return GameSchema(
        game_name=first_line[:50],
        setup="Set up the board as described in the rulebook.",
        turn_structure=[{"name": "Turn", "description": "Take your turn", "actions": ["Roll dice", "Move"]}],
        victory_conditions=["Reach the goal first"],
    )
