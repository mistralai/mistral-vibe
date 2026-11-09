from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys
from typing import Any

import tomli_w

from vibe.core.hooks.config import load_hooks_file, load_hooks_from_fs
from vibe.core.hooks.models import HookType


def _write_hooks_toml(path: Path, hooks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        tomli_w.dump({"hooks": hooks}, f)


def _emit_cmd(payload: dict[str, Any]) -> str:
    """Build a shell command that prints ``payload`` as JSON to stdout.

    Uses Python (over ``printf``) so embedded quotes / shell metacharacters
    in field values are not a footgun, and ``shlex.quote`` to give the
    shell a single safe argument to pass to ``-c``.
    """
    body = f"import sys; sys.stdout.write({json.dumps(payload)!r})"
    return f"{sys.executable} -c {shlex.quote(body)}"


class TestConfigLoading:
    def test_load_from_global_file(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "lint", "type": HookType.POST_AGENT, "command": "echo lint"}],
        )
        result = load_hooks_from_fs()
        assert len(result.hooks) == 1
        assert result.hooks[0].name == "lint"
        assert result.issues == []

    def test_load_from_both_global_and_project(
        self, config_dir: Path, tmp_working_directory: Path
    ) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "global-hook", "type": "post_agent", "command": "echo global"}],
        )
        project_vibe = tmp_working_directory / ".vibe"
        _write_hooks_toml(
            project_vibe / "hooks.toml",
            [{"name": "project-hook", "type": "post_agent", "command": "echo project"}],
        )
        from vibe.core.trusted_folders import trusted_folders_manager

        trusted_folders_manager.add_trusted(tmp_working_directory)

        result = load_hooks_from_fs()
        assert len(result.hooks) == 2
        names = [h.name for h in result.hooks]
        # Project hooks are loaded first.
        assert names == ["project-hook", "global-hook"]

    def test_project_file_skipped_when_untrusted(
        self, tmp_working_directory: Path
    ) -> None:
        project_vibe = tmp_working_directory / ".vibe"
        _write_hooks_toml(
            project_vibe / "hooks.toml",
            [{"name": "sneaky-hook", "type": "post_agent", "command": "echo sneaky"}],
        )
        result = load_hooks_from_fs()
        assert not any(h.name == "sneaky-hook" for h in result.hooks)

    def test_duplicate_hook_name_project_wins(
        self, config_dir: Path, tmp_working_directory: Path
    ) -> None:
        # Project file loads first; the user-global duplicate is flagged.
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "dup-hook", "type": "post_agent", "command": "echo global"}],
        )
        project_vibe = tmp_working_directory / ".vibe"
        _write_hooks_toml(
            project_vibe / "hooks.toml",
            [{"name": "dup-hook", "type": "post_agent", "command": "echo project"}],
        )
        from vibe.core.trusted_folders import trusted_folders_manager

        trusted_folders_manager.add_trusted(tmp_working_directory)

        result = load_hooks_from_fs()
        assert len(result.hooks) == 1
        assert result.hooks[0].command == "echo project"
        assert any("Duplicate" in i.message for i in result.issues)

    def test_toml_parse_error_reported(self, config_dir: Path) -> None:
        hooks_file = config_dir / "hooks.toml"
        hooks_file.write_text("this is not valid toml [[[", encoding="utf-8")
        result = load_hooks_from_fs()
        assert result.hooks == []
        assert len(result.issues) == 1
        assert (
            "parse" in result.issues[0].message.lower()
            or "Failed" in result.issues[0].message
        )

    def test_validation_error_reported(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "bad", "type": "InvalidType", "command": "echo"}],
        )
        result = load_hooks_from_fs()
        assert result.hooks == []
        assert len(result.issues) == 1

    def test_missing_command_reported(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml", [{"name": "no-cmd", "type": "post_agent"}]
        )
        result = load_hooks_from_fs()
        assert result.hooks == []
        assert len(result.issues) == 1

    def test_empty_command_reported(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "empty-cmd", "type": "post_agent", "command": "   "}],
        )
        result = load_hooks_from_fs()
        assert result.hooks == []
        assert len(result.issues) == 1

    def test_backslash_command_is_loaded(self, config_dir: Path) -> None:
        # Both executors run the command through a shell, so a backslash path is
        # the shell's to interpret -- the loader must not pre-emptively drop it.
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [
                {"name": "good-hook", "type": "post_agent", "command": "echo ok"},
                {
                    "name": "win-hook",
                    "type": "post_agent",
                    "command": r"C:\Tools\my-hook.exe --flag value",
                },
            ],
        )
        result = load_hooks_from_fs()
        assert [hook.name for hook in result.hooks] == ["good-hook", "win-hook"]
        assert result.issues == []

    def test_forward_slash_command_no_warning(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [
                {
                    "name": "ok-hook",
                    "type": "post_agent",
                    "command": "C:/tools/my-hook.exe --flag value",
                }
            ],
        )
        result = load_hooks_from_fs()
        assert len(result.hooks) == 1
        assert result.issues == []

    def test_default_timeout_is_uniform(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [
                {"name": "p", "type": "post_agent", "command": "echo ok"},
                {"name": "b", "type": "pre_tool", "command": "echo ok"},
                {"name": "a", "type": "post_tool", "command": "echo ok"},
            ],
        )
        result = load_hooks_from_fs()
        assert all(h.timeout == 60.0 for h in result.hooks)

    def test_explicit_timeout_overrides_default(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "b", "type": "pre_tool", "command": "echo ok", "timeout": 12.5}],
        )
        result = load_hooks_from_fs()
        assert result.hooks[0].timeout == 12.5

    def test_match_field_on_tool_hooks(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [
                {
                    "name": "b",
                    "type": "pre_tool",
                    "command": "echo ok",
                    "match": "bash",
                },
                {
                    "name": "a",
                    "type": "post_tool",
                    "command": "echo ok",
                    "match": "re:read_.*",
                },
            ],
        )
        result = load_hooks_from_fs()
        assert result.hooks[0].match == "bash"
        assert result.hooks[1].match == "re:read_.*"

    def test_match_field_selects_agents_on_post_agent(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [
                {
                    "name": "h",
                    "type": "post_agent",
                    "command": "echo ok",
                    "match": "main",
                }
            ],
        )
        result = load_hooks_from_fs()
        assert result.issues == []
        assert result.hooks[0].match == "main"

    def test_strict_still_rejected_on_post_agent(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [
                {
                    "name": "h",
                    "type": "post_agent",
                    "command": "echo ok",
                    "match": "main",
                    "strict": True,
                }
            ],
        )
        result = load_hooks_from_fs()
        assert result.hooks == []
        assert any("strict" in i.message for i in result.issues)

    def test_empty_match_rejected(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "h", "type": "pre_tool", "command": "echo ok", "match": "   "}],
        )
        result = load_hooks_from_fs()
        assert result.hooks == []

    def test_nonexistent_file_returns_empty(self, tmp_path: Path) -> None:
        result = load_hooks_file(tmp_path / "missing.toml")
        assert result.hooks == []
        assert result.issues == []
