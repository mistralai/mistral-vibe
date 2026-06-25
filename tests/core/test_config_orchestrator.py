from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
import tomllib
from typing import Annotated, Any

from pydantic import Field, ValidationError
import pytest

from vibe.core.config.event_bus import EventBus
from vibe.core.config.layer import (
    ConfigLayer,
    ConfigPatchApplicationError,
    LayerImplementationError,
    LayerNotLoadedError,
    RawConfig,
)
from vibe.core.config.layers.environment import EnvironmentLayer
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.orchestrator import ConfigOrchestrator, ConfigPatchValidationError
from vibe.core.config.patch import (
    AddOperationPatch,
    RemoveOperationPatch,
    ReplaceOperationPatch,
)
from vibe.core.config.schema import (
    ConfigFragment,
    ConfigSchema,
    WithConcatMerge,
    WithReplaceMerge,
)
from vibe.core.config.types import (
    ConcurrencyConflictError,
    ConfigChangeEvent,
    LayerConfigSnapshot,
)


class FakeLayer(ConfigLayer[RawConfig]):
    def __init__(self, *, name: str, data: dict[str, Any]) -> None:
        super().__init__(name=name)
        self._data = data

    async def _check_trust(self) -> bool:
        return True

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        return LayerConfigSnapshot(data=dict(self._data), fingerprint="fp")

    async def _save_to_store(self, _next_config: RawConfig) -> str:
        raise NotImplementedError


class NormalizingWritableLayer(FakeLayer):
    async def _save_to_store(self, next_config: RawConfig) -> str:
        value = next_config.model_dump()["value"]
        self._data = {"value": f"{value}-normalized"}
        return "write-fp"


class FieldWritableLayer(FakeLayer):
    def __init__(
        self,
        *,
        name: str,
        field_name: str,
        data: dict[str, Any],
        barrier: ParallelSaveBarrier | None = None,
    ) -> None:
        super().__init__(name=name, data=data)
        self._field_name = field_name
        self._barrier = barrier

    async def _save_to_store(self, next_config: RawConfig) -> str:
        if self._barrier is not None:
            await self._barrier.wait(self.name)

        value = next_config.model_dump()[self._field_name]
        self._data = {self._field_name: value}
        return "write-fp"


class RawWritableLayer(FakeLayer):
    async def _save_to_store(self, next_config: RawConfig) -> str:
        self._data = next_config.model_dump()
        return "write-fp"


class FailingSaveLayer(FakeLayer):
    async def _save_to_store(self, _next_config: RawConfig) -> str:
        raise RuntimeError("boom")


class ApplyErrorLayer(FakeLayer):
    def __init__(self, *, name: str, data: dict[str, Any], error: Exception) -> None:
        super().__init__(name=name, data=data)
        self._error = error

    async def apply(self, *args: Any, **kwargs: Any) -> None:
        raise self._error


class ParallelSaveBarrier:
    def __init__(self, expected_starts: int) -> None:
        self.expected_starts = expected_starts
        self.started_layers: list[str] = []
        self.all_started = asyncio.Event()

    async def wait(self, layer_name: str) -> None:
        self.started_layers.append(layer_name)
        if len(self.started_layers) == self.expected_starts:
            self.all_started.set()

        await asyncio.wait_for(self.all_started.wait(), timeout=0.1)


class SimpleSchema(ConfigSchema):
    value: Annotated[str, WithReplaceMerge()] = "default"


class MultiValueSchema(ConfigSchema):
    first: Annotated[str, WithReplaceMerge()] = "default-first"
    second: Annotated[str, WithReplaceMerge()] = "default-second"


class ToolsFragment(ConfigFragment):
    enabled_tools: Annotated[list[str], WithConcatMerge()] = Field(default_factory=list)
    disabled_tools: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list
    )
    deprecated_setting: Annotated[bool, WithReplaceMerge()] = False


class ToolSchema(ConfigSchema):
    active_model: Annotated[str, WithReplaceMerge()] = "default-model"
    tools: ToolsFragment = Field(default_factory=ToolsFragment)


class RoutingSchema(ConfigSchema):
    active_model: Annotated[str, WithReplaceMerge()] = "default-model"
    default_agent: Annotated[str, WithReplaceMerge()] = "default-agent"


class RequiredPairSchema(ConfigSchema):
    first: Annotated[str, WithReplaceMerge()]
    second: Annotated[str, WithReplaceMerge()]


def assert_single_failure[E: BaseException](
    result: Sequence[BaseException], expected_type: type[E]
) -> E:
    assert len(result) == 1
    failure = result[0]
    assert isinstance(failure, expected_type)
    return failure


@pytest.mark.asyncio
async def test_create_builds_config() -> None:
    layer = FakeLayer(name="test", data={"value": "hello"})
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[layer])
    assert orch.config.model_dump() == {"value": "hello"}


@pytest.mark.asyncio
async def test_get_layer_returns_named_layer() -> None:
    layer = FakeLayer(name="my-layer", data={})
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[layer])
    assert orch.get_layer("my-layer") is layer


@pytest.mark.asyncio
async def test_get_layer_unknown_raises() -> None:
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[])
    with pytest.raises(KeyError, match="No layer named 'unknown'"):
        orch.get_layer("unknown")


@pytest.mark.asyncio
async def test_reload_picks_up_changes() -> None:
    layer = FakeLayer(name="mutable", data={"value": "original"})
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[layer])
    assert orch.config.value == "original"

    layer._data = {"value": "updated"}
    await orch.reload()
    assert orch.config.value == "updated"


@pytest.mark.asyncio
async def test_config_is_immutable() -> None:
    layer = FakeLayer(name="test", data={"value": "hello"})
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[layer])
    with pytest.raises(ValidationError, match="frozen"):
        orch.config.value = "changed"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_origin_of_missing_key_returns_none() -> None:
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[])
    assert orch.config.origin_of("nonexistent") is None


@pytest.mark.asyncio
async def test_apply_patch_empty_operations_is_noop() -> None:
    layer = FakeLayer(name="test", data={"value": "hello"})
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[layer])

    result = await orch.apply_patch([], reason="no-op")

    assert result == []
    assert orch.config.value == "hello"


@pytest.mark.asyncio
async def test_apply_patch_rejects_invalid_schema_result_before_routing() -> None:
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[])
    with pytest.raises(ConfigPatchValidationError) as exc_info:
        await orch.apply_patch(
            [ReplaceOperationPatch(path="/value", value={"invalid": "shape"})],
            reason="test invalid patch",
        )

    assert exc_info.value.args == (
        "Config patch failed preflight validation against the merged config; fix the patch payload and retry",
    )
    assert isinstance(exc_info.value.__cause__, ValidationError)


@pytest.mark.asyncio
async def test_apply_patch_returns_failure_when_default_fallback_layer_is_missing() -> (
    None
):
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[])

    result = await orch.apply_patch(
        [ReplaceOperationPatch(path="/value", value="updated")], reason="test update"
    )

    failure = assert_single_failure(result, KeyError)
    assert str(failure) == f'"No layer named {UserConfigLayer.LAYER_NAME!r}"'
    assert orch.config.value == "default"


@pytest.mark.asyncio
async def test_apply_patch_unknown_explicit_target_returns_failure() -> None:
    layer = NormalizingWritableLayer(name=UserConfigLayer.LAYER_NAME, data={})
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[layer])

    result = await orch.apply_patch(
        [
            AddOperationPatch(
                path="/value", value="updated", target_layer_name="missing-layer"
            )
        ],
        reason="test update",
    )

    failure = assert_single_failure(result, KeyError)
    assert str(failure) == "\"No layer named 'missing-layer'\""
    assert orch.config.value == "default"
    assert layer._data == {}


@pytest.mark.asyncio
async def test_apply_patch_falls_back_to_default_user_layer_and_reloads_config() -> (
    None
):
    layer = NormalizingWritableLayer(name=UserConfigLayer.LAYER_NAME, data={})
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[layer])

    result = await orch.apply_patch(
        [AddOperationPatch(path="/value", value="updated")], reason="test update"
    )

    assert result == []
    assert orch.config.value == "updated-normalized"
    assert layer._data == {"value": "updated-normalized"}


@pytest.mark.asyncio
async def test_apply_patch_returns_layer_save_error_after_other_layer_commits() -> None:
    first_layer = FieldWritableLayer(
        name="first-layer", field_name="first", data={"first": "one"}
    )
    second_layer = FailingSaveLayer(name="second-layer", data={"second": "two"})
    orch = await ConfigOrchestrator.create(
        schema=MultiValueSchema, layers=[first_layer, second_layer]
    )

    result = await orch.apply_patch(
        [
            ReplaceOperationPatch(
                path="/first", value="updated-one", target_layer_name="first-layer"
            ),
            ReplaceOperationPatch(
                path="/second", value="updated-two", target_layer_name="second-layer"
            ),
        ],
        reason="test partial apply",
    )

    failure = assert_single_failure(result, LayerImplementationError)
    assert str(failure) == "Layer 'second-layer': _save_to_store() failed"
    assert isinstance(failure.__cause__, RuntimeError)
    assert first_layer._data == {"first": "updated-one"}
    assert second_layer._data == {"second": "two"}


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(
            ConcurrencyConflictError(expected_fp="before", actual_fp="after"),
            id="concurrency-conflict",
        ),
        pytest.param(ConfigPatchApplicationError("test"), id="patch-application-error"),
        pytest.param(RuntimeError("unexpected bug"), id="unexpected-runtime-error"),
    ],
)
@pytest.mark.asyncio
async def test_apply_patch_returns_layer_apply_error_in_failures(
    error: Exception,
) -> None:
    layer = ApplyErrorLayer(name="test", data={"value": "hello"}, error=error)
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[layer])

    result = await orch.apply_patch(
        [
            ReplaceOperationPatch(
                path="/value", value="updated", target_layer_name="test"
            )
        ],
        reason="test update",
    )

    assert assert_single_failure(result, type(error)) is error


@pytest.mark.asyncio
async def test_apply_patch_returns_unloaded_layer_error_in_failures() -> None:
    loaded_layer = FakeLayer(name="loaded", data={"value": "hello"})
    target_layer = FakeLayer(name="target", data={"value": "hello"})
    orch = await ConfigOrchestrator.create(
        schema=SimpleSchema, layers=[loaded_layer, target_layer]
    )
    await target_layer.invalidate_cache()

    result = await orch.apply_patch(
        [
            ReplaceOperationPatch(
                path="/value", value="updated", target_layer_name="target"
            )
        ],
        reason="test update",
    )

    failure = assert_single_failure(result, LayerNotLoadedError)
    assert str(failure) == "Layer 'target' must be loaded before applying patches"


@pytest.mark.asyncio
async def test_apply_patch_applies_layers_in_parallel() -> None:
    barrier = ParallelSaveBarrier(expected_starts=2)
    first_layer = FieldWritableLayer(
        name="first-layer", field_name="first", data={"first": "one"}, barrier=barrier
    )
    second_layer = FieldWritableLayer(
        name="second-layer",
        field_name="second",
        data={"second": "two"},
        barrier=barrier,
    )
    orch = await ConfigOrchestrator.create(
        schema=MultiValueSchema, layers=[first_layer, second_layer]
    )

    result = await orch.apply_patch(
        [
            ReplaceOperationPatch(
                path="/first", value="updated-one", target_layer_name="first-layer"
            ),
            ReplaceOperationPatch(
                path="/second", value="updated-two", target_layer_name="second-layer"
            ),
        ],
        reason="test parallel apply",
    )

    assert result == []
    assert barrier.started_layers == ["first-layer", "second-layer"]
    assert first_layer._data == {"first": "updated-one"}
    assert second_layer._data == {"second": "updated-two"}


@pytest.mark.asyncio
async def test_apply_patch_end_to_end_updates_real_user_config_file(
    tmp_working_directory: Path,
) -> None:
    toml_path = tmp_working_directory / "config.toml"
    toml_path.write_text(
        """\
active_model = "old"

[tools]
disabled_tools = ["bash", "python"]
deprecated_setting = true
""",
        encoding="utf-8",
    )

    user_layer = UserConfigLayer(path=toml_path)
    orch = await ConfigOrchestrator.create(schema=ToolSchema, layers=[user_layer])

    result = await orch.apply_patch(
        [
            ReplaceOperationPatch(path="/active_model", value="new"),
            AddOperationPatch(path="/tools/enabled_tools", value=["read"]),
            AddOperationPatch(path="/tools/disabled_tools/-", value="node"),
            RemoveOperationPatch(path="/tools/disabled_tools/0"),
            RemoveOperationPatch(path="/tools/deprecated_setting"),
        ],
        reason="update user defaults",
    )

    assert result == []
    with toml_path.open("rb") as file:
        assert tomllib.load(file) == {
            "active_model": "new",
            "tools": {"disabled_tools": ["python", "node"], "enabled_tools": ["read"]},
        }
    assert orch.config.active_model == "new"
    assert orch.config.tools.disabled_tools == ["python", "node"]
    assert orch.config.tools.enabled_tools == ["read"]


@pytest.mark.asyncio
async def test_apply_patch_fallback_to_user_layer_fails_when_user_file_is_missing(
    tmp_working_directory: Path,
) -> None:
    toml_path = tmp_working_directory / "config.toml"
    user_layer = UserConfigLayer(path=toml_path)
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[user_layer])

    result = await orch.apply_patch(
        [AddOperationPatch(path="/value", value="created-later")],
        reason="fallback write without user file",
    )

    failure = assert_single_failure(result, LayerNotLoadedError)
    assert str(failure) == "Layer 'user-toml' must be loaded before applying patches"
    assert not toml_path.exists()
    assert orch.config.value == "default"


@pytest.mark.asyncio
async def test_apply_patch_end_to_end_falls_back_to_user_layer_when_no_target_is_provided(
    monkeypatch: pytest.MonkeyPatch, tmp_working_directory: Path
) -> None:
    toml_path = tmp_working_directory / "config.toml"
    toml_path.write_text('default_agent = "plan"\n', encoding="utf-8")
    monkeypatch.setenv("VIBE_ACTIVE_MODEL", "env-model")

    user_layer = UserConfigLayer(path=toml_path)
    environment_layer = EnvironmentLayer(schema=RoutingSchema)
    orch = await ConfigOrchestrator.create(
        schema=RoutingSchema, layers=[user_layer, environment_layer]
    )

    result = await orch.apply_patch(
        [
            AddOperationPatch(path="/active_model", value="persisted-in-user-file"),
            ReplaceOperationPatch(path="/default_agent", value="accept-edits"),
        ],
        reason="update runtime defaults",
    )

    assert result == []
    with toml_path.open("rb") as file:
        assert tomllib.load(file) == {
            "active_model": "persisted-in-user-file",
            "default_agent": "accept-edits",
        }
    assert orch.config.active_model == "env-model"
    assert orch.config.default_agent == "accept-edits"


@pytest.mark.asyncio
async def test_apply_patch_end_to_end_respects_explicit_target_layer(
    monkeypatch: pytest.MonkeyPatch, tmp_working_directory: Path
) -> None:
    toml_path = tmp_working_directory / "config.toml"
    toml_path.write_text('default_agent = "plan"\n', encoding="utf-8")
    monkeypatch.setenv("VIBE_ACTIVE_MODEL", "env-model")

    user_layer = UserConfigLayer(path=toml_path)
    environment_layer = EnvironmentLayer(schema=RoutingSchema)
    orch = await ConfigOrchestrator.create(
        schema=RoutingSchema, layers=[user_layer, environment_layer]
    )

    result = await orch.apply_patch(
        [
            ReplaceOperationPatch(
                path="/active_model",
                value="persist-me-nowhere",
                target_layer_name="environment",
            ),
            ReplaceOperationPatch(path="/default_agent", value="accept-edits"),
        ],
        reason="update runtime defaults",
    )

    failure = assert_single_failure(result, NotImplementedError)
    assert str(failure) == "EnvironmentLayer patch persistence is not implemented yet"
    with toml_path.open("rb") as file:
        assert tomllib.load(file) == {"default_agent": "accept-edits"}
    assert orch.config.active_model == "env-model"
    assert orch.config.default_agent == "accept-edits"


@pytest.mark.asyncio
async def test_subscribe_registers_on_the_bus() -> None:
    bus = EventBus()
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[], bus=bus)
    received: list[ConfigChangeEvent] = []
    orch.subscribe(received.append)

    event = ConfigChangeEvent(
        changed_keys=frozenset({"value"}), before={}, after={}, reason=""
    )
    bus.publish(event)

    assert received == [event]
