from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.config.layer import LayerImplementationError
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.patch import ConfigPatch


@pytest.mark.asyncio
async def test_reads_toml_file(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / "config.toml"
    path.write_text('active_model = "mistral-large"\ncount = 42\n')

    layer = UserConfigLayer(path=path, name="user-toml")
    data = await layer.load()
    assert data.model_extra == {"active_model": "mistral-large", "count": 42}


@pytest.mark.asyncio
async def test_always_trusted(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / "config.toml"
    path.write_text('key = "value"\n')

    layer = UserConfigLayer(path=path, name="user-toml")
    assert layer.is_trusted is None
    data = await layer.load()
    assert layer.is_trusted is True
    assert data.model_extra == {"key": "value"}


@pytest.mark.asyncio
async def test_missing_file_returns_empty(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / "nonexistent.toml"
    layer = UserConfigLayer(path=path, name="user-toml")
    data = await layer.load()
    assert data.model_extra == {}


@pytest.mark.asyncio
async def test_apply_raises_not_implemented(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / "config.toml"
    layer = UserConfigLayer(path=path, name="user-toml")
    with pytest.raises(NotImplementedError, match="M2"):
        await layer.apply(ConfigPatch(fingerprint="fp-1"))


@pytest.mark.asyncio
async def test_nested_toml_structure(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / "config.toml"
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
    path = tmp_working_directory / "bad.toml"
    path.write_text("this is not valid = = = toml [[[")
    layer = UserConfigLayer(path=path, name="user-toml")
    with pytest.raises(LayerImplementationError, match="_read_config"):
        await layer.load()


@pytest.mark.asyncio
async def test_force_reload_reads_fresh_data(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / "config.toml"
    path.write_text('value = "first"\n')
    layer = UserConfigLayer(path=path, name="user-toml")

    data1 = await layer.load()
    assert data1.model_extra == {"value": "first"}

    path.write_text('value = "second"\n')
    data2 = await layer.load(force=True)
    assert data2.model_extra == {"value": "second"}


@pytest.mark.asyncio
async def test_empty_toml_file(tmp_working_directory: Path) -> None:
    path = tmp_working_directory / "empty.toml"
    path.write_text("")
    layer = UserConfigLayer(path=path, name="user-toml")
    data = await layer.load()
    assert data.model_extra == {}
