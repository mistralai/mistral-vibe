"""How `config.toml` shell timeouts load: a shell tool's `default_timeout` and
`max_timeout_seconds` are set together, as whole numbers of seconds above 0, and
tools that are not shells keep their own timeout settings unchecked.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

from pydantic import ValidationError
import pytest

from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.shell_timeouts import shell_timeout_seconds
from vibe.core.config.vibe_schema import VibeConfigSchema
from vibe.core.tools.builtins.bash import BashToolConfig
from vibe.core.tools.builtins.experimental_bash import ExperimentalBashToolConfig
from vibe.core.tools.builtins.git_bash import GitBashToolConfig
from vibe.core.tools.builtins.windows_shell import WindowsShellToolConfig


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("settings", "message"),
    [
        pytest.param(
            "default_timeout = 60\nmax_timeout_seconds = inf\n",
            "tools.bash: max_timeout_seconds inf is not a number of seconds",
            id="maximum inf",
        ),
        pytest.param(
            "default_timeout = 60\nmax_timeout_seconds = nan\n",
            "tools.bash: max_timeout_seconds nan is not a number of seconds",
            id="maximum nan",
        ),
        pytest.param(
            "default_timeout = nan\nmax_timeout_seconds = 600\n",
            "tools.bash: default_timeout nan is not a number of seconds",
            id="default nan",
        ),
        pytest.param(
            "default_timeout = inf\nmax_timeout_seconds = inf\n",
            "tools.bash: default_timeout inf is not a number of seconds",
            id="default inf",
        ),
        pytest.param(
            'default_timeout = 60\nmax_timeout_seconds = "inf"\n',
            "tools.bash: max_timeout_seconds inf is not a number of seconds",
            id="quoted maximum inf",
        ),
        pytest.param(
            'default_timeout = "soon"\nmax_timeout_seconds = 600\n',
            "tools.bash: default_timeout soon is not a number of seconds",
            id="default not a number",
        ),
        pytest.param(
            'default_timeout = "0"\nmax_timeout_seconds = 600\n',
            "tools.bash: default_timeout 0 is not above 0 seconds",
            id="quoted default 0",
        ),
        pytest.param(
            "default_timeout = 60\nmax_timeout_seconds = 450.5\n",
            "tools.bash: max_timeout_seconds 450.5 is not a whole number of seconds",
            id="fractional maximum",
        ),
        pytest.param(
            'default_timeout = "59.9"\nmax_timeout_seconds = 600\n',
            "tools.bash: default_timeout 59.9 is not a whole number of seconds",
            id="quoted fractional default",
        ),
    ],
)
async def test_a_shell_timeout_that_is_not_a_finite_number_is_a_config_error(
    tmp_path: Path, settings: str, message: str
) -> None:
    """*Prepare*: A `config.toml` whose bash default or maximum timeout is `inf`,
    `nan`, text that is not a number, quoted text of a number not above 0, or a
    fraction of a second.
    *Do*: Load the config.
    *Assert*: Loading fails, naming the setting, instead of a session that cannot
    start or a timeout rounded to whole seconds.
    """
    # Prepare
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(f"[tools.bash]\n{settings}")

    # Do / Assert
    with patch.dict(os.environ, {}, clear=True):
        user = UserConfigLayer(path=toml_path)
        with pytest.raises(ValidationError) as exc_info:
            _ = await ConfigOrchestrator.create(
                schema=VibeConfigSchema,
                layers=[user],
                default_layer_resolver=lambda: user,
            )

    assert message in str(exc_info.value)


@pytest.mark.asyncio
async def test_shell_timeouts_given_as_text_of_numbers_load(tmp_path: Path) -> None:
    """*Prepare*: Bash timeouts given as quoted text of numbers, which the bash
    tool's own config reads as numbers.
    *Do*: Load the config.
    *Assert*: It loads with both as given.
    """
    # Do
    tools = await _load_tools(
        '[tools.bash]\ndefault_timeout = "60"\nmax_timeout_seconds = "900"\n', tmp_path
    )

    # Assert
    assert tools["bash"]["default_timeout"] == "60"
    assert tools["bash"]["max_timeout_seconds"] == "900"


@pytest.mark.asyncio
async def test_timeout_settings_of_a_tool_that_is_not_a_shell_are_not_checked(
    tmp_path: Path,
) -> None:
    """*Prepare*: A custom tool whose own `default_timeout` and
    `max_timeout_seconds` break the shell rules (inf, a default above the
    maximum, and an unpaired one).
    *Do*: Load the config.
    *Assert*: It loads; the shell rules apply to bash, git_bash and powershell only.
    """
    # Do
    tools = await _load_tools(
        "[tools.my_tool]\nmax_timeout_seconds = inf\n"
        "[tools.other_tool]\ndefault_timeout = 900\nmax_timeout_seconds = 600\n"
        "[tools.third_tool]\ndefault_timeout = 900\n",
        tmp_path,
    )

    # Assert
    assert tools["my_tool"]["max_timeout_seconds"] == float("inf")
    assert tools["other_tool"]["default_timeout"] == 900
    assert tools["third_tool"]["default_timeout"] == 900


async def _load_tools(toml: str, tmp_path: Path) -> dict[str, dict[str, Any]]:
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(toml)
    with patch.dict(os.environ, {}, clear=True):
        user = UserConfigLayer(path=toml_path)
        orchestrator = await ConfigOrchestrator.create(
            schema=VibeConfigSchema, layers=[user], default_layer_resolver=lambda: user
        )
    return orchestrator.config.tools


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["bash", "git_bash", "powershell"])
@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        pytest.param(
            "default_timeout = 60\nmax_timeout_seconds = 900\n",
            {"default_timeout": 60, "max_timeout_seconds": 900},
            id="both set",
        ),
        pytest.param("permission = 'ask'\n", {}, id="neither set"),
        pytest.param(
            "default_timeout = 120\n",
            {"default_timeout": 120},
            id="only a default within Vibe's maximum set",
        ),
        pytest.param(
            "default_timeout = 300\n",
            {"default_timeout": 300},
            id="only a default equal to Vibe's maximum set",
        ),
    ],
)
async def test_a_shell_tool_takes_its_timeouts_as_set(
    tmp_path: Path, tool: str, settings: str, expected: dict[str, int]
) -> None:
    """*Prepare*: A `config.toml` whose shell tool sets both its default and
    maximum timeouts, neither, or only a default within Vibe's maximum.
    *Do*: Load the config.
    *Assert*: It loads, with the timeouts as set and none added.
    """
    # Do
    tools = await _load_tools(f"[tools.{tool}]\n{settings}", tmp_path)

    # Assert
    timeouts = {
        key: value
        for key, value in tools[tool].items()
        if key in {"default_timeout", "max_timeout_seconds"}
    }
    assert timeouts == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["bash", "git_bash", "powershell"])
@pytest.mark.parametrize(
    ("settings", "message"),
    [
        pytest.param(
            "default_timeout = 600\n",
            "default_timeout is set without max_timeout_seconds. Set both, or neither.",
            id="only a default above Vibe's maximum set",
        ),
        pytest.param(
            "max_timeout_seconds = 60\n",
            "max_timeout_seconds is set without default_timeout. Set both, or neither.",
            id="only the maximum set",
        ),
    ],
)
async def test_a_shell_timeout_set_without_the_other_is_a_config_error(
    tmp_path: Path, tool: str, settings: str, message: str
) -> None:
    """*Prepare*: A `config.toml` whose shell tool sets a default timeout above
    Vibe's maximum without a maximum, or its maximum without a default.
    *Do*: Load the config.
    *Assert*: Loading fails, naming both settings.
    """
    # Do / Assert
    with pytest.raises(ValidationError) as exc_info:
        await _load_tools(f"[tools.{tool}]\n{settings}", tmp_path)

    assert f"tools.{tool}: {message}" in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("settings", "message"),
    [
        pytest.param(
            "default_timeout = 0\nmax_timeout_seconds = 60\n",
            "tools.bash: default_timeout 0 is not above 0 seconds",
            id="default",
        ),
        pytest.param(
            "default_timeout = 60\nmax_timeout_seconds = -1\n",
            "tools.bash: max_timeout_seconds -1 is not above 0 seconds",
            id="maximum",
        ),
    ],
)
async def test_a_shell_timeout_not_above_0_is_a_config_error(
    tmp_path: Path, settings: str, message: str
) -> None:
    """*Prepare*: A `config.toml` whose shell tool sets a timeout of 0 or below.
    *Do*: Load the config.
    *Assert*: Loading fails, naming the setting, instead of the timeout being
    ignored.
    """
    # Do / Assert
    with pytest.raises(ValidationError) as exc_info:
        await _load_tools(f"[tools.bash]\n{settings}", tmp_path)

    assert message in str(exc_info.value)


@pytest.mark.asyncio
async def test_a_default_timeout_alone_on_a_tool_that_is_not_a_shell_loads(
    tmp_path: Path,
) -> None:
    """*Prepare*: A `config.toml` that sets `default_timeout` alone on `grep`,
    which has no maximum.
    *Do*: Load the config.
    *Assert*: It loads; only shell tools pair the two settings.
    """
    # Do
    tools = await _load_tools("[tools.grep]\ndefault_timeout = 30\n", tmp_path)

    # Assert
    assert tools["grep"]["default_timeout"] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "shown"),
    [
        pytest.param("9007199254740992", "9007199254740992", id="2**53"),
        pytest.param('"9007199254740992"', "9007199254740992", id="quoted 2**53"),
        pytest.param("18446744073709551615", "18446744073709551615", id="u64 max"),
        pytest.param("1e20", "1e+20", id="whole float above u64 max"),
    ],
)
async def test_a_shell_timeout_above_the_longest_a_session_starts_with_is_a_config_error(
    tmp_path: Path, value: str, shown: str
) -> None:
    """*Prepare*: A `config.toml` whose bash maximum is a whole number of seconds
    above 9007199254740991 (2**53 - 1), the longest a session starts with: the
    local harness hashes the Core configuration as RFC 8785 JSON, whose integers
    stop there.
    *Do*: Load the config.
    *Assert*: Loading fails, naming the setting and the longest, instead of the
    session failing to start or the timeout being lowered.
    """
    # Do / Assert
    with pytest.raises(ValidationError) as exc_info:
        await _load_tools(
            f"[tools.bash]\ndefault_timeout = 60\nmax_timeout_seconds = {value}\n",
            tmp_path,
        )

    assert (
        f"tools.bash: max_timeout_seconds {shown} is above 9007199254740991"
        " seconds, the longest shell timeout." in str(exc_info.value)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", ["9007199254740991", '"9007199254740991"'], ids=["int", "quoted"]
)
async def test_a_shell_timeout_at_the_longest_a_session_starts_with_loads(
    tmp_path: Path, value: str
) -> None:
    """*Prepare*: A `config.toml` whose bash maximum is 9007199254740991
    (2**53 - 1) seconds, the longest a session starts with.
    *Do*: Load the config.
    *Assert*: It loads.
    """
    # Do
    tools = await _load_tools(
        f"[tools.bash]\ndefault_timeout = 60\nmax_timeout_seconds = {value}\n", tmp_path
    )

    # Assert
    assert int(tools["bash"]["max_timeout_seconds"]) == 9007199254740991


_SHELL_TOOL_CONFIGS = [
    pytest.param("bash", BashToolConfig, id="bash"),
    pytest.param("bash", ExperimentalBashToolConfig, id="managed bash"),
    pytest.param("git_bash", GitBashToolConfig, id="git_bash"),
    pytest.param("powershell", WindowsShellToolConfig, id="powershell"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "config_class"), _SHELL_TOOL_CONFIGS)
@pytest.mark.parametrize(
    ("default", "maximum", "seconds"),
    [
        pytest.param('"1e3"', '"1e3"', 1000, id="quoted exponent"),
        pytest.param("1e3", "1e3", 1000, id="exponent"),
        pytest.param('"60"', '"900.0"', (60, 900), id="quoted whole numbers"),
        pytest.param("60", "900.0", (60, 900), id="whole numbers"),
    ],
)
async def test_the_shell_tool_reads_the_timeouts_the_config_loads_with(
    tmp_path: Path,
    tool: str,
    config_class: type[BashToolConfig],
    default: str,
    maximum: str,
    seconds: int | tuple[int, int],
) -> None:
    """*Prepare*: A shell tool's timeouts written as text of whole numbers, an
    exponent such as `"1e3"` included, which the config loads.
    *Do*: Load the config, then read the tool's own config from it.
    *Assert*: The tool reads the same whole seconds the config check does, so a
    config that loads never fails when the tool is used.
    """
    # Prepare
    default_seconds, max_seconds = (
        seconds if isinstance(seconds, tuple) else (seconds, seconds)
    )
    tools = await _load_tools(
        f"[tools.{tool}]\ndefault_timeout = {default}\nmax_timeout_seconds = {maximum}\n",
        tmp_path,
    )

    # Do
    config = config_class.model_validate(tools[tool])

    # Assert
    assert config.default_timeout == default_seconds
    assert config.max_timeout_seconds == max_seconds
    assert config.default_timeout == shell_timeout_seconds(
        tool, "default_timeout", tools[tool]["default_timeout"]
    )


@pytest.mark.parametrize(("tool", "config_class"), _SHELL_TOOL_CONFIGS)
@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        pytest.param(
            "default_timeout",
            "59.9",
            "default_timeout 59.9 is not a whole number of seconds",
            id="fractional default",
        ),
        pytest.param(
            "max_timeout_seconds",
            "inf",
            "max_timeout_seconds inf is not a number of seconds",
            id="maximum inf",
        ),
        pytest.param(
            "max_timeout_seconds",
            0,
            "max_timeout_seconds 0 is not above 0 seconds",
            id="maximum 0",
        ),
    ],
)
def test_the_shell_tool_refuses_a_timeout_as_the_config_check_does(
    tool: str, config_class: type[BashToolConfig], key: str, value: object, message: str
) -> None:
    """*Prepare*: A shell tool's timeout the config check refuses.
    *Do*: Read the tool's own config with it.
    *Assert*: The tool refuses it too, with the config check's message.
    """
    # Do / Assert
    with pytest.raises(ValidationError) as exc_info:
        config_class.model_validate({key: value})

    assert message in str(exc_info.value)
