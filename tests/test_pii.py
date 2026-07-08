from __future__ import annotations

import pytest

from vibe.core.pii import scrub_paths


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # POSIX / home / Windows absolute paths collapse to their filename.
        ("boom at /Users/rk/.local/bin/vibe", "boom at [Filtered]/vibe"),
        ("failed in /home/rk/project/x.py", "failed in [Filtered]/x.py"),
        ("/Users/rk/app/_markdown.py", "[Filtered]/_markdown.py"),
        ("~/foo/bar", "[Filtered]/bar"),
        (r"C:\Users\rk\app\_markdown.py", "[Filtered]/_markdown.py"),
        ("C:/Users/rk/app/_markdown.py", "[Filtered]/_markdown.py"),
        # Spaced interior segments (usernames, "Application Support") are scrubbed.
        ("/Users/John Doe/app/_markdown.py", "[Filtered]/_markdown.py"),
        (
            "/Users/rk/Library/Application Support/vibe/config.toml",
            "[Filtered]/config.toml",
        ),
        (r"C:\Users\John Doe\app\_markdown.py", "[Filtered]/_markdown.py"),
        # Paths immediately following a colon are still scrubbed.
        ("path:/Users/me/app.py", "path:[Filtered]/app.py"),
        ("note:~/secrets/file.txt", "note:[Filtered]/file.txt"),
        # Bare home directories leak the username as the final segment; filter it too.
        ("Permission denied: /Users/johndoe", "Permission denied: [Filtered]"),
        ("home dir /home/johndoe", "home dir [Filtered]"),
        (r"C:\Users\johndoe", "[Filtered]"),
        ("HOME=/Users/johndoe", "HOME=[Filtered]"),
        ("boom /Users/johndoe here", "boom [Filtered] here"),
        ("quoted '/Users/johndoe'", "quoted '[Filtered]'"),
        # A trailing separator on a bare home dir must not re-leak the username.
        ("cwd /Users/johndoe/", "cwd [Filtered]/"),
        ("/home/johndoe/ then", "[Filtered]/ then"),
        ("C:\\Users\\johndoe\\", "[Filtered]\\"),
        # A trailing separator followed by more path still collapses normally.
        ("/Users/johndoe/app/file.py", "[Filtered]/file.py"),
        # Trailing prose after the filename is never swallowed.
        ("boom at /Users/rk/x.py failed here", "boom at [Filtered]/x.py failed here"),
        (
            "err at /home/rk/secret project/notes.txt done",
            "err at [Filtered]/notes.txt done",
        ),
        # URLs, relative fragments and numeric sequences are left untouched.
        (
            "see https://example.com/api/v2/users",
            "see https://example.com/api/v2/users",
        ),
        ("GET http://foo.com/a/b failed", "GET http://foo.com/a/b failed"),
        ("module vibe/core/sentry.py line 5", "module vibe/core/sentry.py line 5"),
        ("value 1/2/3", "value 1/2/3"),
        ("error 10:30/12/2024 weird", "error 10:30/12/2024 weird"),
        ("url git@github.com:org/repo.git", "url git@github.com:org/repo.git"),
        ("single /etc file", "single /etc file"),
    ],
)
def test_scrub_paths(value: str, expected: str):
    assert scrub_paths(value) == expected


def test_scrub_paths_recurses_into_containers():
    value = {"argv": ["/Users/rk/.local/bin/vibe", "--flag"], "n": 1, "t": ("/a/b/c",)}
    assert scrub_paths(value) == {
        "argv": ["[Filtered]/vibe", "--flag"],
        "n": 1,
        "t": ("[Filtered]/c",),
    }
