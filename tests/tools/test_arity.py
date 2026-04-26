from __future__ import annotations

from vibe.core.tools.arity import build_session_pattern


class TestBuildSessionPattern:
    def test_single_token_in_arity(self):
        assert build_session_pattern(["mkdir", "foo"]) == "mkdir *"

    def test_single_token_not_in_arity(self):
        assert build_session_pattern(["whoami"]) == "whoami *"

    def test_two_token_arity(self):
        assert build_session_pattern(["git", "checkout", "main"]) == "git checkout *"

    def test_three_token_arity(self):
        assert build_session_pattern(["npm", "run", "dev"]) == "npm run dev *"

    def test_longer_prefix_wins(self):
        # "git" is arity 2, but "git stash" is arity 3
        assert build_session_pattern(["git", "stash", "pop"]) == "git stash pop *"

    def test_docker_compose(self):
        assert (
            build_session_pattern(["docker", "compose", "up", "-d"])
            == "docker compose up *"
        )

    def test_empty_tokens(self):
        assert build_session_pattern([]) == ""

    def test_unknown_command_returns_first_token(self):
        assert build_session_pattern(["mycommand", "arg1", "arg2"]) == "mycommand *"

    def test_cat_is_arity_1(self):
        assert build_session_pattern(["cat", "file.txt"]) == "cat *"

    def test_rm_is_arity_1(self):
        assert build_session_pattern(["rm", "-rf", "dir"]) == "rm *"

    def test_uv_run(self):
        assert build_session_pattern(["uv", "run", "pytest"]) == "uv run pytest *"

    def test_pip_install(self):
        assert build_session_pattern(["pip", "install", "numpy"]) == "pip install *"

    def test_git_remote_add(self):
        assert (
            build_session_pattern(["git", "remote", "add", "origin", "url"])
            == "git remote add *"
        )

    def test_gh_pr_list(self):
        assert build_session_pattern(["gh", "pr", "list"]) == "gh pr list *"
