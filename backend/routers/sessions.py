"""Game session management — create, advance turns, update scores, finish."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import GameRecord, GameSession, get_db
from backend.models.session import (
    Player,
    ScoreUpdateRequest,
    SessionCreate,
    SessionState,
    SessionSummary,
    TurnAdvanceRequest,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

DB = Annotated[Session, Depends(get_db)]


def _load_session(db: Session, session_id: int) -> GameSession:
    record = db.query(GameSession).get(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    return record


def _session_state(record: GameSession, game_name: str = "") -> SessionState:
    players = [Player.model_validate(p) for p in json.loads(record.players_json)]
    return SessionState(
        id=record.id,
        game_id=record.game_id,
        game_name=game_name,
        players=players,
        turn_count=json.loads(record.state_json).get("turn_count", 0),
        status=record.status,
        house_rules=json.loads(record.house_rules_json),
        history=json.loads(record.history_json),
    )


@router.post("/", response_model=SessionState)
async def create_session(body: SessionCreate, db: DB = None):  # type: ignore[assignment]
    """Start a new game session."""
    game = db.query(GameRecord).get(body.game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found — search and save a game first.")

    # Mark first player as active
    players = [p.model_dump() for p in body.players]
    if players:
        players[0]["is_current_turn"] = True

    record = GameSession(
        game_id=body.game_id,
        players_json=json.dumps(players),
        state_json=json.dumps({"turn_count": 1}),
        status="playing",
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return _session_state(record, game.name)


@router.get("/{session_id}", response_model=SessionState)
async def get_session(session_id: int, db: DB = None):  # type: ignore[assignment]
    """Get current session state."""
    record = _load_session(db, session_id)
    game = db.query(GameRecord).get(record.game_id)
    return _session_state(record, game.name if game else "")


@router.post("/advance-turn", response_model=SessionState)
async def advance_turn(body: TurnAdvanceRequest, db: DB = None):  # type: ignore[assignment]
    """Advance to the next player's turn."""
    record = _load_session(db, body.session_id)
    players = json.loads(record.players_json)

    current_idx = next((i for i, p in enumerate(players) if p.get("is_current_turn")), 0)
    for p in players:
        p["is_current_turn"] = False
    next_idx = (current_idx + 1) % len(players)
    players[next_idx]["is_current_turn"] = True

    state = json.loads(record.state_json)
    state["turn_count"] = state.get("turn_count", 0) + 1

    record.players_json = json.dumps(players)
    record.state_json = json.dumps(state)
    history = json.loads(record.history_json)
    history.append(f"Turn {state['turn_count']}: {players[next_idx]['name']}'s turn")
    record.history_json = json.dumps(history)
    db.commit()

    game = db.query(GameRecord).get(record.game_id)
    return _session_state(record, game.name if game else "")


@router.post("/update-score", response_model=SessionState)
async def update_score(body: ScoreUpdateRequest, db: DB = None):  # type: ignore[assignment]
    """Update a player's score."""
    record = _load_session(db, body.session_id)
    players = json.loads(record.players_json)

    for p in players:
        if p["id"] == body.player_id:
            p["score"] = max(0, p.get("score", 0) + body.delta)
            break
    else:
        raise HTTPException(status_code=404, detail="Player not found in session")

    record.players_json = json.dumps(players)
    db.commit()

    game = db.query(GameRecord).get(record.game_id)
    return _session_state(record, game.name if game else "")


@router.post("/{session_id}/finish", response_model=SessionSummary)
async def finish_session(session_id: int, db: DB = None):  # type: ignore[assignment]
    """End the game and return a summary."""
    record = _load_session(db, session_id)
    record.status = "finished"
    db.commit()

    game = db.query(GameRecord).get(record.game_id)
    players = [Player.model_validate(p) for p in json.loads(record.players_json)]
    state = json.loads(record.state_json)
    house_rules = json.loads(record.house_rules_json)
    history = json.loads(record.history_json)

    winner = max(players, key=lambda p: p.score) if players else None
    disputes = db.query(GameSession).filter_by(id=session_id).count()  # simplified

    return SessionSummary(
        game_name=game.name if game else "",
        total_turns=state.get("turn_count", 0),
        winner_name=winner.name if winner else None,
        winner_color=winner.color if winner else None,
        house_rules_active=len(house_rules),
        disputes_resolved=0,
        rules_explained=len([h for h in history if "rule" in h.lower()]),
    )
