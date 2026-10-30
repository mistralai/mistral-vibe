"""The reasoning effort a user picks reaches the provider as picked.

A config that lists a model's levels is followed, including a level of the model's
own that no table knows. A level the config sets is sent as set, listed or not,
and the provider says whether the model takes it. The Mistral client sends only
the levels its SDK lists; another goes out as no level, with a warning.
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

# A name the known-model table matches to its Mistral Large 4 row, served by a
# deployment that takes low, high and max.
_MODEL = "mistral-large-4"
_TAKEN = ("low", "high", "max")
# A level of the deployment's own, beside a standard one.
_OWN_LEVELS = ("low", "turbo")


def _reply(
    _request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    return assistant_text_chunks("All done.")


def _effort(payload: ChatCompletionsRequestPayload) -> object:
    return dict[str, Any](payload).get("reasoning_effort")


def _status(_request_index: int, payload: ChatCompletionsRequestPayload) -> int:
    return 200 if _effort(payload) in (None, *_TAKEN, *_OWN_LEVELS) else 400


def _refusal(_request_index: int, payload: ChatCompletionsRequestPayload) -> str:
    return (
        f"reasoning_effort {_effort(payload)} is not supported for this model, "
        f"supported values: [{', '.join(_TAKEN)}]"
    )


def _run(
    tmp_path: Path,
    workdir: Path,
    thinking: str,
    *,
    lists_levels: bool,
    levels: tuple[str, ...] = _TAKEN,
    backend: str = "mistral",
) -> tuple[subprocess.CompletedProcess[str], StreamingMockServer, Path]:
    server = StreamingMockServer(
        chunk_factory=_reply, status_factory=_status, error_message_factory=_refusal
    )
    server.start()
    listed = '", "'.join(levels)
    vibe_home = Path(os.environ["VIBE_HOME"])
    write_e2e_config(
        vibe_home,
        server.api_base,
        backend=backend,
        model_settings=[
            f'thinking = "{thinking}"',
            *([f'thinking_levels = ["{listed}"]'] if lists_levels else []),
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
    *Assert*: The run finishes, and the request carries the picked effort.
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


def test_a_level_of_the_models_own_reaches_an_openai_compatible_provider_as_written(
    e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: An OpenAI-compatible deployment that takes low and a level of its
    own, turbo, and a config that lists those levels and picks turbo.
    *Do*: Run `vibe -p`.
    *Assert*: The run finishes with no config warning, and the request carries
    turbo as written: no table knows it, and none refuses it.
    """
    # Do
    result, server, output_dir = _run(
        tmp_path,
        e2e_workdir,
        "turbo",
        lists_levels=True,
        levels=_OWN_LEVELS,
        backend="generic",
    )

    # Assert
    assert result.returncode == 0, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "finished"
    assert not [w for w in export.warnings or [] if "thinking" in w.message]
    assert [_effort(request) for request in server.requests] == ["turbo"]


def test_a_level_the_mistral_sdk_cannot_send_warns_and_goes_without_one(
    e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: A Mistral deployment with a config that lists low and turbo and
    picks turbo, which the Mistral SDK does not list.
    *Do*: Run `vibe -p`.
    *Assert*: The run finishes. The harness passes turbo on, the SDK leaves it out
    of the request, and stderr and the export carry the config warning that says
    so and how to fix it.
    """
    # Do
    result, server, output_dir = _run(
        tmp_path, e2e_workdir, "turbo", lists_levels=True, levels=_OWN_LEVELS
    )

    # Assert
    assert result.returncode == 0, result.stderr
    assert "the Mistral client cannot send thinking 'turbo'" in result.stderr
    export = read_export(output_dir)
    assert export.outcome == "finished"
    assert [_effort(request) for request in server.requests] == [None]
    assert export.warnings is not None
    assert any(
        "cannot send thinking 'turbo'" in warning.message
        and "Pick another thinking level" in warning.message
        for warning in export.warnings
    )


def test_a_level_neither_standard_nor_listed_warns_and_the_session_still_opens(
    e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: An OpenAI-compatible deployment, with a config that lists low and
    turbo and picks ultra, a word that is neither a standard level nor one it
    lists.
    *Do*: Run `vibe -p`.
    *Assert*: The session opens and sends ultra as written; stderr and the export
    carry a warning naming it and how to fix it, and the provider's refusal is the
    run's error.
    """
    # Do
    result, server, output_dir = _run(
        tmp_path,
        e2e_workdir,
        "ultra",
        lists_levels=True,
        levels=_OWN_LEVELS,
        backend="generic",
    )

    # Assert
    assert "thinking 'ultra' is neither a standard level" in result.stderr
    assert server.requests
    assert {_effort(request) for request in server.requests} == {"ultra"}
    export = read_export(output_dir)
    assert export.warnings is not None
    assert any(
        "thinking 'ultra'" in warning.message
        and "Pick another thinking level" in warning.message
        for warning in export.warnings
    )
    assert export.error is not None
    assert "reasoning_effort ultra is not supported" in export.error.message
