from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import tomllib

import pytest
import tomli_w

from vibe.cli.vscode_extension_promo import (
    MAX_SHOWN_COUNT,
    PROMO_START,
    FileSystemVscodeExtensionPromoRepository,
    VscodeExtensionPromoState,
    should_show_promo,
)


@pytest.mark.asyncio
async def test_repository_roundtrip_persists_shown_count(tmp_path: Path) -> None:
    repository = FileSystemVscodeExtensionPromoRepository(base_path=tmp_path)

    await repository.set(VscodeExtensionPromoState(shown_count=4))

    assert await repository.get() == VscodeExtensionPromoState(shown_count=4)


@pytest.mark.asyncio
async def test_repository_set_preserves_unrelated_cache_sections(
    tmp_path: Path,
) -> None:
    with (tmp_path / "cache.toml").open("wb") as f:
        tomli_w.dump({"update_cache": {"latest_version": "2.0.0"}}, f)
    repository = FileSystemVscodeExtensionPromoRepository(base_path=tmp_path)

    await repository.set(VscodeExtensionPromoState(shown_count=1))

    with (tmp_path / "cache.toml").open("rb") as f:
        data = tomllib.load(f)
    assert data["update_cache"]["latest_version"] == "2.0.0"
    assert data["vscode_extension_promo"]["shown_count"] == 1


@pytest.mark.parametrize(
    "state,expected",
    [
        (None, True),
        (VscodeExtensionPromoState(shown_count=0), True),
        (VscodeExtensionPromoState(shown_count=MAX_SHOWN_COUNT - 1), True),
        (VscodeExtensionPromoState(shown_count=MAX_SHOWN_COUNT), False),
        (VscodeExtensionPromoState(shown_count=MAX_SHOWN_COUNT + 5), False),
    ],
)
def test_should_show_promo_respects_threshold(
    state: VscodeExtensionPromoState | None, expected: bool
) -> None:
    assert should_show_promo(state, now=PROMO_START) is expected


@pytest.mark.parametrize(
    "now,expected",
    [
        (PROMO_START - timedelta(seconds=1), False),
        (PROMO_START - timedelta(days=365), False),
        (PROMO_START, True),
        (PROMO_START + timedelta(seconds=1), True),
    ],
)
def test_should_show_promo_gated_by_start_date(now: datetime, expected: bool) -> None:
    assert should_show_promo(None, now=now) is expected


def test_promo_start_is_2026_05_28_16_utc() -> None:
    assert PROMO_START == datetime(2026, 5, 28, 16, 0, tzinfo=UTC)
