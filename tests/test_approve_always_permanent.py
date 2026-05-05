from __future__ import annotations

from pathlib import Path
import tomllib

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from vibe.core.tools.permissions import PermissionScope, RequiredPermission


def _read_persisted_config(config_dir: Path) -> dict:
    config_file = config_dir / "config.toml"
    with config_file.open("rb") as f:
        return tomllib.load(f)


class TestApproveAlwaysPermanentNoGranularPermissions:
    def test_sets_tool_permission_always_in_config(self, config_dir: Path):
        agent = build_test_agent_loop()

        agent.approve_always("bash", None, save_permanently=True)

        persisted = _read_persisted_config(config_dir)
        assert persisted["tools"]["bash"]["permission"] == "always"

    def test_session_only_does_not_persist(self, config_dir: Path):
        agent = build_test_agent_loop()

        agent.approve_always("bash", None, save_permanently=False)

        assert agent.config.tools["bash"]["permission"] == "always"
        persisted = _read_persisted_config(config_dir)
        assert "bash" not in persisted.get("tools", {})


class TestApproveAlwaysPermanentWithGranularPermissions:
    def _make_permissions(self) -> list[RequiredPermission]:
        return [
            RequiredPermission(
                scope=PermissionScope.COMMAND_PATTERN,
                invocation_pattern="npm install foo",
                session_pattern="npm install *",
                label="npm install *",
            )
        ]

    def test_persists_allowlist_to_config(self, config_dir: Path):
        agent = build_test_agent_loop()
        perms = self._make_permissions()

        agent.approve_always("bash", perms, save_permanently=True)

        persisted = _read_persisted_config(config_dir)
        assert persisted["tools"]["bash"]["allowlist"] == ["npm install *"]

    def test_also_adds_session_rules(self, config_dir: Path):
        agent = build_test_agent_loop()
        perms = self._make_permissions()

        agent.approve_always("bash", perms, save_permanently=True)

        assert len(agent._session_rules) == 1
        rule = agent._session_rules[0]
        assert rule.tool_name == "bash"
        assert rule.scope == PermissionScope.COMMAND_PATTERN
        assert rule.session_pattern == "npm install *"

    def test_session_only_does_not_persist_allowlist(self, config_dir: Path):
        agent = build_test_agent_loop()
        perms = self._make_permissions()

        agent.approve_always("bash", perms, save_permanently=False)

        assert len(agent._session_rules) == 1
        persisted = _read_persisted_config(config_dir)
        assert "bash" not in persisted.get("tools", {})

    def test_does_not_duplicate_existing_allowlist_entries(self, config_dir: Path):
        config = build_test_vibe_config(
            tools={"bash": {"allowlist": ["npm install *"]}}
        )
        agent = build_test_agent_loop(config=config)
        perms = self._make_permissions()

        agent.approve_always("bash", perms, save_permanently=True)

        persisted = _read_persisted_config(config_dir)
        # Pattern already existed -- nothing new should be written
        assert persisted.get("tools", {}).get("bash", {}).get("allowlist") is None

    def test_appends_new_patterns_to_existing_allowlist(self, config_dir: Path):
        config = build_test_vibe_config(tools={"bash": {"allowlist": ["git *"]}})
        agent = build_test_agent_loop(config=config)
        perms = self._make_permissions()

        agent.approve_always("bash", perms, save_permanently=True)

        persisted = _read_persisted_config(config_dir)
        assert persisted["tools"]["bash"]["allowlist"] == ["git *", "npm install *"]

    def test_multiple_permissions_persisted(self, config_dir: Path):
        agent = build_test_agent_loop()
        perms = [
            RequiredPermission(
                scope=PermissionScope.COMMAND_PATTERN,
                invocation_pattern="npm install foo",
                session_pattern="npm install *",
                label="npm install *",
            ),
            RequiredPermission(
                scope=PermissionScope.OUTSIDE_DIRECTORY,
                invocation_pattern="/tmp/newdir",
                session_pattern="/tmp/*",
                label="/tmp/*",
            ),
        ]

        agent.approve_always("bash", perms, save_permanently=True)

        persisted = _read_persisted_config(config_dir)
        assert persisted["tools"]["bash"]["allowlist"] == ["/tmp/*", "npm install *"]
