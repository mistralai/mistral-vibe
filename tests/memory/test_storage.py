from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.memory.models import Observation, Seed, UserField, FieldMeta, UserState
from vibe.core.memory.storage import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    db = tmp_path / "test_memory.db"
    s = MemoryStore(db)
    yield s
    s.close()


def test_schema_creation(store: MemoryStore) -> None:
    tables = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {row["name"] for row in tables}
    assert "user_states" in names
    assert "context_memories" in names
    assert "reflections" in names
    assert "consolidations" in names
    assert "schema_version" in names


def test_wal_mode(store: MemoryStore) -> None:
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()
    assert mode[0] == "wal"


def test_get_or_create_user_state(store: MemoryStore) -> None:
    state = store.get_or_create_user_state("user1")
    assert state.user_id == "user1"
    assert state.accumulated_importance == 0.0

    state2 = store.get_or_create_user_state("user1")
    assert state2.user_id == state.user_id


def test_update_user_state(store: MemoryStore) -> None:
    state = store.get_or_create_user_state("user1")
    state.accumulated_importance = 42.5
    state.seed = Seed(affect="positive", user_model="senior dev")
    store.update_user_state(state)

    loaded = store.get_or_create_user_state("user1")
    assert loaded.accumulated_importance == 42.5
    assert loaded.seed.affect == "positive"
    assert loaded.seed.user_model == "senior dev"


def test_update_user_state_with_fields(store: MemoryStore) -> None:
    state = store.get_or_create_user_state("user1")
    state.fields = [
        UserField(key="role", value="Staff Engineer"),
        UserField(key="lang", value="Python"),
    ]
    store.update_user_state(state)

    loaded = store.get_or_create_user_state("user1")
    assert len(loaded.fields) == 2
    assert loaded.fields[0].key == "role"
    assert loaded.fields[1].value == "Python"


def test_add_observation(store: MemoryStore) -> None:
    store.get_or_create_user_state("user1")
    count = store.add_observation(
        Observation(user_id="user1", context_key="project:test", content="test msg", importance=5, source_role="user")
    )
    assert count == 1

    count2 = store.add_observation(
        Observation(user_id="user1", context_key="project:test", content="test msg 2", importance=8, source_role="user")
    )
    assert count2 == 2


def test_get_pending_observations(store: MemoryStore) -> None:
    store.get_or_create_user_state("user1")
    for i in range(5):
        store.add_observation(
            Observation(user_id="user1", context_key="ctx", content=f"msg{i}", importance=i + 1, source_role="user")
        )

    obs = store.get_pending_observations("user1", limit=3)
    assert len(obs) == 3
    assert obs[0].importance >= obs[1].importance >= obs[2].importance


def test_get_or_create_context_memory(store: MemoryStore) -> None:
    ctx = store.get_or_create_context_memory("project:foo", "user1")
    assert ctx.context_key == "project:foo"
    assert ctx.sensory == []
    assert ctx.long_term == ""

    ctx2 = store.get_or_create_context_memory("project:foo", "user1")
    assert ctx2.context_key == ctx.context_key


def test_update_context_memory(store: MemoryStore) -> None:
    ctx = store.get_or_create_context_memory("project:foo", "user1")
    ctx.short_term = ["point 1", "point 2"]
    ctx.long_term = "This is a summary."
    assert store.update_context_memory(ctx) is True

    loaded = store.get_or_create_context_memory("project:foo", "user1")
    assert loaded.short_term == ["point 1", "point 2"]
    assert loaded.long_term == "This is a summary."
    assert loaded.version == 1


def test_add_sensory_fifo(store: MemoryStore) -> None:
    store.get_or_create_context_memory("project:test", "user1")
    for i in range(55):
        store.add_sensory("project:test", "user1", f"obs_{i}", cap=50)

    ctx = store.get_or_create_context_memory("project:test", "user1")
    assert len(ctx.sensory) == 50
    assert ctx.sensory[0] == "obs_5"
    assert ctx.sensory[-1] == "obs_54"


def test_optimistic_locking(store: MemoryStore) -> None:
    ctx = store.get_or_create_context_memory("project:lock", "user1")
    assert ctx.version == 0

    assert store.update_context_memory(ctx) is True
    loaded = store.get_or_create_context_memory("project:lock", "user1")
    assert loaded.version == 1

    # Stale version should fail and report it.
    ctx.long_term = "stale update"
    assert store.update_context_memory(ctx) is False  # version 0 != current 1
    final = store.get_or_create_context_memory("project:lock", "user1")
    assert final.long_term == ""  # stale update was not applied


def test_add_sensory_bumps_version(store: MemoryStore) -> None:
    ctx = store.get_or_create_context_memory("project:sensory", "user1")
    assert ctx.version == 0

    assert store.add_sensory("project:sensory", "user1", "hello world") is True
    loaded = store.get_or_create_context_memory("project:sensory", "user1")
    assert loaded.version == 1


def test_log_reflection(store: MemoryStore) -> None:
    store.log_reflection("user1", '{"obs": [1,2]}', '{"updates": {}}')
    rows = store._conn.execute("SELECT * FROM reflections").fetchall()
    assert len(rows) == 1
    assert rows[0]["user_id"] == "user1"


def test_clear_observations(store: MemoryStore) -> None:
    store.get_or_create_user_state("user1")
    for i in range(3):
        store.add_observation(
            Observation(user_id="user1", context_key="ctx", content=f"msg{i}", importance=5, source_role="user")
        )
    assert len(store.get_pending_observations("user1")) == 3

    store.clear_observations("user1")
    assert len(store.get_pending_observations("user1")) == 0


def test_log_consolidation(store: MemoryStore) -> None:
    store.log_consolidation("project:x", "user1", 10, "old", "new")
    rows = store._conn.execute("SELECT * FROM consolidations").fetchall()
    assert len(rows) == 1
    assert rows[0]["input_sensory_count"] == 10


# -- Compression storage-layer tests ------------------------------------------


def test_long_term_stored_with_z_prefix(store: MemoryStore) -> None:
    """Long long_term text should be stored compressed with z: prefix."""
    ctx = store.get_or_create_context_memory("project:z", "user1")
    ctx.long_term = "x" * 300 + " some varied content to trigger compression " * 5
    store.update_context_memory(ctx)

    raw = store._conn.execute(
        "SELECT long_term FROM context_memories WHERE context_key='project:z' AND user_id='user1'"
    ).fetchone()
    assert raw["long_term"].startswith("z:")


def test_long_term_short_stored_raw(store: MemoryStore) -> None:
    """Short long_term text should be stored without compression."""
    ctx = store.get_or_create_context_memory("project:short", "user1")
    ctx.long_term = "A brief summary."
    store.update_context_memory(ctx)

    raw = store._conn.execute(
        "SELECT long_term FROM context_memories WHERE context_key='project:short' AND user_id='user1'"
    ).fetchone()
    assert not raw["long_term"].startswith("z:")
    assert raw["long_term"] == "A brief summary."


def test_long_term_legacy_raw_readable(store: MemoryStore) -> None:
    """Raw text inserted directly (legacy) should read back correctly."""
    store.get_or_create_context_memory("project:legacy", "user1")
    legacy_text = "This is old uncompressed data from before compression was added."
    store._conn.execute(
        "UPDATE context_memories SET long_term=? WHERE context_key='project:legacy' AND user_id='user1'",
        (legacy_text,),
    )
    store._conn.commit()

    loaded = store.get_or_create_context_memory("project:legacy", "user1")
    assert loaded.long_term == legacy_text


def test_json_columns_compact(store: MemoryStore) -> None:
    """JSON columns should use compact separators (no spaces)."""
    state = store.get_or_create_user_state("user1")
    state.fields = [
        UserField(key="role", value="Staff Engineer"),
        UserField(key="lang", value="Python"),
    ]
    store.update_user_state(state)

    raw = store._conn.execute(
        "SELECT fields_json FROM user_states WHERE user_id='user1'"
    ).fetchone()
    fields_str = raw["fields_json"]
    # Compact JSON should not contain ": " or ", " (uses ":" and ",")
    assert ": " not in fields_str
    assert ", " not in fields_str
