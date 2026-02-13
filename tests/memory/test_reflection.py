from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vibe.core.memory.config import MemoryConfig
from vibe.core.memory.models import FieldMeta, Observation, Seed, UserField
from vibe.core.memory.reflection import ReflectionEngine
from vibe.core.memory.storage import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    db = tmp_path / "test_memory.db"
    s = MemoryStore(db)
    yield s
    s.close()


def _make_engine(store: MemoryStore, response: str = "{}", should_raise: bool = False) -> ReflectionEngine:
    async def mock_llm(system: str, user: str) -> str:
        if should_raise:
            raise RuntimeError("LLM failed")
        return response

    return ReflectionEngine(mock_llm, store)


def _seed_observations(store: MemoryStore, user_id: str, count: int = 5, importance: int = 8) -> None:
    """Add observations and accumulate importance above threshold."""
    for i in range(count):
        store.add_observation(
            Observation(
                user_id=user_id,
                context_key="project:test",
                content=f"observation {i}",
                importance=importance,
                source_role="user",
            )
        )
    state = store.get_or_create_user_state(user_id)
    state.accumulated_importance = count * importance
    store.update_user_state(state)


@pytest.mark.asyncio
async def test_no_reflection_below_threshold(store: MemoryStore) -> None:
    state = store.get_or_create_user_state("user1")
    state.accumulated_importance = 50.0  # below default 150
    store.update_user_state(state)

    engine = _make_engine(store)
    config = MemoryConfig(enabled=True, reflection_trigger=150)
    result = await engine.maybe_reflect("user1", config)
    assert result is False


@pytest.mark.asyncio
async def test_no_reflection_without_observations(store: MemoryStore) -> None:
    state = store.get_or_create_user_state("user1")
    state.accumulated_importance = 200.0  # above threshold
    store.update_user_state(state)

    engine = _make_engine(store)
    config = MemoryConfig(enabled=True, reflection_trigger=150)
    result = await engine.maybe_reflect("user1", config)
    assert result is False


@pytest.mark.asyncio
async def test_reflection_triggers_and_resets(store: MemoryStore) -> None:
    store.get_or_create_user_state("user1")
    _seed_observations(store, "user1", count=20, importance=10)

    response = json.dumps({
        "seed_updates": {"affect": "focused"},
        "field_updates": [{"action": "add", "key": "lang", "value": "Python"}],
    })
    engine = _make_engine(store, response=response)
    config = MemoryConfig(enabled=True, reflection_trigger=150)

    result = await engine.maybe_reflect("user1", config)
    assert result is True

    # Accumulated importance should be reset
    state = store.get_or_create_user_state("user1")
    assert state.accumulated_importance == 0.0


@pytest.mark.asyncio
async def test_seed_updates_applied(store: MemoryStore) -> None:
    store.get_or_create_user_state("user1")
    _seed_observations(store, "user1", count=20, importance=10)

    response = json.dumps({
        "seed_updates": {"affect": "collaborative", "user_model": "senior dev"},
        "field_updates": [],
    })
    engine = _make_engine(store, response=response)
    config = MemoryConfig(enabled=True, reflection_trigger=150)

    await engine.maybe_reflect("user1", config)

    state = store.get_or_create_user_state("user1")
    assert state.seed.affect == "collaborative"
    assert state.seed.user_model == "senior dev"


@pytest.mark.asyncio
async def test_field_add(store: MemoryStore) -> None:
    store.get_or_create_user_state("user1")
    _seed_observations(store, "user1", count=20, importance=10)

    response = json.dumps({
        "seed_updates": {},
        "field_updates": [
            {"action": "add", "key": "editor", "value": "Neovim"},
            {"action": "add", "key": "os", "value": "Linux"},
        ],
    })
    engine = _make_engine(store, response=response)
    config = MemoryConfig(enabled=True, reflection_trigger=150)

    await engine.maybe_reflect("user1", config)

    state = store.get_or_create_user_state("user1")
    keys = {f.key for f in state.fields}
    assert "editor" in keys
    assert "os" in keys


@pytest.mark.asyncio
async def test_field_update(store: MemoryStore) -> None:
    state = store.get_or_create_user_state("user1")
    state.fields = [UserField(key="lang", value="JavaScript")]
    store.update_user_state(state)
    _seed_observations(store, "user1", count=20, importance=10)

    response = json.dumps({
        "seed_updates": {},
        "field_updates": [{"action": "update", "key": "lang", "value": "TypeScript"}],
    })
    engine = _make_engine(store, response=response)
    config = MemoryConfig(enabled=True, reflection_trigger=150)

    await engine.maybe_reflect("user1", config)

    state = store.get_or_create_user_state("user1")
    lang_field = next(f for f in state.fields if f.key == "lang")
    assert lang_field.value == "TypeScript"
    # Update should reinforce (increase access_count)
    assert lang_field.meta.access_count == 1


@pytest.mark.asyncio
async def test_field_remove(store: MemoryStore) -> None:
    state = store.get_or_create_user_state("user1")
    state.fields = [
        UserField(key="lang", value="Python"),
        UserField(key="stale", value="old info"),
    ]
    store.update_user_state(state)
    _seed_observations(store, "user1", count=20, importance=10)

    response = json.dumps({
        "seed_updates": {},
        "field_updates": [{"action": "remove", "key": "stale"}],
    })
    engine = _make_engine(store, response=response)
    config = MemoryConfig(enabled=True, reflection_trigger=150)

    await engine.maybe_reflect("user1", config)

    state = store.get_or_create_user_state("user1")
    keys = {f.key for f in state.fields}
    assert "lang" in keys
    assert "stale" not in keys


@pytest.mark.asyncio
async def test_field_cap_enforced(store: MemoryStore) -> None:
    state = store.get_or_create_user_state("user1")
    # Pre-populate with max_user_fields fields
    state.fields = [
        UserField(key=f"field_{i}", value=f"val_{i}", meta=FieldMeta(strength=float(i + 1)))
        for i in range(5)
    ]
    store.update_user_state(state)
    _seed_observations(store, "user1", count=20, importance=10)

    # LLM adds 3 more fields, total = 8, cap = 5
    response = json.dumps({
        "seed_updates": {},
        "field_updates": [
            {"action": "add", "key": "new_1", "value": "v1"},
            {"action": "add", "key": "new_2", "value": "v2"},
            {"action": "add", "key": "new_3", "value": "v3"},
        ],
    })
    engine = _make_engine(store, response=response)
    config = MemoryConfig(enabled=True, reflection_trigger=150, max_user_fields=5)

    await engine.maybe_reflect("user1", config)

    state = store.get_or_create_user_state("user1")
    assert len(state.fields) <= 5


@pytest.mark.asyncio
async def test_observations_cleared_after_reflection(store: MemoryStore) -> None:
    store.get_or_create_user_state("user1")
    _seed_observations(store, "user1", count=5, importance=40)

    engine = _make_engine(store, response=json.dumps({"seed_updates": {}, "field_updates": []}))
    config = MemoryConfig(enabled=True, reflection_trigger=150)

    await engine.maybe_reflect("user1", config)

    obs = store.get_pending_observations("user1")
    assert len(obs) == 0


@pytest.mark.asyncio
async def test_reflection_only_clears_processed_observations(store: MemoryStore) -> None:
    store.get_or_create_user_state("user1")
    _seed_observations(store, "user1", count=25, importance=10)

    engine = _make_engine(store, response=json.dumps({"seed_updates": {}, "field_updates": []}))
    config = MemoryConfig(enabled=True, reflection_trigger=150)

    await engine.maybe_reflect("user1", config)

    # Reflection processes top 20 observations; remaining 5 should still be pending.
    obs = store.get_pending_observations("user1", limit=100)
    assert len(obs) == 5


@pytest.mark.asyncio
async def test_llm_failure_returns_false(store: MemoryStore) -> None:
    store.get_or_create_user_state("user1")
    _seed_observations(store, "user1", count=20, importance=10)

    engine = _make_engine(store, should_raise=True)
    config = MemoryConfig(enabled=True, reflection_trigger=150)

    result = await engine.maybe_reflect("user1", config)
    assert result is False

    # State should not be modified
    state = store.get_or_create_user_state("user1")
    assert state.accumulated_importance == 200.0  # 20 * 10


@pytest.mark.asyncio
async def test_garbage_llm_response_returns_true_with_no_changes(store: MemoryStore) -> None:
    store.get_or_create_user_state("user1")
    _seed_observations(store, "user1", count=20, importance=10)

    engine = _make_engine(store, response="not valid json at all")
    config = MemoryConfig(enabled=True, reflection_trigger=150)

    result = await engine.maybe_reflect("user1", config)
    assert result is True  # empty dict parsed, apply succeeds with no changes

    state = store.get_or_create_user_state("user1")
    assert state.accumulated_importance == 0.0  # reset even with empty result


@pytest.mark.asyncio
async def test_audit_log_written(store: MemoryStore) -> None:
    store.get_or_create_user_state("user1")
    _seed_observations(store, "user1", count=20, importance=10)

    response = json.dumps({"seed_updates": {"affect": "calm"}, "field_updates": []})
    engine = _make_engine(store, response=response)
    config = MemoryConfig(enabled=True, reflection_trigger=150)

    await engine.maybe_reflect("user1", config)

    rows = store._conn.execute("SELECT * FROM reflections").fetchall()
    assert len(rows) == 1
    assert rows[0]["user_id"] == "user1"


def test_parse_response_plain_json() -> None:
    raw = '{"seed_updates": {}, "field_updates": []}'
    assert ReflectionEngine._parse_response(raw) == {"seed_updates": {}, "field_updates": []}


def test_parse_response_markdown_fenced() -> None:
    raw = '```json\n{"seed_updates": {"affect": "calm"}, "field_updates": []}\n```'
    result = ReflectionEngine._parse_response(raw)
    assert result["seed_updates"]["affect"] == "calm"


def test_parse_response_garbage() -> None:
    assert ReflectionEngine._parse_response("not json") == {}


def test_parse_response_non_dict() -> None:
    assert ReflectionEngine._parse_response("[1, 2, 3]") == {}


def test_format_state_empty() -> None:
    from vibe.core.memory.models import UserState
    state = UserState(user_id="test")
    assert ReflectionEngine._format_state(state) == "(empty state)"


def test_format_state_with_data() -> None:
    from vibe.core.memory.models import UserState
    state = UserState(
        user_id="test",
        seed=Seed(affect="focused", user_model="backend dev"),
        fields=[UserField(key="lang", value="Python")],
    )
    result = ReflectionEngine._format_state(state)
    assert "focused" in result
    assert "backend dev" in result
    assert "lang: Python" in result
