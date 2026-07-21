from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.tools.base import BaseToolState, ToolPermission
from vibe.core.tools.builtins.bash import (
    Bash,
    BashArgs,
    BashToolConfig,
    _collect_outside_dirs,
    _extract_redirect_targets,
)
from vibe.core.tools.builtins.experimental_bash import (
    _extract_redirect_targets as _extract_redirect_targets_experimental,
)
from vibe.core.tools.permissions import PermissionScope
from vibe.core.utils import is_windows

pytestmark = pytest.mark.skipif(
    is_windows(), reason="redirect analysis applies to POSIX shell semantics"
)


class TestExtractRedirectTargets:
    def test_simple_overwrite_redirect(self) -> None:
        assert _extract_redirect_targets("cat > /tmp/out.txt") == ["/tmp/out.txt"]

    def test_append_redirect(self) -> None:
        assert _extract_redirect_targets("echo hi >> notes.txt") == ["notes.txt"]

    def test_heredoc_with_file_redirect(self) -> None:
        command = "cat > /parent/file.py << 'EOF'\nprint('hi')\nEOF"
        assert _extract_redirect_targets(command) == ["/parent/file.py"]

    def test_stderr_dup_and_dev_null_have_targets_but_are_skipped_later(self) -> None:
        targets = _extract_redirect_targets("cmd > /dev/null 2>&1")
        assert "/dev/null" in targets

    def test_no_redirect_yields_nothing(self) -> None:
        assert _extract_redirect_targets("ls -la /tmp") == []

    def test_input_redirect_is_not_a_write_target(self) -> None:
        assert _extract_redirect_targets("cat < /etc/hosts") == []

    def test_stderr_redirect_is_collected(self) -> None:
        assert _extract_redirect_targets("make 2> err.log") == ["err.log"]

    def test_ampersand_redirect_is_collected(self) -> None:
        assert _extract_redirect_targets("python3 x.py &> all.log") == ["all.log"]

    def test_process_substitution_is_not_a_write_target(self) -> None:
        assert _extract_redirect_targets("tee log > >(grep x)") == []
        assert _extract_redirect_targets("diff <(ls a) <(ls b)") == []

    def test_herestring_yields_nothing(self) -> None:
        assert _extract_redirect_targets("sort <<< 'b\na'") == []

    def test_read_write_redirect_is_collected(self) -> None:
        assert _extract_redirect_targets("echo x 1<> /tmp/evil") == ["/tmp/evil"]
        assert _extract_redirect_targets("cmd <> /tmp/evil") == ["/tmp/evil"]

    def test_experimental_bash_read_write_redirect_matches(self) -> None:
        assert _extract_redirect_targets_experimental("echo x 1<> /tmp/evil") == [
            "/tmp/evil"
        ]

    def test_experimental_bash_extraction_matches(self) -> None:
        command = "cat > /parent/file.py << 'EOF'\nx\nEOF"
        assert _extract_redirect_targets_experimental(command) == ["/parent/file.py"]


class TestCollectOutsideDirsRedirects:
    def test_redirect_outside_workdir_is_collected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        dirs = _collect_outside_dirs(
            ["cat <redirect>"], ["/private/tmp/parent/file.py"]
        )

        assert dirs
        assert any("parent" in d for d in dirs)

    def test_redirect_inside_workdir_is_allowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        assert _collect_outside_dirs(["cat <redirect>"], ["out.txt"]) == set()

    def test_dev_null_and_fd_dup_are_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        dirs = _collect_outside_dirs(["cmd <redirect>"], ["/dev/null", "&1", "1"])

        assert dirs == set()

    def test_variable_target_requires_approval(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        dirs = _collect_outside_dirs(["cat <redirect>"], ["$HOME/evil.txt"])

        assert dirs == {"$HOME/evil.txt"}


class TestBashResolvePermissionWithRedirects:
    def make_bash(self) -> Bash:
        return Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())

    def test_allowlisted_command_redirecting_outside_workdir_asks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workdir = tmp_path / "wt"
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        outside = tmp_path / "parent" / "file.py"
        bash = self.make_bash()

        ctx = bash.resolve_permission(
            BashArgs(command=f"cat > {outside} << 'EOF'\nx\nEOF")
        )

        assert ctx is not None
        assert ctx.permission is ToolPermission.ASK
        assert any(
            rp.scope is PermissionScope.OUTSIDE_DIRECTORY
            for rp in ctx.required_permissions
        )

    def test_allowlisted_command_redirecting_inside_workdir_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workdir = tmp_path / "wt"
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        bash = self.make_bash()

        ctx = bash.resolve_permission(
            BashArgs(command="cat > out.txt << 'EOF'\nx\nEOF")
        )

        assert ctx is not None
        assert ctx.permission is ToolPermission.ALWAYS

    def test_redirect_to_dev_null_stays_allowlisted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        bash = self.make_bash()

        ctx = bash.resolve_permission(BashArgs(command="ls . > /dev/null 2>&1"))

        assert ctx is not None
        assert ctx.permission is ToolPermission.ALWAYS

    def test_read_write_redirect_outside_workdir_asks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workdir = tmp_path / "wt"
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        outside = tmp_path / "parent" / "evil.txt"
        bash = self.make_bash()

        ctx = bash.resolve_permission(BashArgs(command=f"echo x 1<> {outside}"))

        assert ctx is not None
        assert ctx.permission is ToolPermission.ASK
        assert any(
            rp.scope is PermissionScope.OUTSIDE_DIRECTORY
            for rp in ctx.required_permissions
        )
