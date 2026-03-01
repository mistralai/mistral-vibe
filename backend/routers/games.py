"""Game search, detail, and OCR upload endpoints."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.database import GameRecord, get_db
from backend.models.game import (
    GameDetailResponse,
    GameSchema,
    GameSearchResponse,
    OCRUploadResponse,
)
from backend.services.game_search import get_game_detail, search_games
from backend.services.rulebook_ocr import process_rulebook_image

router = APIRouter(prefix="/api/games", tags=["games"])

DB = Annotated[Session, Depends(get_db)]


@router.get("/search", response_model=GameSearchResponse)
async def search(q: str = ""):
    """Search for board games by name (uses Mistral web search)."""
    if not q.strip():
        return GameSearchResponse(results=[])
    return await search_games(q)


@router.get("/{game_name}", response_model=GameDetailResponse)
async def detail(game_name: str, db: DB = None):  # type: ignore[assignment]
    """Get full game detail / normalised schema.

    If the game has been saved locally it is returned from the DB;
    otherwise it is fetched via AI and persisted.
    """
    # Check local cache first
    record = db.query(GameRecord).filter(GameRecord.name.ilike(game_name)).first()
    if record:
        schema = GameSchema.model_validate(record.schema_data)
        return GameDetailResponse(id=record.id, schema=schema)

    # Fetch via Mistral
    schema = await get_game_detail(game_name)

    # Persist for future look-ups
    new_record = GameRecord(
        name=schema.game_name,
        source="search",
        schema_json=schema.model_dump_json(),
    )
    db.add(new_record)
    db.commit()
    db.refresh(new_record)

    return GameDetailResponse(id=new_record.id, schema=schema)


@router.post("/ocr", response_model=OCRUploadResponse)
async def upload_rulebook(file: UploadFile = File(...)):
    """Upload a rulebook image for OCR processing."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are accepted.")

    image_bytes = await file.read()
    if len(image_bytes) > 20 * 1024 * 1024:  # 20 MB limit
        raise HTTPException(status_code=413, detail="File too large (max 20 MB).")

    return await process_rulebook_image(image_bytes, filename=file.filename or "upload.jpg")


@router.get("/saved/list")
async def list_saved_games(db: DB = None):  # type: ignore[assignment]
    """List all locally saved games."""
    records = db.query(GameRecord).order_by(GameRecord.updated_at.desc()).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "source": r.source,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]
