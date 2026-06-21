from __future__ import annotations

import os
from pathlib import Path
import tomllib
from uuid import uuid4

import pytest

from vibe.core.config.fingerprint import create_file_fingerprint
from vibe.core.config.layer import LayerImplementationError, LayerNotLoadedError
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.patch import (
    AddOperationPatch,
    ConfigPatch,
    RemoveOperationPatch,
    ReplaceOperationPatch,
)
from vibe.core.config.types import MISSING_CONFIG_FILE_FINGERPRINT


def random_config_file_name() -> str:
    return f"config-{uuid4().hex}.toml"


@pytest.mark.asyncio
async def test_reads_toml_file(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('active_model = "mistral-large"\ncount = 42\n')

    layer = UserConfigLayer(path=path, name="user-toml")
    data = await layer.load()
    assert data.model_extra == {"active_model": "mistral-large", "count": 42}
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)
    assert fingerprint


@pytest.mark.asyncio
async def test_always_trusted(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('key = "value"\n')

    layer = UserConfigLayer(path=path, name="user-toml")
    assert layer.is_trusted is None
    data = await layer.load()
    assert layer.is_trusted is True
    assert data.model_extra == {"key": "value"}


@pytest.mark.asyncio
async def test_missing_file_returns_empty(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / random_config_file_name()
    layer = UserConfigLayer(path=path, name="user-toml")
    data = await layer.load()
    assert data.model_extra == {}
    assert layer.fingerprint == MISSING_CONFIG_FILE_FINGERPRINT


@pytest.mark.asyncio
async def test_apply_raises_when_file_does_not_exist(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    layer = UserConfigLayer(path=path, name="user-toml")

    await layer.load()
    assert layer.fingerprint == MISSING_CONFIG_FILE_FINGERPRINT

    with pytest.raises(LayerNotLoadedError, match="loaded before applying patches"):
        await layer.apply(
            ConfigPatch(
                AddOperationPatch(path="/active_model", value="mistral-large"),
                fingerprint=MISSING_CONFIG_FILE_FINGERPRINT,
            )
        )

    assert not path.exists()


@pytest.mark.asyncio
async def test_apply_sets_field_and_refreshes_cache(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text("""\
active_model = "old"

[tools]
disabled_tools = ["bash", "python"]
deprecated_setting = true
""")
    layer = UserConfigLayer(path=path, name="user-toml")

    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)

    await layer.apply(
        ConfigPatch(
            ReplaceOperationPatch(path="/active_model", value="new"),
            AddOperationPatch(path="/tools/enabled_tools", value=["read"]),
            AddOperationPatch(path="/tools/disabled_tools/-", value="node"),
            RemoveOperationPatch(path="/tools/disabled_tools/0"),
            RemoveOperationPatch(path="/tools/deprecated_setting"),
            fingerprint=fingerprint,
        )
    )

    expected_data = {
        "active_model": "new",
        "tools": {"disabled_tools": ["python", "node"], "enabled_tools": ["read"]},
    }
    with path.open("rb") as file:
        assert tomllib.load(file) == expected_data

    cached_data = layer._state.data
    assert cached_data is not None
    assert cached_data.model_extra == expected_data
    assert layer.fingerprint != fingerprint


@pytest.mark.asyncio
async def test_apply_cache_fingerprint_matches_written_file(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text("")
    layer = UserConfigLayer(path=path, name="user-toml")

    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)

    await layer.apply(
        ConfigPatch(
            AddOperationPatch(path="/active_model", value="mistral-large"),
            fingerprint=fingerprint,
        )
    )

    with path.open("rb") as file:
        assert layer.fingerprint == create_file_fingerprint(file)


@pytest.mark.asyncio
async def test_apply_uses_unique_temp_file(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / random_config_file_name()
    fixed_tmp_path = tmp_working_directory / f".{path.name}.tmp"
    path.write_text("")
    fixed_tmp_path.write_text("stale")
    layer = UserConfigLayer(path=path, name="user-toml")

    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)

    await layer.apply(
        ConfigPatch(
            AddOperationPatch(path="/active_model", value="mistral-large"),
            fingerprint=fingerprint,
        )
    )

    assert fixed_tmp_path.read_text() == "stale"
    assert list(tmp_working_directory.glob(f".{path.name}.*.tmp")) == []
    with path.open("rb") as file:
        assert tomllib.load(file) == {"active_model": "mistral-large"}


def test_atomic_replace_preserves_replacement_fingerprint(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    replacement = tmp_working_directory / f".{path.name}.tmp"
    path.write_text("key = 1")
    replacement.write_text("key = 2")

    with replacement.open("rb") as file:
        replacement_fingerprint = create_file_fingerprint(file)

    os.replace(replacement, path)

    with path.open("rb") as file:
        assert create_file_fingerprint(file) == replacement_fingerprint


@pytest.mark.asyncio
async def test_apply_raises_when_layer_is_not_loaded(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    layer = UserConfigLayer(path=path, name="user-toml")

    with pytest.raises(LayerNotLoadedError, match="loaded before applying patches"):
        await layer.apply(
            ConfigPatch(
                AddOperationPatch(path="/active_model", value="mistral-large"),
                fingerprint=MISSING_CONFIG_FILE_FINGERPRINT,
            )
        )


@pytest.mark.asyncio
async def test_apply_raises_when_cache_is_invalidated(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('active_model = "old"\n')
    layer = UserConfigLayer(path=path, name="user-toml")

    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)
    await layer.invalidate_cache()

    with pytest.raises(LayerNotLoadedError, match="loaded before applying patches"):
        await layer.apply(
            ConfigPatch(
                ReplaceOperationPatch(path="/active_model", value="new"),
                fingerprint=fingerprint,
            )
        )


@pytest.mark.asyncio
async def test_apply_raises_when_parent_directory_does_not_exist(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / "nested" / random_config_file_name()
    layer = UserConfigLayer(path=path, name="user-toml")

    await layer.load()

    with pytest.raises(LayerNotLoadedError, match="loaded before applying patches"):
        await layer.apply(
            ConfigPatch(
                AddOperationPatch(path="/active_model", value="mistral-large"),
                fingerprint=MISSING_CONFIG_FILE_FINGERPRINT,
            )
        )

    assert not path.exists()


@pytest.mark.asyncio
async def test_commit_sets_missing_nested_field(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text("[models]\n")
    layer = UserConfigLayer(path=path, name="user-toml")

    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)

    await layer.apply(
        ConfigPatch(
            AddOperationPatch(path="/models/active_model", value="mistral-large"),
            fingerprint=fingerprint,
        )
    )

    with path.open("rb") as file:
        assert tomllib.load(file) == {"models": {"active_model": "mistral-large"}}


@pytest.mark.asyncio
async def test_apply_overwrites_external_file_changes(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('active_model = "old"\n')
    layer = UserConfigLayer(path=path, name="user-toml")

    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)
    path.write_text('active_model = "external"\n')

    await layer.apply(
        ConfigPatch(
            ReplaceOperationPatch(path="/active_model", value="new"),
            fingerprint=fingerprint,
        )
    )

    with path.open("rb") as file:
        assert tomllib.load(file) == {"active_model": "new"}
    data = await layer.load()
    assert data.model_extra == {"active_model": "new"}


@pytest.mark.asyncio
async def test_nested_toml_structure(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text("""\
[models]
active_model = "test"

[[models.items]]
alias = "a"
provider = "p"
""")
    layer = UserConfigLayer(path=path, name="user-toml")
    data = await layer.load()
    assert data.model_extra == {
        "models": {"active_model": "test", "items": [{"alias": "a", "provider": "p"}]}
    }


@pytest.mark.asyncio
async def test_invalid_toml_raises(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text("this is not valid = = = toml [[[")
    layer = UserConfigLayer(path=path, name="user-toml")
    with pytest.raises(LayerImplementationError, match="_build_config_snapshot"):
        await layer.load()


@pytest.mark.asyncio
async def test_force_reload_reads_fresh_data(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('value = "first"\n')
    layer = UserConfigLayer(path=path, name="user-toml")

    data1 = await layer.load()
    fp1 = layer.fingerprint
    assert data1.model_extra == {"value": "first"}
    assert isinstance(fp1, str)
    assert fp1

    path.write_text('value = "second"\n')
    data2 = await layer.load(force=True)
    fp2 = layer.fingerprint
    assert data2.model_extra == {"value": "second"}
    assert isinstance(fp2, str)
    assert fp2
    assert fp1 != fp2

    path.unlink()
    data3 = await layer.load(force=True)
    assert data3.model_extra == {}
    assert layer.fingerprint == MISSING_CONFIG_FILE_FINGERPRINT


@pytest.mark.asyncio
async def test_empty_toml_file(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text("")
    layer = UserConfigLayer(path=path, name="user-toml")
    data = await layer.load()
    assert data.model_extra == {}
