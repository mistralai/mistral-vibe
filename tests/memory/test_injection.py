from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.memory.injection import MemoryInjector
from vibe.core.memory.models import Seed, UserField
from vibe.core.memory.storage import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    db = tmp_path / "test_memory.db"
    s = MemoryStore(db)
    yield s
    s.close()


@pytest.fixture
def injector(store: MemoryStore) -> MemoryInjector:
    return MemoryInjector(store)


def test_empty_state_returns_empty(injector: MemoryInjector, store: MemoryStore) -> None:
    store.get_or_create_user_state("user1")
    store.get_or_create_context_memory("project:test", "user1")
    result = injector.build_memory_block("user1", "project:test")
    assert result == ""


def test_seed_injection(injector: MemoryInjector, store: MemoryStore) -> None:
    state = store.get_or_create_user_state("user1")
    state.seed = Seed(affect="collaborative", user_model="senior backend dev")
    store.update_user_state(state)
    store.get_or_create_context_memory("project:test", "user1")

    result = injector.build_memory_block("user1", "project:test")
    assert "<memory>" in result
    assert "<seed>" in result
    assert "collaborative" in result
    assert "senior backend dev" in result


def test_fields_injection(injector: MemoryInjector, store: MemoryStore) -> None:
    state = store.get_or_create_user_state("user1")
    state.fields = [
        UserField(key="lang", value="Python"),
        UserField(key="editor", value="Neovim"),
    ]
    store.update_user_state(state)
    store.get_or_create_context_memory("project:test", "user1")

    result = injector.build_memory_block("user1", "project:test")
    assert "<fields>" in result
    assert "lang: Python" in result
    assert "editor: Neovim" in result


def test_context_memory_injection(injector: MemoryInjector, store: MemoryStore) -> None:
    store.get_or_create_user_state("user1")
    ctx = store.get_or_create_context_memory("project:test", "user1")
    ctx.short_term = ["Uses FastAPI", "Prefers PostgreSQL"]
    ctx.long_term = "This is a Python web project using async patterns."
    store.update_context_memory(ctx)

    result = injector.build_memory_block("user1", "project:test")
    assert "<active>" in result
    assert "Uses FastAPI" in result
    assert "<knowledge>" in result
    assert "async patterns" in result


def test_full_memory_block_format(injector: MemoryInjector, store: MemoryStore) -> None:
    state = store.get_or_create_user_state("user1")
    state.seed = Seed(affect="focused")
    state.fields = [UserField(key="role", value="SRE")]
    store.update_user_state(state)

    ctx = store.get_or_create_context_memory("project:test", "user1")
    ctx.short_term = ["Kubernetes cluster"]
    ctx.long_term = "Infrastructure project."
    store.update_context_memory(ctx)

    result = injector.build_memory_block("user1", "project:test")
    assert result.startswith("<memory>")
    assert result.endswith("</memory>")
    # Flat structure — no wrapper tags
    assert "<user>" not in result
    assert "<context" not in result
    assert "<seed>" in result
    assert "<fields>" in result
    assert "<active>" in result
    assert "<knowledge>" in result


def test_budget_truncation(injector: MemoryInjector, store: MemoryStore) -> None:
    store.get_or_create_user_state("user1")
    ctx = store.get_or_create_context_memory("project:test", "user1")
    ctx.long_term = "x" * 10000
    ctx.short_term = ["important point"]
    store.update_context_memory(ctx)

    # Very tight budget — 50 tokens = 200 chars
    result = injector.build_memory_block("user1", "project:test", budget_tokens=50)
    assert len(result) < 500  # should be truncated


def test_seed_respects_budget(injector: MemoryInjector, store: MemoryStore) -> None:
    state = store.get_or_create_user_state("user1")
    state.seed = Seed(user_model="x" * 5000)
    store.update_user_state(state)
    store.get_or_create_context_memory("project:test", "user1")

    result = injector.build_memory_block("user1", "project:test", budget_tokens=30)
    assert result
    assert len(result) <= 150


def test_escapes_xml_content(injector: MemoryInjector, store: MemoryStore) -> None:
    state = store.get_or_create_user_state("user1")
    state.seed = Seed(user_model="uses <FastAPI> & SQLAlchemy")
    state.fields = [UserField(key="pref", value="likes <xml> & tags")]
    store.update_user_state(state)
    store.add_sensory("project:test", "user1", "recent <unsafe> & content")

    result = injector.build_memory_block("user1", "project:test")
    assert "&lt;FastAPI&gt;" in result
    assert "&amp;" in result
    assert "<FastAPI>" not in result
    assert "<unsafe>" not in result


# -- Sensory injection tests --


def test_sensory_injection_when_no_other_data(injector: MemoryInjector, store: MemoryStore) -> None:
    """Sensory data should be injected even when seed/fields/short_term/long_term are empty."""
    store.get_or_create_user_state("user1")
    store.add_sensory("project:test", "user1", "I prefer Python for backend work")

    result = injector.build_memory_block("user1", "project:test")
    assert "<memory>" in result
    assert "<recent>" in result
    assert "I prefer Python for backend work" in result


def test_sensory_limited_to_last_10(injector: MemoryInjector, store: MemoryStore) -> None:
    store.get_or_create_user_state("user1")
    for i in range(20):
        store.add_sensory("project:test", "user1", f"message {i}")

    result = injector.build_memory_block("user1", "project:test")
    assert "message 10" in result
    assert "message 19" in result
    assert "message 0" not in result
    assert "message 9" not in result


def test_sensory_empty_returns_empty(store: MemoryStore) -> None:
    from vibe.core.memory.injection import MemoryInjector

    inj = MemoryInjector(store)
    assert inj._format_sensory([]) == ""


def test_sensory_format(store: MemoryStore) -> None:
    from vibe.core.memory.injection import MemoryInjector

    inj = MemoryInjector(store)
    result = inj._format_sensory(["hello", "world"], limit=10)
    assert result == "- hello\n- world"


def test_sensory_with_other_sections(injector: MemoryInjector, store: MemoryStore) -> None:
    """Sensory appears alongside other memory sections."""
    state = store.get_or_create_user_state("user1")
    state.seed = Seed(user_model="developer")
    store.update_user_state(state)
    store.add_sensory("project:test", "user1", "recent observation")

    result = injector.build_memory_block("user1", "project:test")
    assert "<seed>" in result
    assert "<recent>" in result
    assert "developer" in result
    assert "recent observation" in result
