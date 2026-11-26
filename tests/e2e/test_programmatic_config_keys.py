"""Settings Vibe does not use, seen by a headless caller: `vibe -p` warns about
them on stderr and in its export, refuses them under `strict_config`, and
`vibe run-server` refuses them in a run's supplied config; `vibe config schema`
describes every setting.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tomllib

import pytest

from tests.e2e.agent_loop_characterization.support import assistant_text_chunks
from tests.e2e.common import read_export, run_rust_vibe, write_e2e_config
from tests.e2e.mock_server import ChatCompletionsRequestPayload, StreamingMockServer
from tests.e2e.run_server_client import run_server
from tests.stubs.fake_agent_server import (
    FakeAgentServer,
    private_socket_path,
    serving_in_background,
)

pytestmark = [
    pytest.mark.timeout(120),
    pytest.mark.usefixtures("setup_e2e_env", "rust_cli"),
]

_TYPOS = ['actiev_model = "mock-model"', "include_model_info = false"]


def _text_reply(
    _request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    return assistant_text_chunks("All done.")


def _rewrite_config(server: StreamingMockServer, settings: list[str]) -> None:
    write_e2e_config(Path(os.environ["VIBE_HOME"]), server.api_base, settings=settings)


@pytest.mark.parametrize("streaming_mock_server", [_text_reply], indirect=True)
def test_headless_run_warns_about_unused_settings_and_finishes(
    streaming_mock_server: StreamingMockServer, e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: A config with a misspelt and a retired setting.
    *Do*: Run `vibe -p`.
    *Assert*: It finishes; stderr and the export name both settings.
    """
    # Prepare
    _rewrite_config(streaming_mock_server, _TYPOS)
    output_dir = tmp_path / "out"

    # Do
    result = run_rust_vibe(
        e2e_workdir, ["-p", "Say hi", "--output-dir", str(output_dir)]
    )

    # Assert
    assert result.returncode == 0, result.stderr
    assert "`actiev_model` is not a Vibe setting" in result.stderr
    assert "did you mean `active_model`?" in result.stderr
    assert "`include_model_info` is no longer used" in result.stderr
    export = read_export(output_dir)
    assert export.outcome == "finished"
    assert export.warnings is not None
    assert any("`actiev_model`" in warning.message for warning in export.warnings)


@pytest.mark.parametrize("streaming_mock_server", [_text_reply], indirect=True)
def test_strict_config_refuses_a_headless_run_as_a_config_error(
    streaming_mock_server: StreamingMockServer, e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: The same config, with `strict_config = true`.
    *Do*: Run `vibe -p`.
    *Assert*: Exit 1, `config_error`, the error names each setting, and the
    model was never called.
    """
    # Prepare
    _rewrite_config(streaming_mock_server, ["strict_config = true", *_TYPOS])
    output_dir = tmp_path / "out"

    # Do
    result = run_rust_vibe(
        e2e_workdir, ["-p", "Say hi", "--output-dir", str(output_dir)]
    )

    # Assert
    assert result.returncode == 1, result.stderr
    assert "strict_config is on" in result.stderr
    assert "`include_model_info` is no longer used" in result.stderr
    export = read_export(output_dir)
    assert export.outcome == "config_error"
    assert export.error is not None
    assert "`actiev_model`" in export.error.message
    assert not streaming_mock_server.requests


@pytest.mark.parametrize("streaming_mock_server", [_text_reply], indirect=True)
def test_run_server_refuses_a_strict_supplied_config(
    streaming_mock_server: StreamingMockServer, tmp_path: Path
) -> None:
    """*Prepare*: A run server, and a run config with `strict_config = true`
    and a misspelt setting.
    *Do*: Start the run with that config.
    *Assert*: It ends `config_error`, naming the setting in the supplied config.
    """
    # Prepare
    config = tomllib.loads((Path(os.environ["VIBE_HOME"]) / "config.toml").read_text())
    config |= {"strict_config": True, "actiev_model": "mock-model"}
    with private_socket_path() as socket:
        workspace = socket.parent / "workspace"
        workspace.mkdir()
        server = FakeAgentServer(socket, workspace, tools=[])
        with serving_in_background(server), run_server(tmp_path) as client:
            client.initialize()

            # Do
            client.start_run(
                "strict", socket, outputDir=str(tmp_path / "out"), config=config
            )
            ended = client.ended("strict")
            client.finish()

    # Assert
    assert ended["outcome"] == "config_error", ended
    error = read_export(tmp_path / "out").error
    assert error is not None
    assert "`actiev_model`" in error.message
    assert "the supplied user config" in error.message


def test_config_schema_describes_every_setting(e2e_workdir: Path) -> None:
    """*Do*: Run `vibe config schema`.
    *Assert*: It prints a JSON schema with the top-level settings, each builtin
    tool's settings, and retired settings marked deprecated.
    """
    # Do
    result = run_rust_vibe(e2e_workdir, ["config", "schema"])

    # Assert
    assert result.returncode == 0, result.stderr
    schema = json.loads(result.stdout)
    properties = schema["properties"]
    assert properties["strict_config"]["type"] == "boolean"
    assert properties["include_model_info"]["deprecated"] is True
    bash = properties["tools"]["properties"]["bash"]
    assert "allowlist" in bash["properties"]
    assert bash["additionalProperties"] is False
    assert schema["additionalProperties"] is False
    assert schema["$comment"].startswith("version ")
