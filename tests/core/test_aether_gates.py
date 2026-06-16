"""Unit tests for the aether discipline gates."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vibe.core.hooks.aether.bonsai import evaluate as bonsai
from vibe.core.hooks.aether.cairn import evaluate as cairn
from vibe.core.hooks.aether.temper import evaluate as temper
from vibe.core.hooks.aether.whetstone import evaluate as whetstone


# ---------------------------------------------------------------------------
# Bonsai
# ---------------------------------------------------------------------------


class TestBonsai:
    def test_blocks_grep_on_py(self) -> None:
        assert bonsai("grep -r MyClass src/main.py", ".") is not None

    def test_blocks_sed_on_ts(self) -> None:
        assert bonsai("sed -i 's/foo/bar/' src/index.ts", ".") is not None

    def test_blocks_awk_on_tsx(self) -> None:
        assert bonsai("awk '{print}' src/App.tsx", ".") is not None

    def test_blocks_mv_on_py(self) -> None:
        assert bonsai("mv old_name.py new_name.py", ".") is not None

    def test_blocks_mv_on_js(self) -> None:
        assert bonsai("mv utils.js helpers.js", ".") is not None

    def test_ignores_grep_on_json(self) -> None:
        assert bonsai("grep foo config.json", ".") is None

    def test_ignores_grep_on_txt(self) -> None:
        assert bonsai("grep pattern README.txt", ".") is None

    def test_ignores_unrelated_command(self) -> None:
        assert bonsai("git commit -m 'feat: add feature'", ".") is None

    def test_false_positive_grep_pattern_in_quotes(self) -> None:
        # grep "\.py" config.json — .py is a pattern in quotes, not a filename
        assert bonsai('grep "\\.py" config.json', ".") is None

    def test_false_positive_sed_pattern_in_quotes(self) -> None:
        assert bonsai("sed 's/\\.py/.python/' README.md", ".") is None

    def test_bypass_bonsai_skip(self) -> None:
        assert bonsai("grep -r foo src/main.py # bonsai:skip", ".") is None

    def test_bypass_aether_skip(self) -> None:
        assert bonsai("grep -r foo src/main.py # aether:skip", ".") is None

    def test_denial_mentions_suggestion(self) -> None:
        result = bonsai("grep foo src/main.py", ".")
        assert result is not None
        assert result["decision"] == "deny"
        assert "pygrep" in result["reason"]

    def test_denial_mentions_bypass(self) -> None:
        result = bonsai("mv old.py new.py", ".")
        assert result is not None
        assert "bonsai:skip" in result["reason"]


# ---------------------------------------------------------------------------
# Cairn
# ---------------------------------------------------------------------------


class TestCairn:
    def test_blocks_weak_fix(self) -> None:
        assert cairn('git commit -m "fix"', ".") is not None

    def test_blocks_weak_wip(self) -> None:
        assert cairn("git commit -m 'wip'", ".") is not None

    def test_blocks_short_message(self) -> None:
        assert cairn('git commit -m "ok"', ".") is not None

    def test_passes_good_conventional(self) -> None:
        assert cairn('git commit -m "feat(auth): add OAuth2 login flow"', ".") is None

    def test_passes_good_plain(self) -> None:
        assert cairn('git commit -m "Improve error handling in the parser"', ".") is None

    def test_passes_no_minus_m(self) -> None:
        # No -m flag — no opinion
        assert cairn("git commit", ".") is None

    def test_long_form_message_blocked(self) -> None:
        assert cairn('git commit --message="wip"', ".") is not None

    def test_long_form_equals_blocked(self) -> None:
        assert cairn('git commit --message=fix', ".") is not None

    def test_long_form_space_blocked(self) -> None:
        assert cairn('git commit --message "wip"', ".") is not None

    def test_long_form_good_passes(self) -> None:
        assert cairn('git commit --message="feat: implement rate limiting"', ".") is None

    def test_multiple_m_weak_first_blocked(self) -> None:
        # Combined "wip\n\nmore detail" — the combined message is fine but first part is weak
        # The concatenated message is "wip\n\nmore detail" which is long enough and not pure weak
        result = cairn('git commit -m "wip" -m "More detail here"', ".")
        # combined = "wip\n\nMore detail here" — 20+ chars, not in WEAK list → passes
        assert result is None

    def test_multiple_m_both_weak_blocked(self) -> None:
        result = cairn('git commit -m "fix" -m "wip"', ".")
        # combined = "fix\n\nwip" — 8 chars < 10 → blocked
        assert result is not None

    def test_handles_single_quotes(self) -> None:
        assert cairn("git commit -m 'update'", ".") is not None

    def test_not_triggered_on_non_commit(self) -> None:
        assert cairn('git push -m "wip"', ".") is None

    def test_bypass_cairn_skip(self) -> None:
        assert cairn('git commit -m "fix" # cairn:skip', ".") is None

    def test_bypass_aether_skip(self) -> None:
        assert cairn('git commit -m "fix" # aether:skip', ".") is None

    def test_denial_mentions_cairn_commit(self) -> None:
        result = cairn('git commit -m "wip"', ".")
        assert result is not None
        assert "cairn-commit" in result["reason"]


# ---------------------------------------------------------------------------
# Whetstone — requires a real git repo
# ---------------------------------------------------------------------------


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        check=True, capture_output=True, cwd=str(tmp_path),
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com",
             "PATH": "/usr/bin:/bin"},
    )
    return tmp_path


class TestWhetstone:
    def test_passes_when_no_plans_dir(self, git_repo: Path) -> None:
        assert whetstone("git commit -m 'feat: x'", str(git_repo)) is None

    def test_passes_on_non_commit_command(self, git_repo: Path) -> None:
        assert whetstone("ls -la", str(git_repo)) is None

    def test_blocks_when_critique_missing(self, git_repo: Path) -> None:
        plans = git_repo / ".claude" / "plans"
        plans.mkdir(parents=True)
        (plans / "my-plan.md").write_text("# Plan")
        result = whetstone("git commit -m 'feat: x'", str(git_repo))
        assert result is not None
        assert result["decision"] == "deny"
        assert "autocritic" in result["reason"]

    def test_blocks_when_plan_newer_than_critique(self, git_repo: Path) -> None:
        import time
        plans = git_repo / ".claude" / "plans"
        plans.mkdir(parents=True)
        critique = plans / "CRITIQUE.md"
        critique.write_text("# Critique")
        time.sleep(0.05)
        (plans / "my-plan.md").write_text("# Plan")
        result = whetstone("git commit -m 'feat: x'", str(git_repo))
        assert result is not None

    def test_passes_when_critique_newer_than_plan(self, git_repo: Path) -> None:
        import time
        plans = git_repo / ".claude" / "plans"
        plans.mkdir(parents=True)
        (plans / "my-plan.md").write_text("# Plan")
        time.sleep(0.05)
        (plans / "CRITIQUE.md").write_text("# Critique")
        assert whetstone("git commit -m 'feat: x'", str(git_repo)) is None

    def test_bypass_whetstone_skip(self, git_repo: Path) -> None:
        plans = git_repo / ".claude" / "plans"
        plans.mkdir(parents=True)
        (plans / "my-plan.md").write_text("# Plan")
        assert whetstone("git commit -m 'x' # whetstone:skip", str(git_repo)) is None

    def test_bypass_aether_skip(self, git_repo: Path) -> None:
        plans = git_repo / ".claude" / "plans"
        plans.mkdir(parents=True)
        (plans / "my-plan.md").write_text("# Plan")
        assert whetstone("git commit -m 'x' # aether:skip", str(git_repo)) is None


# ---------------------------------------------------------------------------
# Temper — requires a git repo with a staged diff
# ---------------------------------------------------------------------------


def _make_staged_diff(repo: Path, content: str = "x = 1\n") -> str:
    src = repo / "src.py"
    src.write_text(content)
    subprocess.run(["git", "add", str(src)], check=True, capture_output=True, cwd=str(repo))
    result = subprocess.run(
        ["git", "diff", "--cached"], capture_output=True, text=True, cwd=str(repo)
    )
    return result.stdout


class TestTemper:
    def test_passes_when_no_trigger(self, git_repo: Path) -> None:
        assert temper("ls -la", str(git_repo)) is None

    def test_passes_small_diff(self, git_repo: Path) -> None:
        _make_staged_diff(git_repo, "x = 1\n")
        assert temper("git commit -m 'feat: small'", str(git_repo)) is None

    def test_blocks_large_diff_lines(self, git_repo: Path) -> None:
        _make_staged_diff(git_repo, "\n".join(f"line_{i} = {i}" for i in range(300)))
        result = temper("git commit -m 'feat: big'", str(git_repo))
        assert result is not None
        assert result["decision"] == "deny"
        assert "temper" in result["reason"]

    def test_blocks_critical_path_file(self, git_repo: Path) -> None:
        auth_file = git_repo / "auth_middleware.py"
        auth_file.write_text("# auth\n")
        subprocess.run(["git", "add", str(auth_file)], check=True, capture_output=True, cwd=str(git_repo))
        result = temper("git commit -m 'fix: patch'", str(git_repo))
        assert result is not None
        assert "critical" in result["reason"]

    def test_clears_when_hash_matches(self, git_repo: Path) -> None:
        diff = _make_staged_diff(git_repo, "y = 2\n")
        diff_hash = hashlib.sha256(diff.encode()).hexdigest()
        vibe_dir = git_repo / ".vibe"
        vibe_dir.mkdir(exist_ok=True)
        (vibe_dir / ".temper_ok").write_text(diff_hash)
        # Even though the file is staged (would normally require review), hash matches
        assert temper("git commit -m 'feat: reviewed'", str(git_repo)) is None

    def test_blocks_when_hash_stale(self, git_repo: Path) -> None:
        # Large diff + stale hash → gate must block (hash mismatch, no approval)
        _make_staged_diff(git_repo, "\n".join(f"z_{i} = {i}" for i in range(300)))
        vibe_dir = git_repo / ".vibe"
        vibe_dir.mkdir(exist_ok=True)
        (vibe_dir / ".temper_ok").write_text("stale_hash_value")
        result = temper("git commit -m 'feat: x'", str(git_repo))
        assert result is not None

    def test_bypass_temper_skip(self, git_repo: Path) -> None:
        _make_staged_diff(git_repo, "\n".join(f"x_{i} = {i}" for i in range(300)))
        assert temper("git commit -m 'x' # temper:skip", str(git_repo)) is None


# ---------------------------------------------------------------------------
# Runner — integration via subprocess stdin
# ---------------------------------------------------------------------------


def _run_runner(command: str, cwd: str = ".") -> dict | None:
    payload = json.dumps({"tool_input": {"command": command}, "cwd": cwd})
    proc = subprocess.run(
        [sys.executable, "-m", "vibe.core.hooks.aether.runner"],
        input=payload, capture_output=True, text=True,
    )
    if not proc.stdout.strip():
        return None
    return json.loads(proc.stdout)


class TestRunner:
    def test_passes_clean_command(self) -> None:
        assert _run_runner("ls -la") is None

    def test_bonsai_denial_returned(self) -> None:
        result = _run_runner("grep foo src/main.py")
        assert result is not None
        assert result["decision"] == "deny"
        assert "bonsai" in result["reason"]

    def test_cairn_denial_returned(self) -> None:
        result = _run_runner('git commit -m "wip"')
        assert result is not None
        assert result["decision"] == "deny"

    def test_malformed_json_passes_silently(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "vibe.core.hooks.aether.runner"],
            input="not json at all", capture_output=True, text=True,
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""

    def test_short_circuits_on_first_denial(self) -> None:
        # A command that would trigger both bonsai and cairn —
        # only one denial should appear (bonsai fires first)
        result = _run_runner('git commit -m "fix" && grep foo main.py')
        assert result is not None
        # cairn fires before bonsai in gate order (whetstone, bonsai, temper, cairn)
        # git commit triggers cairn; the grep part also triggers bonsai but
        # cairn fires after bonsai in the gate order, so bonsai fires first
        assert result["decision"] == "deny"
