"""`/` in a source's tools searches names and descriptions; Esc leaves, then clears."""

from __future__ import annotations

from e2e.app_server.mcp import connector, handshake as mcp_handshake, tool
from e2e.app_server.scenario import Timeline, take_snapshot

handshake = mcp_handshake([
    connector(
        "github_app",
        tools=[
            tool("create_issue", "Open a GitHub issue"),
            tool("list_issues", "List issues of a repository"),
            tool("merge_pull_request", "Merge a pull request once it is approved"),
            tool("search_code", "Search code across every repository"),
        ],
    )
])

timeline: Timeline = ["/mcp github_app\r", "/", "repository", take_snapshot()]

screen_contains = ("list_issues", "search_code", "/  repository")
screen_excludes = ("create_issue", "merge_pull_request")
