"""The Host's picker reads report a config the user can fix as a configuration error.

Regression: ``config/read`` without a session, ``agents/list`` and ``skills/list``
answered ``internal_error`` when strict_config refused the config or the config
did not parse, while session reads and session starts answered ``invalid_params``
of kind configuration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibe.app_server._connection_protocol import ClientInfo
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.protocol import ProtocolErrorCode
from vibe.core.config.harness_files import HarnessFilesManager

_PICKER_READS = ["config/read", "agents/list", "skills/list"]


async def _picker_read_error(
    method: str, *, process_services: ProcessServices
) -> RequestFailure:
    handler = HostRequestHandler(
        HarnessFilesManager(sources=("user",)), org_config=process_services.org_config
    )
    with pytest.raises(RequestFailure) as raised:
        await handler.dispatch(
            method, {}, client_info=ClientInfo(name="test", version="0")
        )
    return raised.value


@pytest.mark.asyncio
@pytest.mark.parametrize("method", _PICKER_READS)
async def test_a_strict_config_refusal_is_a_configuration_error(
    config_dir: Path, method: str, process_services: ProcessServices
) -> None:
    """*Prepare*: A user config with strict_config on and a misspelt setting.
    *Do*: Make a picker read without a session.
    *Assert*: Invalid params of kind configuration, naming the setting.
    """
    # Prepare
    config_file = config_dir / "config.toml"
    config_file.write_text(
        'strict_config = true\nthemee = "dark"\n'
        + config_file.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    # Do
    error = await _picker_read_error(method, process_services=process_services)

    # Assert
    assert error.code is ProtocolErrorCode.INVALID_PARAMS
    assert error.data == {"kind": "configuration"}
    assert "`themee`" in str(error)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", _PICKER_READS)
async def test_an_unparsable_config_is_a_configuration_error(
    config_dir: Path, method: str, process_services: ProcessServices
) -> None:
    """*Prepare*: A user config that is not valid TOML.
    *Do*: Make a picker read without a session.
    *Assert*: Invalid params of kind configuration, as a session start reports.
    """
    # Prepare
    (config_dir / "config.toml").write_text("active_model = [\n", encoding="utf-8")

    # Do
    error = await _picker_read_error(method, process_services=process_services)

    # Assert
    assert error.code is ProtocolErrorCode.INVALID_PARAMS
    assert error.data == {"kind": "configuration"}
    assert str(error).startswith("Cannot load the Vibe config.")


@pytest.mark.asyncio
@pytest.mark.parametrize("method", _PICKER_READS)
async def test_a_value_the_schema_refuses_is_a_configuration_error(
    config_dir: Path, method: str, process_services: ProcessServices
) -> None:
    """*Prepare*: A user config with a value the Vibe schema refuses.
    *Do*: Make a picker read without a session.
    *Assert*: Invalid params of kind configuration, naming the setting, as a
    session start reports.
    """
    # Prepare
    config_file = config_dir / "config.toml"
    config_file.write_text(
        "auto_compact_threshold = 0\n" + config_file.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    # Do
    error = await _picker_read_error(method, process_services=process_services)

    # Assert
    assert error.code is ProtocolErrorCode.INVALID_PARAMS
    assert error.data == {"kind": "configuration"}
    assert str(error).startswith(
        "Cannot load the Vibe config. auto_compact_threshold: 0 is not allowed."
    )
