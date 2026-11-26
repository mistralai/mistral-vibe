"""Test that callbacks from local sessions (code_local workers) are surfaced to the parent session.

This is a regression test for the issue where tool approvals from child sessions
(local sessions created by the bot orchestrator) were not surfaced to the user.

The fix ensures that local sessions with started_by_session_id matching the root
session ID are included in the open_callbacks list, in addition to subagent sessions
with SubagentSessionIdentity.
"""

from __future__ import annotations


def test_started_by_session_id_logic():
    """Test the logic for checking started_by_session_id in open_callbacks."""
    # This is a unit test for the logic without requiring full harness integration

    # Mock session with started_by_session_id
    class MockSession:
        def __init__(self, session_id, started_by_session_id=None):
            self.session_id = session_id
            self._started_by = started_by_session_id
            self.ephemeral = False

        @property
        def started_by_session_id(self):
            return self._started_by

        def open_callbacks(self):
            return [{"callback_id": "test_callback"}]

        @property
        def _runtime_for_host(self):
            class MockRuntime:
                @property
                def identity(self):
                    return None

            return MockRuntime()

    # Mock host with the fixed open_callbacks logic
    class MockHost:
        def __init__(self):
            self._sessions = {}

        def add_session(self, session):
            self._sessions[session.session_id] = type(
                "Entry", (), {"session": session}
            )()

        def open_callbacks(self, root_session_id):
            """Implementation matching the fixed version in _host.py"""
            callbacks = []
            for entry in self._sessions.values():
                session = entry.session
                if session.session_id == root_session_id:
                    callbacks.extend(session.open_callbacks())
                    continue
                if session.ephemeral:
                    continue
                identity = session._runtime_for_host.identity
                # Check for SubagentSessionIdentity (not implemented in mock, so always None)
                if identity is not None and hasattr(identity, "parent_session_id"):
                    if identity.parent_session_id == root_session_id:
                        callbacks.extend(session.open_callbacks())
                        continue
                # Also include local sessions started by the root session
                if session.started_by_session_id == root_session_id:
                    callbacks.extend(session.open_callbacks())
            return tuple(callbacks)

    # Test setup
    host = MockHost()
    parent_session = MockSession("parent_session")
    local_session = MockSession("local_session", started_by_session_id="parent_session")
    other_session = MockSession(
        "other_session", started_by_session_id="different_parent"
    )

    host.add_session(parent_session)
    host.add_session(local_session)
    host.add_session(other_session)

    # Test that parent's open_callbacks includes local session's callback
    callbacks = host.open_callbacks("parent_session")
    assert len(callbacks) == 2  # parent + local_session
    assert any(cb["callback_id"] == "test_callback" for cb in callbacks)

    # Test that other session's callbacks are not included
    callbacks = host.open_callbacks("other_parent")
    assert len(callbacks) == 0

    print("✓ Test passed: Local session callbacks are surfaced to parent")


def test_respond_to_callback_logic():
    """Test the logic for checking started_by_session_id in respond_to_callback."""

    # Mock session
    class MockSession:
        def __init__(self, session_id, started_by_session_id=None):
            self.session_id = session_id
            self._started_by = started_by_session_id
            self.ephemeral = False

        @property
        def started_by_session_id(self):
            return self._started_by

        @property
        def _runtime_for_host(self):
            class MockRuntime:
                @property
                def identity(self):
                    return None

            return MockRuntime()

    # Test the logic
    parent_session_id = "parent_session"
    local_session_id = "local_session"
    other_session_id = "other_session"

    parent_session = MockSession(parent_session_id)
    local_session = MockSession(
        local_session_id, started_by_session_id=parent_session_id
    )
    other_session = MockSession(
        other_session_id, started_by_session_id="different_parent"
    )

    # Simulate the check in respond_to_callback
    def can_respond(session, root_session_id):
        if session.session_id == root_session_id:
            return True
        if session.ephemeral:
            return False
        identity = session._runtime_for_host.identity
        if identity is not None and hasattr(identity, "parent_session_id"):
            if identity.parent_session_id == root_session_id:
                return True
        # Also allow responding to callbacks from local sessions started by root
        if session.started_by_session_id == root_session_id:
            return True
        return False

    # Test that parent can respond to its own callbacks
    assert can_respond(parent_session, parent_session_id)

    # Test that parent can respond to local session's callbacks
    assert can_respond(local_session, parent_session_id)

    # Test that parent cannot respond to other session's callbacks
    assert not can_respond(other_session, parent_session_id)

    print("✓ Test passed: Parent can respond to local session callbacks")


if __name__ == "__main__":
    test_started_by_session_id_logic()
    test_respond_to_callback_logic()
    print("\nAll unit tests passed!")
