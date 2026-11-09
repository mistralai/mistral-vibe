from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vibe.utils.io import read_safe


class UpstreamFixtureFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    fixture: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("source", "fixture")
    @classmethod
    def require_relative_path(cls, value: str) -> str:
        _validate_relative_file_path(value)
        return value


class UpstreamFixtureLicense(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    spdx: str = Field(min_length=1)
    source: str

    @field_validator("source")
    @classmethod
    def require_relative_path(cls, value: str) -> str:
        _validate_relative_file_path(value)
        return value


class UpstreamPluginFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    repository: str = Field(
        pattern=r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git$"
    )
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_path: str
    license: UpstreamFixtureLicense
    expected_normalized_output: str
    files: tuple[UpstreamFixtureFile, ...] = Field(min_length=1)

    @field_validator("source_path")
    @classmethod
    def require_relative_directory_path(cls, value: str) -> str:
        _validate_relative_path(value)
        return value

    @field_validator("expected_normalized_output")
    @classmethod
    def require_relative_output_path(cls, value: str) -> str:
        _validate_relative_file_path(value)
        return value


class UpstreamFixtureCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    fixtures: tuple[UpstreamPluginFixture, ...]


class FixtureVerificationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fixture_id: str
    code: str
    path: str
    message: str


def load_upstream_fixture_catalog(path: Path) -> UpstreamFixtureCatalog:
    return UpstreamFixtureCatalog.model_validate_json(
        read_safe(path, raise_on_error=True).text
    )


def verify_local_fixture_catalog(
    catalog_path: Path,
) -> tuple[FixtureVerificationIssue, ...]:
    catalog = load_upstream_fixture_catalog(catalog_path)
    fixture_ids: set[str] = set()
    issues: list[FixtureVerificationIssue] = []
    for fixture in catalog.fixtures:
        if fixture.id in fixture_ids:
            issues.append(
                FixtureVerificationIssue(
                    fixture_id=fixture.id,
                    code="fixture.id.duplicate",
                    path=".",
                    message=f"Fixture id {fixture.id!r} is duplicated.",
                )
            )
            continue
        fixture_ids.add(fixture.id)
        issues.extend(_verify_local_fixture(catalog_path, fixture))
    return tuple(issues)


def compare_fixture_to_upstream(
    catalog_path: Path,
    fixture: UpstreamPluginFixture,
    read_upstream: Callable[[str], bytes],
) -> tuple[FixtureVerificationIssue, ...]:
    issues: list[FixtureVerificationIssue] = []
    for file in fixture.files:
        try:
            fixture_path = _contained_fixture_path(catalog_path, file.fixture)
        except ValueError as error:
            issues.append(
                FixtureVerificationIssue(
                    fixture_id=fixture.id,
                    code="fixture.path.outside_catalog",
                    path=file.fixture,
                    message=str(error),
                )
            )
            continue
        try:
            local = fixture_path.read_bytes()
        except OSError as error:
            issues.append(
                FixtureVerificationIssue(
                    fixture_id=fixture.id,
                    code="fixture.file.unreadable",
                    path=file.fixture,
                    message=str(error),
                )
            )
            continue
        upstream_path = _join_source_path(fixture.source_path, file.source)
        try:
            upstream = read_upstream(upstream_path)
        except OSError as error:
            issues.append(
                FixtureVerificationIssue(
                    fixture_id=fixture.id,
                    code="fixture.upstream.unreadable",
                    path=upstream_path,
                    message=str(error),
                )
            )
            continue
        if local != upstream:
            issues.append(
                FixtureVerificationIssue(
                    fixture_id=fixture.id,
                    code="fixture.upstream.mismatch",
                    path=file.fixture,
                    message=f"Vendored file differs from upstream {upstream_path}.",
                )
            )
    return tuple(issues)


def _verify_local_fixture(
    catalog_path: Path, fixture: UpstreamPluginFixture
) -> list[FixtureVerificationIssue]:
    issues: list[FixtureVerificationIssue] = []
    try:
        expected_path = _contained_fixture_path(
            catalog_path, fixture.expected_normalized_output
        )
        expected = json.loads(read_safe(expected_path, raise_on_error=True).text)
        if not isinstance(expected, dict):
            raise ValueError("expected normalized output must be a JSON object")
        if expected.get("schemaVersion") != 1:
            raise ValueError("expected normalized output must use schemaVersion 1")
        if "root" in expected or "contentDigest" in expected:
            raise ValueError(
                "expected normalized output cannot contain machine-specific roots "
                "or content digests"
            )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        issues.append(
            FixtureVerificationIssue(
                fixture_id=fixture.id,
                code="fixture.expected_output.invalid",
                path=fixture.expected_normalized_output,
                message=str(error),
            )
        )

    if not fixture.expected_normalized_output.startswith(f"{fixture.id}/"):
        issues.append(
            FixtureVerificationIssue(
                fixture_id=fixture.id,
                code="fixture.expected_output.outside_fixture",
                path=fixture.expected_normalized_output,
                message="Expected output must be stored inside its fixture directory.",
            )
        )

    source_files = {file.source for file in fixture.files}
    if fixture.license.source not in source_files:
        issues.append(
            FixtureVerificationIssue(
                fixture_id=fixture.id,
                code="fixture.license.not_vendored",
                path=fixture.license.source,
                message="The declared upstream license must be one of the vendored files.",
            )
        )

    seen_sources: set[str] = set()
    seen_fixture_paths: set[str] = set()
    for file in fixture.files:
        if file.source in seen_sources:
            issues.append(
                FixtureVerificationIssue(
                    fixture_id=fixture.id,
                    code="fixture.source.duplicate",
                    path=file.source,
                    message="The upstream source path is listed more than once.",
                )
            )
        seen_sources.add(file.source)
        if file.fixture in seen_fixture_paths:
            issues.append(
                FixtureVerificationIssue(
                    fixture_id=fixture.id,
                    code="fixture.path.duplicate",
                    path=file.fixture,
                    message="The vendored fixture path is listed more than once.",
                )
            )
        seen_fixture_paths.add(file.fixture)
        if not file.fixture.startswith(f"{fixture.id}/source/"):
            issues.append(
                FixtureVerificationIssue(
                    fixture_id=fixture.id,
                    code="fixture.path.outside_fixture",
                    path=file.fixture,
                    message=(
                        "Vendored files must be stored inside the fixture source "
                        "directory."
                    ),
                )
            )
        try:
            fixture_path = _contained_fixture_path(catalog_path, file.fixture)
            digest = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
        except (OSError, ValueError) as error:
            issues.append(
                FixtureVerificationIssue(
                    fixture_id=fixture.id,
                    code=(
                        "fixture.path.outside_catalog"
                        if isinstance(error, ValueError)
                        else "fixture.file.unreadable"
                    ),
                    path=file.fixture,
                    message=str(error),
                )
            )
            continue
        if digest != file.sha256:
            issues.append(
                FixtureVerificationIssue(
                    fixture_id=fixture.id,
                    code="fixture.file.digest_mismatch",
                    path=file.fixture,
                    message=f"Expected sha256:{file.sha256}, got sha256:{digest}.",
                )
            )
    return issues


def _contained_fixture_path(catalog_path: Path, relative: str) -> Path:
    root = catalog_path.parent.resolve()
    path = root.joinpath(*PurePosixPath(relative).parts).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Fixture path escapes catalog root: {relative}")
    return path


def _validate_relative_path(value: str) -> None:
    if not value:
        raise ValueError("path cannot be empty")
    if "\\" in value:
        raise ValueError("path must use forward slashes")
    if PureWindowsPath(value).drive:
        raise ValueError("path cannot use a Windows drive prefix")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("path must be relative and cannot contain '..'")


def _validate_relative_file_path(value: str) -> None:
    _validate_relative_path(value)
    if value in {"", "."} or value.endswith("/"):
        raise ValueError("path must identify a file")


def _join_source_path(root: str, path: str) -> str:
    if root in {"", "."}:
        return path
    return (PurePosixPath(root) / path).as_posix()
