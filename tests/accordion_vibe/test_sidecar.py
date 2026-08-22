"""Transport level: one real subprocess, two pipes, and every way it can fail."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import sys

import pytest

from accordion_vibe._config import child_environment
from accordion_vibe._sidecar import SidecarClient
from tests.accordion_vibe.conftest import HANDSHAKE_TIMEOUT_S, Sidecar, wait_until

_HELLO = {"harness": "vibe", "harnessVersion": "test", "sessionId": "s-1", "cwd": "."}


def _started(
    make_client: Callable[..., SidecarClient], sidecar: Sidecar
) -> SidecarClient:
    client = make_client(sidecar)
    assert client.start(_HELLO, timeout=HANDSHAKE_TIMEOUT_S)
    return client


def test_handshake_succeeds_and_publishes_the_ready_payload(
    make_sidecar: Callable[..., Sidecar], make_client: Callable[..., SidecarClient]
):
    sidecar = make_sidecar()
    client = _started(make_client, sidecar)

    assert client.alive
    assert client.detached_reason is None
    ready = client.ready_payload
    assert ready["protocolVersion"] == 22
    assert [t["name"] for t in ready["tools"]] == ["unfold", "recall"]
    assert [c["name"] for c in ready["commands"]] == ["accordion"]
    assert sidecar.received()[0]["type"] == "hello"
    assert sidecar.received()[0]["v"] == 1


def test_request_round_trips_through_the_pipes(
    make_sidecar: Callable[..., Sidecar], make_client: Callable[..., SidecarClient]
):
    sidecar = make_sidecar()
    client = _started(make_client, sidecar)

    reply = client.request(
        {"type": "tool", "name": "unfold", "args": {"codes": ["a1f"]}}, timeout=5.0
    )

    assert reply is not None
    assert reply["type"] == "tool_result"
    assert reply["isError"] is False
    assert '"codes": ["a1f"]' in reply["content"]
    assert client.timeouts == 0
    sent = sidecar.received_of("tool")[0]
    assert sent["req"] == reply["req"]


def test_a_sidecar_that_never_sends_ready_detaches_without_raising(
    make_sidecar: Callable[..., Sidecar], make_client: Callable[..., SidecarClient]
):
    client = make_client(make_sidecar(ready=False))

    assert client.start(_HELLO, timeout=1.0) is False
    assert client.detached_reason == "no ready message within timeout"
    assert not client.alive
    # Later traffic is a silent no-op rather than an exception.
    client.send({"type": "agent_start"})
    assert client.request({"type": "context"}, timeout=0.25) is None


def test_request_times_out_on_a_hanging_sidecar(
    make_sidecar: Callable[..., Sidecar], make_client: Callable[..., SidecarClient]
):
    sidecar = make_sidecar(context="hang")
    client = _started(make_client, sidecar)

    assert client.request({"type": "context", "messages": []}, timeout=0.25) is None

    assert client.timeouts == 1
    assert client.alive  # a timeout is not a death
    assert sidecar.received_of("context")


def test_a_crash_mid_session_degrades_to_no_sidecar(
    make_sidecar: Callable[..., Sidecar], make_client: Callable[..., SidecarClient]
):
    client = _started(make_client, make_sidecar(crash_on="context"))

    assert client.request({"type": "context", "messages": []}, timeout=5.0) is None

    assert wait_until(lambda: not client.alive)
    assert client.detached_reason == "sidecar stdout closed"
    client.send({"type": "agent_start"})  # must not raise
    assert client.request({"type": "tool", "name": "unfold"}, timeout=0.25) is None


def test_close_reaps_the_process(
    make_sidecar: Callable[..., Sidecar], make_client: Callable[..., SidecarClient]
):
    client = _started(make_client, make_sidecar())
    process = client._process
    assert process is not None and process.poll() is None

    client.close()

    assert process.poll() is not None
    client.close()  # idempotent


def test_close_on_a_never_started_client_is_a_no_op(
    make_sidecar: Callable[..., Sidecar], make_client: Callable[..., SidecarClient]
):
    client = make_client(make_sidecar())

    client.close()

    assert not client.alive


def test_notifications_reach_their_listeners(
    make_sidecar: Callable[..., Sidecar], make_client: Callable[..., SidecarClient]
):
    notices: list[tuple[str, str]] = []
    folding: list[bool] = []
    sidecar = make_sidecar(folding_sequence=[True], command_notices=[["hi", "warning"]])
    client = make_client(
        sidecar,
        on_notify=lambda text, level: notices.append((text, level)),
        on_folding=folding.append,
    )
    assert client.start(_HELLO, timeout=HANDSHAKE_TIMEOUT_S)

    assert wait_until(lambda: folding == [False, True])
    assert client.request({"type": "command", "name": "accordion"}, timeout=5.0)
    assert notices == [("hi", "warning")]


def test_the_sidecar_never_inherits_accordion_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """``ACCORDION_HOME`` relocates Accordion's own state directory.

    Passing it through would make the sidecar keep its registry, door secret
    and controller lease inside whatever the harness's variable happens to
    name -- historically the checkout the bridge was pointed at.
    """
    monkeypatch.setenv("ACCORDION_HOME", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("ACCORDION_REPO", str(tmp_path))

    env = child_environment()

    assert "ACCORDION_HOME" not in env
    assert env["ACCORDION_REPO"] == str(tmp_path)
    assert env.get("PATH") == os.environ.get("PATH"), "the rest is inherited"


def test_a_spawned_sidecar_sees_no_accordion_home(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ACCORDION_HOME", str(tmp_path / "elsewhere"))
    # print() supplies the line terminator, so the script needs no escapes.
    script = (
        "import json,os,sys;"
        "sys.stdin.buffer.readline();"
        "print(json.dumps({'type':'ready','v':1,"
        "'accordionHome':os.environ.get('ACCORDION_HOME')}),flush=True);"
        "sys.stdin.buffer.readline()"
    )
    client = SidecarClient([sys.executable, "-c", script], cwd=tmp_path)
    try:
        assert client.start({"harness": "vibe"}, timeout=10.0) is True
        assert client.ready_payload["accordionHome"] is None
    finally:
        client.close()
