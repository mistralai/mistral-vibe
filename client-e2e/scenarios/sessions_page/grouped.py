"""Sessions from several folders group by project, the current project first.

Two folders sharing a name show enough of their path to tell them apart, and a
row in a linked worktree or subfolder says where; `g` lists a folder gone from disk.
"""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.sessions_page._sessions import listed, session

# The details pane wraps a long folder in full.
_LONG_FOLDER = "/projects/app-old/packages/very-long-workspace-name/vibe"

handshake = listed(
    session(
        1,
        "Current work",
        model="devstral-2",
        reasoningEffort="high",
        tokenUsage={"inputTokens": 95_300, "outputTokens": 781, "totalTokens": 96_081},
    ),
    session(2, "Docs refresh", "/projects/website"),
    session(3, "Website plan", "/projects/website"),
    session(4, "Older work"),
    session(5, "Failed run", "/projects/tools", status={"type": "failed"}),
    session(6, "Unread answer", "/projects/tools", isUnseen=True),
    session(
        7,
        "Worktree fix",
        "/projects/app-feature/vibe",
        repositoryRoot="/projects/app",
        checkoutRoot="/projects/app-feature",
    ),
    session(
        8,
        "Removed worktree",
        _LONG_FOLDER,
        repositoryRoot="/projects/app",
        checkoutRoot="/projects/app-old",
        cwdMissing=True,
    ),
    session(9, "Old checkout A", "/archive/one/vibe", repositoryRoot=None),
    session(10, "Old checkout B", "/archive/two/vibe", repositoryRoot=None),
)

timeline: Timeline = [
    "/sessions",
    "\r",
    take_snapshot(),
    "g",
    "jjjjjjj",
    take_snapshot("removed_worktree"),
]
