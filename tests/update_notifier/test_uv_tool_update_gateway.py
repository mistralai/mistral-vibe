from __future__ import annotations

from pathlib import Path
import sys
import textwrap

import pytest

from vibe.cli.update_notifier.adapters.uv_tool_update_gateway import UvToolUpdateGateway
from vibe.cli.update_notifier.ports.update_gateway import (
    Update,
    UpdateGatewayCause,
    UpdateGatewayError,
    UpdateGatewayUnavailableError,
)

OUTDATED_OUTPUT = (
    "fast-resume v1.18.0 [latest: 2.12.0]\n"
    "- fast-resume\n"
    "mistral-vibe v2.25.2 [latest: 2.25.8]\n"
    "- vibe\n"
    "- vibe-acp\n"
)


def _receipt(tmp_path: Path, requirement: str = '{ name = "mistral-vibe" }') -> Path:
    receipt = tmp_path / "uv-receipt.toml"
    receipt.write_text(
        f"[tool]\nrequirements = [{requirement}]\n\n[tool.options]\n", encoding="utf-8"
    )
    return receipt


def _fake_uv(
    tmp_path: Path,
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    sleep_seconds: float = 0,
) -> tuple[str, ...]:
    script = tmp_path / "fake_uv.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys, time
            assert sys.argv[1:] == ["tool", "list", "--outdated", "--color", "never"], sys.argv
            time.sleep({sleep_seconds!r})
            sys.stdout.write({stdout!r})
            sys.stderr.write({stderr!r})
            sys.exit({exit_code!r})
            """
        ),
        encoding="utf-8",
    )
    return (sys.executable, str(script))


def _gateway(
    tmp_path: Path,
    *,
    requirement: str = '{ name = "mistral-vibe" }',
    project_name: str = "mistral-vibe",
    timeout: float = 30.0,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    sleep_seconds: float = 0,
) -> UvToolUpdateGateway:
    uv_command = _fake_uv(
        tmp_path,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        sleep_seconds=sleep_seconds,
    )
    return UvToolUpdateGateway(
        project_name,
        receipt_path=_receipt(tmp_path, requirement),
        uv_command=uv_command,
        timeout=timeout,
    )


@pytest.mark.asyncio
async def test_retrieves_the_latest_version_reported_by_uv(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path, project_name="mistral_vibe", stdout=OUTDATED_OUTPUT)

    update = await gateway.fetch_update()

    assert update == Update(latest_version="2.25.8")


@pytest.mark.asyncio
async def test_retrieves_nothing_when_uv_does_not_report_the_project_as_outdated(
    tmp_path: Path,
) -> None:
    gateway = _gateway(
        tmp_path, stdout="pre-commit v4.5.1 [latest: 4.6.2]\n- pre-commit\n"
    )

    update = await gateway.fetch_update()

    assert update is None


@pytest.mark.asyncio
@pytest.mark.parametrize("specifier", ["==2.25.2", "===2.25.2", ">=2,==2.25.2"])
async def test_retrieves_nothing_when_the_install_is_pinned_to_another_version(
    tmp_path: Path, specifier: str
) -> None:
    gateway = _gateway(
        tmp_path,
        requirement=f'{{ name = "mistral-vibe", specifier = "{specifier}" }}',
        stdout=OUTDATED_OUTPUT,
    )

    update = await gateway.fetch_update()

    assert update is None


@pytest.mark.asyncio
@pytest.mark.parametrize("specifier", [">=2.25", "<3", "==2.*", "==2.25.8"])
async def test_retrieves_the_update_when_the_specifier_does_not_pin_another_version(
    tmp_path: Path, specifier: str
) -> None:
    gateway = _gateway(
        tmp_path,
        requirement=f'{{ name = "mistral-vibe", specifier = "{specifier}" }}',
        stdout=OUTDATED_OUTPUT,
    )

    update = await gateway.fetch_update()

    assert update == Update(latest_version="2.25.8")


@pytest.mark.asyncio
async def test_is_unavailable_for_git_installs(tmp_path: Path) -> None:
    gateway = _gateway(
        tmp_path,
        requirement='{ name = "mistral-vibe", git = "https://github.com/mistralai/mistral-vibe.git" }',
        stdout=OUTDATED_OUTPUT,
    )

    with pytest.raises(UpdateGatewayUnavailableError):
        await gateway.fetch_update()


@pytest.mark.asyncio
async def test_is_unavailable_when_the_receipt_does_not_list_the_project(
    tmp_path: Path,
) -> None:
    gateway = _gateway(
        tmp_path, requirement='{ name = "other-tool" }', stdout=OUTDATED_OUTPUT
    )

    with pytest.raises(UpdateGatewayUnavailableError):
        await gateway.fetch_update()


@pytest.mark.asyncio
async def test_is_unavailable_when_the_receipt_is_missing(tmp_path: Path) -> None:
    gateway = UvToolUpdateGateway(
        "mistral-vibe",
        receipt_path=tmp_path / "missing.toml",
        uv_command=_fake_uv(tmp_path, stdout=OUTDATED_OUTPUT),
    )

    with pytest.raises(UpdateGatewayUnavailableError):
        await gateway.fetch_update()


@pytest.mark.asyncio
async def test_is_unavailable_when_uv_does_not_support_outdated(tmp_path: Path) -> None:
    gateway = _gateway(
        tmp_path, stderr="error: unexpected argument '--outdated' found\n", exit_code=2
    )

    with pytest.raises(UpdateGatewayUnavailableError):
        await gateway.fetch_update()


@pytest.mark.asyncio
async def test_is_unavailable_when_uv_cannot_be_started(tmp_path: Path) -> None:
    gateway = UvToolUpdateGateway(
        "mistral-vibe",
        receipt_path=_receipt(tmp_path),
        uv_command=(str(tmp_path / "missing-uv"),),
    )

    with pytest.raises(UpdateGatewayUnavailableError) as excinfo:
        await gateway.fetch_update()

    assert excinfo.value.cause == UpdateGatewayCause.REQUEST_FAILED


@pytest.mark.asyncio
async def test_fails_with_uv_error_when_uv_cannot_resolve(tmp_path: Path) -> None:
    gateway = _gateway(
        tmp_path,
        stderr="error: Request failed after 3 retries\n  Caused by: dns error\n",
        exit_code=2,
    )

    with pytest.raises(UpdateGatewayError) as excinfo:
        await gateway.fetch_update()

    assert not isinstance(excinfo.value, UpdateGatewayUnavailableError)
    assert excinfo.value.cause == UpdateGatewayCause.ERROR_RESPONSE
    assert excinfo.value.user_message == (
        "uv failed while checking for updates: error: Request failed after 3 retries"
    )


@pytest.mark.asyncio
async def test_fails_when_uv_times_out(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path, sleep_seconds=30, timeout=0.5)

    with pytest.raises(UpdateGatewayError) as excinfo:
        await gateway.fetch_update()

    assert not isinstance(excinfo.value, UpdateGatewayUnavailableError)
    assert excinfo.value.cause == UpdateGatewayCause.REQUEST_FAILED
