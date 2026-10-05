from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
import re
import tomllib

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from pydantic import BaseModel, ConfigDict, ValidationError

from vibe.cli.update_notifier.ports.update_gateway import (
    Update,
    UpdateGateway,
    UpdateGatewayCause,
    UpdateGatewayError,
    UpdateGatewayUnavailableError,
)
from vibe.utils.io import read_safe_async

_OUTDATED_TOOL_PATTERN = re.compile(
    r"^(?P<name>\S+) v\S+ \[latest: (?P<latest>[^\]\s]+)\]\s*$"
)
_UNSUPPORTED_OUTDATED_FLAG = "unexpected argument '--outdated'"
_NON_REGISTRY_SOURCES = frozenset({"git", "url", "path", "directory", "editable"})
_EXACT_PIN_OPERATORS = frozenset({"==", "==="})


class _UvReceiptRequirement(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    specifier: str = ""

    @property
    def is_registry(self) -> bool:
        return not (_NON_REGISTRY_SOURCES & (self.model_extra or {}).keys())


class _UvReceiptTool(BaseModel):
    model_config = ConfigDict(extra="ignore")

    requirements: list[_UvReceiptRequirement] = []


class _UvReceipt(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tool: _UvReceiptTool


class UvToolUpdateGateway(UpdateGateway):
    def __init__(
        self,
        project_name: str,
        *,
        receipt_path: Path,
        uv_command: Sequence[str] = ("uv",),
        timeout: float = 30.0,
    ) -> None:
        self._project_name = canonicalize_name(project_name)
        self._receipt_path = receipt_path
        self._uv_command = tuple(uv_command)
        self._timeout = timeout

    async def fetch_update(self) -> Update | None:
        requirement = await self._read_requirement()
        if not requirement.is_registry:
            raise UpdateGatewayUnavailableError(
                cause=UpdateGatewayCause.UNKNOWN,
                message="uv does not report updates for non-registry installs.",
            )

        latest = self._find_latest_version(await self._list_outdated_tools())
        if latest is None or _pins_another_version(requirement.specifier, latest):
            return None
        return Update(latest_version=latest)

    async def _read_requirement(self) -> _UvReceiptRequirement:
        try:
            text = (await read_safe_async(self._receipt_path)).text
            receipt = _UvReceipt.model_validate(tomllib.loads(text))
        except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
            raise UpdateGatewayUnavailableError(
                cause=UpdateGatewayCause.INVALID_RESPONSE,
                message="Unable to read the uv tool receipt.",
            ) from exc

        for requirement in receipt.tool.requirements:
            if canonicalize_name(requirement.name) == self._project_name:
                return requirement
        raise UpdateGatewayUnavailableError(
            cause=UpdateGatewayCause.NOT_FOUND,
            message="The uv tool receipt does not list this project.",
        )

    def _find_latest_version(self, output: str) -> str | None:
        for line in output.splitlines():
            match = _OUTDATED_TOOL_PATTERN.match(line)
            if match and canonicalize_name(match["name"]) == self._project_name:
                return match["latest"]
        return None

    async def _list_outdated_tools(self) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                *self._uv_command,
                "tool",
                "list",
                "--outdated",
                "--color",
                "never",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise UpdateGatewayUnavailableError(
                cause=UpdateGatewayCause.REQUEST_FAILED,
                message="Unable to run uv while checking for updates.",
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout
            )
        except TimeoutError as exc:
            await _kill(process)
            raise UpdateGatewayError(
                cause=UpdateGatewayCause.REQUEST_FAILED,
                message="Timed out while checking for updates with uv.",
            ) from exc
        except asyncio.CancelledError:
            await _kill(process)
            raise

        if process.returncode == 0:
            return stdout.decode(errors="replace")

        error_output = stderr.decode(errors="replace")
        if _UNSUPPORTED_OUTDATED_FLAG in error_output:
            raise UpdateGatewayUnavailableError(
                cause=UpdateGatewayCause.ERROR_RESPONSE,
                message="This uv version cannot list outdated tools.",
            )
        raise UpdateGatewayError(
            cause=UpdateGatewayCause.ERROR_RESPONSE,
            message=_describe_uv_failure(error_output),
        )


async def _kill(process: asyncio.subprocess.Process) -> None:
    with suppress(ProcessLookupError):
        process.kill()
    await process.wait()


def _pins_another_version(specifier: str, latest: str) -> bool:
    # `uv tool upgrade` re-resolves within the requirement recorded at install
    # time, so an exact pin makes any other version a no-op upgrade. Ranges are
    # left alone: whether they block depends on releases between the bounds.
    try:
        clauses = SpecifierSet(specifier)
    except InvalidSpecifier:
        return False
    return any(
        clause.operator in _EXACT_PIN_OPERATORS
        and not clause.version.endswith(".*")
        and not clause.contains(latest, prereleases=True)
        for clause in clauses
    )


def _describe_uv_failure(error_output: str) -> str:
    lines = [line.strip() for line in error_output.splitlines()]
    if detail := next((line for line in lines if line), None):
        return f"uv failed while checking for updates: {detail}"
    return "uv failed while checking for updates."
