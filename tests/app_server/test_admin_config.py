from __future__ import annotations

import pytest

from vibe.core.config.admin_config import AdminConfigApplyResult


@pytest.mark.asyncio
async def test_disabled_answer_on_reload_drops_the_loaded_org_config() -> None:
    from vibe.app_server._admin_config import load_fetched_admin_config
    from vibe.core.config import VibeConfigSchema
    from vibe.core.config.admin_config import AdminConfigOutcome
    from vibe.core.config.layers.admin import AdminConfigLayer
    from vibe.core.config.orchestrator import ConfigOrchestrator

    admin = AdminConfigLayer()
    admin.load_managed_toml('theme = "nord"\n')
    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema, layers=[admin], default_layer_resolver=lambda: admin
    )
    assert orchestrator.config.theme == "nord"

    result, answer = await load_fetched_admin_config(
        orchestrator, AdminConfigApplyResult(AdminConfigOutcome.DISABLED)
    )

    assert result.outcome is AdminConfigOutcome.DISABLED
    # Cached as an empty policy once the session accepts it.
    assert answer == ""
    assert admin.enforced_keys == []
    assert orchestrator.config.theme != "nord"


@pytest.mark.asyncio
async def test_disabled_answer_with_nothing_loaded_leaves_the_layer_alone() -> None:
    from vibe.app_server._admin_config import load_fetched_admin_config
    from vibe.core.config import VibeConfigSchema
    from vibe.core.config.admin_config import AdminConfigOutcome
    from vibe.core.config.layers.admin import AdminConfigLayer
    from vibe.core.config.orchestrator import ConfigOrchestrator

    admin = AdminConfigLayer()
    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema, layers=[admin], default_layer_resolver=lambda: admin
    )
    disabled = AdminConfigApplyResult(AdminConfigOutcome.DISABLED)

    assert await load_fetched_admin_config(orchestrator, disabled) == (disabled, "")


@pytest.mark.parametrize(
    ("saved", "expected"),
    [
        # The cached copy failed: local config.
        ("cached", "none"),
        # Nothing applied (no API key, nothing cached): local config's failure.
        (None, None),
    ],
)
@pytest.mark.parametrize("source", ["ensured", "cached"])
def test_next_source_never_builds_the_same_org_config_twice(
    source: str, saved: str | None, expected: str | None
) -> None:
    from vibe.app_server._admin_config import OrgConfigAttempt, OrgConfigSource

    attempt = OrgConfigAttempt(OrgConfigSource(source), saved=saved)

    assert attempt.next_source() == (
        None if expected is None else OrgConfigSource(expected)
    )


@pytest.mark.parametrize(
    ("fetch", "reported"),
    [
        (None, True),
        # A fetch that failed kept the cached copy: its failure is the one.
        ("fetch_failed", True),
        # Any other fetch failure is the one to report.
        ("parse_failed", False),
    ],
)
def test_a_cached_copy_failure_is_reported_unless_the_fetch_has_its_own(
    fetch: str | None, reported: bool
) -> None:
    from vibe.app_server._admin_config import OrgConfigAttempt, OrgConfigSource
    from vibe.core.config.admin_config import AdminConfigOutcome

    failure = AdminConfigApplyResult(AdminConfigOutcome.APPLY_FAILED, error="bad")
    attempt = OrgConfigAttempt(
        OrgConfigSource.ENSURED,
        fetch_outcome=(
            None
            if fetch is None
            else AdminConfigApplyResult(AdminConfigOutcome(fetch), error="x")
        ),
        saved_failure=failure,
    )

    assert attempt.cache_failure is (failure if reported else None)
