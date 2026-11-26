# ruff: noqa: I002 -- new modules here do without postponed annotations.
"""Where the Runtime records the facts a Session API view needs.

A Session the Session API serves records them in a ``SessionApiView``. Any
other Session gets ``NoFacts``, which records nothing, so its Session file
keeps no ``session_api`` field. This module imports no Session API models: a
Runtime that never serves the Session API does not load them.
"""

from typing import Protocol

from mistralai_vibe_local_harness.session_protocol import JsonObject, PublicSessionState
from mistralai_vibe_local_harness.vibe._storage import SessionApiFactsV1


class FactRecorder(Protocol):
    def observe(self, state: PublicSessionState) -> None: ...

    def record_tool_call(self, entry_id: str, call: JsonObject) -> None: ...

    def all_facts(self) -> SessionApiFactsV1 | None: ...

    def unsaved_facts(self) -> SessionApiFactsV1 | None: ...

    def mark_saved(self, facts: SessionApiFactsV1 | None) -> None: ...


class NoFacts:
    """Records nothing; used when the Session API does not serve the Session."""

    def observe(self, state: PublicSessionState) -> None:
        pass

    def record_tool_call(self, entry_id: str, call: JsonObject) -> None:
        pass

    def all_facts(self) -> None:
        return None

    def unsaved_facts(self) -> None:
        return None

    def mark_saved(self, facts: SessionApiFactsV1 | None) -> None:
        pass


NO_FACTS = NoFacts()


def fact_recorder(*, enabled: bool, saved: SessionApiFactsV1 | None) -> FactRecorder:
    """A Session API view restored from ``saved`` when ``enabled``, else ``NO_FACTS``.

    The Session API modules load here, on first use, so a Session the Session
    API does not serve never loads them.
    """
    if not enabled:
        return NO_FACTS
    from mistralai_vibe_local_harness.vibe._session_api_state import SessionApiView

    return SessionApiView.from_facts(saved)


__all__ = ["NO_FACTS", "FactRecorder", "NoFacts", "fact_recorder"]
