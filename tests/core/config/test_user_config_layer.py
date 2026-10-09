from __future__ import annotations

import os
from pathlib import Path
import tomllib
from uuid import uuid4

import pytest

from vibe.core.config._migration import migrate_config_layers
from vibe.core.config.fingerprint import create_file_fingerprint
from vibe.core.config.layer import (
    ConfigStorageError,
    LayerImplementationError,
    LayerNotLoadedError,
)
from vibe.core.config.layers import _base
from vibe.core.config.layers.project import ProjectConfigLayer
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.patch import (
    AddOperationPatch,
    ConfigPatch,
    RemoveOperationPatch,
    ReplaceOperationPatch,
)
from vibe.core.config.types import MISSING_BACKING_STORE_DATA_FINGERPRINT
from vibe.core.trusted_folders import trusted_folders_manager


def random_config_file_name() -> str:
    return f"config-{uuid4().hex}.toml"


@pytest.mark.asyncio
async def test_reads_toml_file(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('active_model = "mistral-large"\ncount = 42\n')

    layer = UserConfigLayer(path=path)
    data = await layer.load()
    assert data.model_extra == {"active_model": "mistral-large", "count": 42}
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)
    assert fingerprint


@pytest.mark.asyncio
async def test_always_trusted(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('key = "value"\n')

    layer = UserConfigLayer(path=path)
    assert layer.is_trusted is None
    data = await layer.load()
    assert layer.is_trusted is True
    assert data.model_extra == {"key": "value"}


@pytest.mark.asyncio
async def test_missing_file_returns_empty(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / random_config_file_name()
    layer = UserConfigLayer(path=path)
    data = await layer.load()
    assert data.model_extra == {}
    assert layer.fingerprint == MISSING_BACKING_STORE_DATA_FINGERPRINT


@pytest.mark.asyncio
async def test_apply_creates_file_when_it_does_not_exist(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    layer = UserConfigLayer(path=path)

    await layer.load()
    assert layer.fingerprint == MISSING_BACKING_STORE_DATA_FINGERPRINT

    await layer.apply(
        ConfigPatch(
            AddOperationPatch(path="/active_model", value="mistral-large"),
            fingerprint=MISSING_BACKING_STORE_DATA_FINGERPRINT,
        )
    )

    with path.open("rb") as file:
        assert tomllib.load(file) == {"active_model": "mistral-large"}
        assert layer.fingerprint == create_file_fingerprint(file)


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
    layer = UserConfigLayer(path=path)

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
async def test_apply_raises_config_storage_error_when_write_fails(
    tmp_working_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A read-only config.toml (e.g. symlinked read-only from the Nix store)
    # makes the atomic write raise OSError. It must surface as a typed
    # ConfigStorageError, not an uncaught traceback. chmod is unreliable under
    # root, so raise the OSError from the write path directly.
    path = tmp_working_directory / random_config_file_name()
    path.write_text('active_model = "old"\n')
    layer = UserConfigLayer(path=path)
    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)

    def deny_write(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(_base.tempfile, "NamedTemporaryFile", deny_write)

    with pytest.raises(ConfigStorageError) as excinfo:
        await layer.apply(
            ConfigPatch(
                ReplaceOperationPatch(path="/active_model", value="new"),
                fingerprint=fingerprint,
            )
        )

    assert excinfo.value.path == path
    assert excinfo.value.operation == "write"
    assert isinstance(excinfo.value.__cause__, PermissionError)


@pytest.mark.asyncio
async def test_load_raises_config_storage_error_when_read_fails(
    tmp_working_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('active_model = "old"\n')
    layer = UserConfigLayer(path=path)

    original_open = Path.open

    def deny_open(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if self == path:
            raise PermissionError(13, "Permission denied", str(path))
        return original_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", deny_open)

    with pytest.raises(ConfigStorageError) as excinfo:
        await layer.load()

    assert excinfo.value.path == path
    assert excinfo.value.operation == "read"
    assert isinstance(excinfo.value.__cause__, PermissionError)


@pytest.mark.asyncio
async def test_apply_cache_fingerprint_matches_written_file(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text("")
    layer = UserConfigLayer(path=path)

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
    layer = UserConfigLayer(path=path)

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
    layer = UserConfigLayer(path=path)

    with pytest.raises(LayerNotLoadedError, match="loaded before applying patches"):
        await layer.apply(
            ConfigPatch(
                AddOperationPatch(path="/active_model", value="mistral-large"),
                fingerprint=MISSING_BACKING_STORE_DATA_FINGERPRINT,
            )
        )


@pytest.mark.asyncio
async def test_apply_raises_when_cache_is_invalidated(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('active_model = "old"\n')
    layer = UserConfigLayer(path=path)

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
async def test_apply_creates_parent_directory_when_it_does_not_exist(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / "nested" / random_config_file_name()
    layer = UserConfigLayer(path=path)

    await layer.load()

    await layer.apply(
        ConfigPatch(
            AddOperationPatch(path="/active_model", value="mistral-large"),
            fingerprint=MISSING_BACKING_STORE_DATA_FINGERPRINT,
        )
    )

    with path.open("rb") as file:
        assert tomllib.load(file) == {"active_model": "mistral-large"}


@pytest.mark.asyncio
async def test_commit_sets_missing_nested_field(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text("[models]\n")
    layer = UserConfigLayer(path=path)

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
    layer = UserConfigLayer(path=path)

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
    layer = UserConfigLayer(path=path)
    data = await layer.load()
    assert data.model_extra == {
        "models": {"active_model": "test", "items": [{"alias": "a", "provider": "p"}]}
    }


@pytest.mark.asyncio
async def test_invalid_toml_raises(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text("this is not valid = = = toml [[[")
    layer = UserConfigLayer(path=path)
    with pytest.raises(LayerImplementationError, match="_build_config_snapshot"):
        await layer.load()


@pytest.mark.asyncio
async def test_force_reload_reads_fresh_data(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('value = "first"\n')
    layer = UserConfigLayer(path=path)

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
    assert layer.fingerprint == MISSING_BACKING_STORE_DATA_FINGERPRINT


@pytest.mark.asyncio
async def test_empty_toml_file(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text("")
    layer = UserConfigLayer(path=path)
    data = await layer.load()
    assert data.model_extra == {}


@pytest.mark.asyncio
async def test_migrate_config_layers_persists_changes(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text(
        """\
active_model = "devstral-2"

[[models]]
name = "mistral-vibe-cli-latest"
alias = "devstral-2"
provider = "mistral"
"""
    )

    layer = UserConfigLayer(path=path)

    await migrate_config_layers([layer])

    with path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["active_model"] == "mistral-medium-3.5"
    assert persisted["models"][0]["alias"] == "mistral-medium-3.5"
    migrated_data = await layer.load()
    assert migrated_data.model_extra is not None
    assert migrated_data.model_extra["active_model"] == "mistral-medium-3.5"


@pytest.mark.asyncio
async def test_migrate_config_layers_drops_sparse_leftover_devstral_small(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text(
        'active_model = "devstral-small"\n\n'
        "[[models]]\n"
        'alias = "devstral-small"\n'
        'thinking = "low"\n'
    )
    layer = UserConfigLayer(path=path)

    await migrate_config_layers([layer])

    with path.open("rb") as file:
        persisted = tomllib.load(file)
    # The pin is left to the load-time fallback: another layer may still
    # define the model.
    assert persisted["active_model"] == "devstral-small"
    assert "models" not in persisted


@pytest.mark.asyncio
async def test_migrate_config_layers_keeps_complete_devstral_small(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text(
        'active_model = "devstral-small"\n\n'
        "[[models]]\n"
        'name = "devstral-small-latest"\n'
        'provider = "mistral"\n'
        'alias = "devstral-small"\n'
        'thinking = "off"\n'
    )
    layer = UserConfigLayer(path=path)

    await migrate_config_layers([layer])

    with path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["active_model"] == "devstral-small"
    assert persisted["models"][0]["alias"] == "devstral-small"


@pytest.mark.asyncio
async def test_migrate_config_layers_completes_sparse_leftover_local(
    tmp_working_directory: Path,
) -> None:
    # /thinking on the built-in wrote a sparse entry; the built-in is gone but
    # the model still runs through llamacpp, so the entry gains the identity it
    # needs to stand alone and the pin keeps resolving.
    path = tmp_working_directory / random_config_file_name()
    path.write_text('active_model = "local"\n\n[models.local]\nthinking = "low"\n')
    layer = UserConfigLayer(path=path)

    await migrate_config_layers([layer])

    with path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["active_model"] == "local"
    assert persisted["models"] == [
        {
            "thinking": "low",
            "alias": "local",
            "name": "devstral",
            "provider": "llamacpp",
        }
    ]


@pytest.mark.asyncio
async def test_migrate_config_layers_keeps_pin_to_removed_local_without_override(
    tmp_working_directory: Path,
) -> None:
    # A per-file migration cannot know the pin is stale; the load-time
    # fallback handles unknown pins.
    path = tmp_working_directory / random_config_file_name()
    path.write_text('active_model = "local"\n')
    layer = UserConfigLayer(path=path)

    await migrate_config_layers([layer])

    with path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["active_model"] == "local"


@pytest.mark.asyncio
async def test_migrate_config_layers_keeps_pin_when_entry_is_in_project_config(
    tmp_working_directory: Path,
) -> None:
    # Pin in the user config, model entry in the project config: each file
    # migrates without seeing the other, so the pin must survive.
    user_path = tmp_working_directory / random_config_file_name()
    user_path.write_text('active_model = "local"\n')
    project_path = tmp_working_directory / ".vibe" / "config.toml"
    project_path.parent.mkdir(parents=True, exist_ok=True)
    project_path.write_text('[models.local]\nthinking = "low"\n')
    trusted_folders_manager.add_trusted(project_path.parent)

    await migrate_config_layers([
        UserConfigLayer(path=user_path),
        ProjectConfigLayer(path=tmp_working_directory),
    ])

    with user_path.open("rb") as file:
        assert tomllib.load(file)["active_model"] == "local"
    with project_path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["models"] == [
        {
            "alias": "local",
            "thinking": "low",
            "name": "devstral",
            "provider": "llamacpp",
        }
    ]


@pytest.mark.asyncio
async def test_migrate_config_layers_does_not_stamp_local_over_custom_definition(
    tmp_working_directory: Path,
) -> None:
    # The user config completely defines a custom ``local``; the project config
    # holds a sparse leftover. The merge fills the sparse entry with the user's
    # identity, so stamping the higher-precedence project file would shadow it.
    user_path = tmp_working_directory / random_config_file_name()
    user_path.write_text(
        '[[models]]\nname = "my-gguf"\nprovider = "my-llamacpp"\nalias = "local"\n'
    )
    project_path = tmp_working_directory / ".vibe" / "config.toml"
    project_path.parent.mkdir(parents=True, exist_ok=True)
    project_path.write_text('[models.local]\nthinking = "low"\n')
    trusted_folders_manager.add_trusted(project_path.parent)

    await migrate_config_layers([
        UserConfigLayer(path=user_path),
        ProjectConfigLayer(path=tmp_working_directory),
    ])

    with user_path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["models"] == [
        {"name": "my-gguf", "provider": "my-llamacpp", "alias": "local"}
    ]
    with project_path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["models"] == {"local": {"thinking": "low"}}


@pytest.mark.asyncio
async def test_migrate_config_layers_completes_local_despite_devstral_named_model(
    tmp_working_directory: Path,
) -> None:
    # The merge fills sparse entries per alias, so a complete ``devstral``-named
    # model under another alias must not suppress completing ``local``.
    user_path = tmp_working_directory / random_config_file_name()
    user_path.write_text(
        '[[models]]\nname = "devstral"\nprovider = "llamacpp"\nalias = "my-devstral"\n'
    )
    project_path = tmp_working_directory / ".vibe" / "config.toml"
    project_path.parent.mkdir(parents=True, exist_ok=True)
    project_path.write_text('[models.local]\nthinking = "low"\n')
    trusted_folders_manager.add_trusted(project_path.parent)

    await migrate_config_layers([
        UserConfigLayer(path=user_path),
        ProjectConfigLayer(path=tmp_working_directory),
    ])

    with project_path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["models"] == [
        {
            "alias": "local",
            "thinking": "low",
            "name": "devstral",
            "provider": "llamacpp",
        }
    ]


@pytest.mark.asyncio
async def test_migrate_config_layers_completes_devstral_named_entry_under_other_alias(
    tmp_working_directory: Path,
) -> None:
    # A complete ``local`` elsewhere suppresses stamping only for ``local``-
    # keyed entries; an entry identified by name under another alias is always
    # completed, since the merge never fills it.
    user_path = tmp_working_directory / random_config_file_name()
    user_path.write_text(
        '[[models]]\nname = "my-gguf"\nprovider = "my-llamacpp"\nalias = "local"\n'
    )
    project_path = tmp_working_directory / ".vibe" / "config.toml"
    project_path.parent.mkdir(parents=True, exist_ok=True)
    project_path.write_text('[models.my-devstral]\nname = "devstral"\n')
    trusted_folders_manager.add_trusted(project_path.parent)

    await migrate_config_layers([
        UserConfigLayer(path=user_path),
        ProjectConfigLayer(path=tmp_working_directory),
    ])

    with project_path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["models"] == [
        {"alias": "my-devstral", "name": "devstral", "provider": "llamacpp"}
    ]


@pytest.mark.asyncio
async def test_migrate_config_layers_completes_local_when_no_file_defines_it(
    tmp_working_directory: Path,
) -> None:
    # No file defines ``local`` completely, so each sparse entry is completed
    # to stand alone.
    first_path = tmp_working_directory / random_config_file_name()
    first_path.write_text('[models.local]\nthinking = "low"\n')
    second_path = tmp_working_directory / random_config_file_name()
    second_path.write_text('[models.local]\nthinking = "high"\n')

    await migrate_config_layers([
        UserConfigLayer(path=first_path),
        UserConfigLayer(path=second_path),
    ])

    for path in (first_path, second_path):
        with path.open("rb") as file:
            persisted = tomllib.load(file)
        assert persisted["models"] == [
            {
                "alias": "local",
                "thinking": "low" if path is first_path else "high",
                "name": "devstral",
                "provider": "llamacpp",
            }
        ]


@pytest.mark.asyncio
async def test_migrate_config_layers_keeps_complete_local(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text(
        'active_model = "local"\n\n'
        "[[models]]\n"
        'name = "devstral"\n'
        'provider = "llamacpp"\n'
        'alias = "local"\n'
    )
    layer = UserConfigLayer(path=path)

    await migrate_config_layers([layer])

    with path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["active_model"] == "local"
    assert persisted["models"][0]["alias"] == "local"


@pytest.mark.asyncio
async def test_migrate_config_layers_renames_default_agent_to_ask(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text(
        'default_agent = "default"\n'
        'enabled_agents = ["default", "plan"]\n'
        'disabled_agents = ["default"]\n'
        'installed_agents = ["default", "lean"]\n'
    )
    layer = UserConfigLayer(path=path)

    await migrate_config_layers([layer])

    with path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["default_agent"] == "ask"
    assert persisted["enabled_agents"] == ["ask", "plan"]
    assert persisted["disabled_agents"] == ["ask"]
    assert persisted["installed_agents"] == ["ask", "lean"]


@pytest.mark.asyncio
async def test_migrate_config_layers_pins_default_agent_when_accept_edits_excluded_by_enabled(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('enabled_agents = ["default", "plan"]\n')
    layer = UserConfigLayer(path=path)

    await migrate_config_layers([layer])

    with path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["default_agent"] == "ask"
    assert persisted["enabled_agents"] == ["ask", "plan"]


@pytest.mark.asyncio
async def test_migrate_config_layers_pins_default_agent_when_accept_edits_disabled(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('disabled_agents = ["accept-edits"]\n')
    layer = UserConfigLayer(path=path)

    await migrate_config_layers([layer])

    with path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["default_agent"] == "ask"
    assert persisted["disabled_agents"] == ["accept-edits"]


@pytest.mark.asyncio
async def test_migrate_config_layers_does_not_pin_default_when_new_default_allowed(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('disabled_agents = ["default"]\n')
    layer = UserConfigLayer(path=path)

    await migrate_config_layers([layer])

    with path.open("rb") as file:
        persisted = tomllib.load(file)
    assert "default_agent" not in persisted
    assert persisted["disabled_agents"] == ["ask"]


@pytest.mark.asyncio
async def test_migrate_config_layers_pins_default_when_only_old_default_enabled(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('enabled_agents = ["default"]\n')
    layer = UserConfigLayer(path=path)

    await migrate_config_layers([layer])

    with path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["default_agent"] == "ask"
    assert persisted["enabled_agents"] == ["ask"]


@pytest.mark.asyncio
async def test_migrate_config_layers_does_not_pin_default_without_agent_filters(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('active_model = "current"\n')
    layer = UserConfigLayer(path=path)

    await migrate_config_layers([layer])

    with path.open("rb") as file:
        persisted = tomllib.load(file)
    assert "default_agent" not in persisted


@pytest.mark.asyncio
async def test_migrate_config_layers_pins_default_with_glob_pattern_enabled_agents(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('enabled_agents = ["re:ask"]\n')
    layer = UserConfigLayer(path=path)

    await migrate_config_layers([layer])

    with path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["default_agent"] == "ask"
    assert persisted["enabled_agents"] == ["re:ask"]


@pytest.mark.asyncio
async def test_migrate_config_layers_is_noop_when_config_is_current(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text('active_model = "current"\n')

    layer = UserConfigLayer(path=path)
    before = path.read_bytes()

    await migrate_config_layers([layer])

    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_migrate_config_layers_is_noop_when_models_lack_devstral_small(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    path.write_text(
        'active_model = "local"\n\n'
        "[[models]]\n"
        'name = "local-model"\n'
        'alias = "local"\n'
        'provider = "openai"\n'
    )
    layer = UserConfigLayer(path=path)
    before = path.read_bytes()

    await migrate_config_layers([layer])

    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_migrate_config_layers_does_not_create_missing_file_without_changes(
    tmp_working_directory: Path,
) -> None:
    path = tmp_working_directory / random_config_file_name()
    layer = UserConfigLayer(path=path)

    await migrate_config_layers([layer])

    assert not path.exists()
