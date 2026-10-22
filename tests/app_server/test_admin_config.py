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
    ("source", "saved", "fetched", "expected"),
    [
        # The fetch failed, so the cached copy already failed: local config.
        ("fetched", "cached", None, "none"),
        # The answer failed and differs from the cached copy: try that copy.
        ("fetched", "cached", "answer", "cached"),
        # ... but not when the answer is the cached copy.
        ("fetched", "same", "same", "none"),
        ("fetched", None, "answer", "none"),
        # "No managed config": nothing applied, never the revoked cached copy.
        ("fetched", "cached", "", None),
        # Nothing applied (no API key, nothing cached): local config's failure.
        ("fetched", None, None, None),
        ("cached", "cached", None, "none"),
        ("cached", None, None, None),
        ("none", None, None, None),
    ],
)
def test_next_source_never_builds_the_same_org_config_twice(
    source: str, saved: str | None, fetched: str | None, expected: str | None
) -> None:
    from vibe.app_server._admin_config import OrgConfigAttempt, OrgConfigSource

    attempt = OrgConfigAttempt(OrgConfigSource(source), saved=saved, fetched=fetched)

    assert attempt.next_source() == (
        None if expected is None else OrgConfigSource(expected)
    )


@pytest.mark.parametrize(
    ("source", "fetched", "fetch", "reported"),
    [
        # Nothing replaced the cached copy: its failure is the one to report.
        ("cached", None, None, True),
        ("fetched", None, "fetch_failed", True),
        # A fetched answer replaced it, or "no managed config" revoked it.
        ("fetched", "answer", "applied", False),
        ("fetched", "", "disabled", False),
        # The fetched answer failed too: that failure is the one to report.
        ("fetched", None, "apply_failed", False),
    ],
)
def test_a_cached_copy_failure_is_reported_only_when_nothing_replaced_it(
    source: str, fetched: str | None, fetch: str | None, reported: bool
) -> None:
    from vibe.app_server._admin_config import OrgConfigAttempt, OrgConfigSource
    from vibe.core.config.admin_config import AdminConfigOutcome

    failure = AdminConfigApplyResult(AdminConfigOutcome.APPLY_FAILED, error="bad")
    attempt = OrgConfigAttempt(
        OrgConfigSource(source),
        fetched=fetched,
        fetch_outcome=(
            None
            if fetch is None
            else AdminConfigApplyResult(AdminConfigOutcome(fetch), error="x")
        ),
        saved_failure=failure,
    )

    assert attempt.cache_failure is (failure if reported else None)
