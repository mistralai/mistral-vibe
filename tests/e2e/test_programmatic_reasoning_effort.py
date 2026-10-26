"""The reasoning effort a user picks reaches the provider as picked.

A model can change under its name before Vibe's known-model table catches up: a
newer Mistral Large 4 takes low, high and max where the table's row stops at high.
A config that lists the new levels is followed. A level the config sets is sent
as set, listed or not, and the provider says whether the model takes it.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any

import pytest

from tests.e2e.agent_loop_characterization.support import assistant_text_chunks
from tests.e2e.common import read_export, run_vibe_headless, write_e2e_config
from tests.e2e.mock_server import ChatCompletionsRequestPayload, StreamingMockServer

pytestmark = [pytest.mark.timeout(90), pytest.mark.usefixtures("setup_e2e_env")]

# A name the known-model table matches to its Mistral Large 4 row, whose levels
# stop at high, served by a deployment that takes low, high and max.
_MODEL = "mistral-large-4"
_TAKEN = ("low", "high", "max")


def _reply(
    _request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    return assistant_text_chunks("All done.")


def _effort(payload: ChatCompletionsRequestPayload) -> object:
    return dict[str, Any](payload).get("reasoning_effort")


def _status(_request_index: int, payload: ChatCompletionsRequestPayload) -> int:
    return 200 if _effort(payload) in _TAKEN else 400


def _refusal(_request_index: int, payload: ChatCompletionsRequestPayload) -> str:
    return (
        f"reasoning_effort {_effort(payload)} is not supported for this model, "
        f"supported values: [{', '.join(_TAKEN)}]"
    )


def _run(
    tmp_path: Path, workdir: Path, thinking: str, *, lists_levels: bool
) -> tuple[subprocess.CompletedProcess[str], StreamingMockServer, Path]:
    server = StreamingMockServer(
        chunk_factory=_reply, status_factory=_status, error_message_factory=_refusal
    )
    server.start()
    levels = '", "'.join(_TAKEN)
    vibe_home = Path(os.environ["VIBE_HOME"])
    write_e2e_config(
        vibe_home,
        server.api_base,
        backend="mistral",
        model_settings=[
            f'thinking = "{thinking}"',
            *([f'thinking_levels = ["{levels}"]'] if lists_levels else []),
        ],
    )
    # The helper names the model `mock-model`; give it the Large 4 name instead.
    config = vibe_home / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'name = "mock-model"', f'name = "{_MODEL}"'
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / f"out-{thinking}"
    try:
        result = run_vibe_headless(
            workdir, ["-p", "hello", "--output-dir", str(output_dir)]
        )
    finally:
        server.stop()
    return result, server, output_dir


@pytest.mark.parametrize("thinking", _TAKEN)
def test_each_listed_effort_reaches_the_provider_as_picked(
    thinking: str, e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: A Mistral Large 4 deployment that takes low, high and max, and a
    config that lists those levels and picks one.
    *Do*: Run `vibe -p`.
    *Assert*: The run finishes, and the request carries the picked effort, not
    the high the known-model table would cap it at.
    """
    # Do
    result, server, output_dir = _run(
        tmp_path, e2e_workdir, thinking, lists_levels=True
    )

    # Assert
    assert result.returncode == 0, result.stderr
    assert read_export(output_dir).outcome == "finished"
    assert [_effort(request) for request in server.requests] == [thinking]


@pytest.mark.parametrize("lists_levels", [False, True])
def test_an_effort_the_model_does_not_take_fails_with_the_providers_error(
    lists_levels: bool, e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: The same deployment, with a config that picks medium, which
    the deployment does not take, and lists no levels or low, high and max.
    *Do*: Run `vibe -p`.
    *Assert*: Every request carries medium, unchanged, and the run fails with the
    provider's own refusal, which names the efforts the model takes.
    """
    # Do
    result, server, output_dir = _run(
        tmp_path, e2e_workdir, "medium", lists_levels=lists_levels
    )

    # Assert
    assert result.returncode != 0
    assert server.requests
    assert {_effort(request) for request in server.requests} == {"medium"}
    export = read_export(output_dir)
    assert export.outcome != "finished"
    assert export.error is not None
    assert "reasoning_effort medium is not supported" in export.error.message
    assert "supported values: [low, high, max]" in export.error.message
