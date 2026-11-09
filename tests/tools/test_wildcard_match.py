from __future__ import annotations

from vibe.core.tools.models import ApprovedRule, PermissionScope
from vibe.core.tools.permissions import (
    PermissionStore,
    RequiredPermission,
    wildcard_match,
)


class TestWildcardMatch:
    def test_exact_match(self):
        assert wildcard_match("hello", "hello")

    def test_exact_no_match(self):
        assert not wildcard_match("hello", "world")

    def test_star_matches_any(self):
        assert wildcard_match("hello world", "hello *")

    def test_star_matches_empty_trailing(self):
        assert wildcard_match("mkdir", "mkdir *")

    def test_star_matches_with_args(self):
        assert wildcard_match("mkdir hello", "mkdir *")

    def test_star_matches_long_trailing(self):
        assert wildcard_match("git commit -m hello world", "git commit *")

    def test_star_in_middle(self):
        assert wildcard_match("fooXbar", "foo*bar")

    def test_question_mark_single_char(self):
        assert wildcard_match("cat", "c?t")

    def test_question_mark_no_match(self):
        assert not wildcard_match("ct", "c?t")

    def test_glob_path_pattern(self):
        assert wildcard_match("/tmp/dir/file.txt", "/tmp/dir/*")

    def test_glob_nested_path(self):
        assert wildcard_match("/tmp/dir/sub/file.txt", "/tmp/dir/*")

    def test_glob_no_match(self):
        assert not wildcard_match("/home/user/file.txt", "/tmp/*")

    def test_special_regex_chars_in_text(self):
        assert wildcard_match("echo (hello)", "echo *")

    def test_special_regex_chars_in_pattern(self):
        assert wildcard_match(".env", ".env")
        assert not wildcard_match("xenv", ".env")

    def test_fnmatch_character_class(self):
        assert wildcard_match("vache", "[bcghlmstv]ache")

    def test_empty_pattern_empty_text(self):
        assert wildcard_match("", "")

    def test_star_only(self):
        assert wildcard_match("anything goes here", "*")

    def test_trailing_space_star_is_optional(self):
        assert wildcard_match("ls", "ls *")
        assert wildcard_match("ls -la", "ls *")
        assert wildcard_match("ls -la /tmp", "ls *")

    def test_non_trailing_star_is_greedy(self):
        assert wildcard_match("abc123def", "abc*def")
        assert not wildcard_match("abc123de", "abc*def")


def _rule(session_pattern: str) -> ApprovedRule:
    return ApprovedRule(
        tool_name="bash",
        scope=PermissionScope.COMMAND_PATTERN,
        session_pattern=session_pattern,
    )


def _required(invocation_pattern: str, *, literal: bool) -> RequiredPermission:
    return RequiredPermission(
        scope=PermissionScope.COMMAND_PATTERN,
        invocation_pattern=invocation_pattern,
        session_pattern=invocation_pattern,
        label=invocation_pattern,
        literal=literal,
    )


class TestLiteralPermissions:
    def test_a_wide_rule_covers_a_pattern_permission(self):
        store = PermissionStore()
        store.add_rule(_rule("cat *"))

        assert store.covers("bash", _required("cat notes.txt", literal=False))

    def test_a_wide_rule_does_not_cover_a_literal_permission(self):
        store = PermissionStore()
        store.add_rule(_rule("cat *"))

        assert not store.covers(
            "bash", _required("cat notes.txt > /etc/cron.d/pwn", literal=True)
        )

    def test_a_literal_permission_is_covered_by_its_own_grant(self):
        store = PermissionStore()
        store.add_rule(_rule("cat notes.txt > /etc/cron.d/pwn"))

        assert store.covers(
            "bash", _required("cat notes.txt > /etc/cron.d/pwn", literal=True)
        )

    def test_a_literal_grant_does_not_read_its_own_glob_as_a_wildcard(self):
        store = PermissionStore()
        store.add_rule(_rule("git log *"))

        assert store.covers("bash", _required("git log *", literal=True))
        assert not store.covers("bash", _required("git log --ext-diff", literal=True))
