"""ChefChat Mode Constants
========================

All constants, mappings, and configurations for the mode system.
Extracted from mode_manager.py for better organization.

This module contains:
- Mode cycle order
- Emoji, description, personality, and tip mappings
- Tool categorization (read-only vs write)
- Bash command security patterns
- Pre-built MODE_CONFIGS dictionary
"""

from __future__ import annotations

from chefchat.modes.types import ModeConfig, VibeMode

# =============================================================================
# MODE METADATA
# =============================================================================

# Mode cycle order for Shift+Tab
MODE_CYCLE_ORDER: tuple[VibeMode, ...] = (
    VibeMode.NORMAL,
    VibeMode.AUTO,
    VibeMode.PLAN,
    VibeMode.YOLO,
    VibeMode.ARCHITECT,
)

# Emoji indicators for each mode
MODE_EMOJIS: dict[VibeMode, str] = {
    VibeMode.PLAN: "📋",
    VibeMode.NORMAL: "✋",
    VibeMode.AUTO: "⚡",
    VibeMode.YOLO: "🚀",
    VibeMode.ARCHITECT: "🏛️",
}

# Short descriptions for each mode
MODE_DESCRIPTIONS: dict[VibeMode, str] = {
    VibeMode.PLAN: "Research & Planning - Read only until approved",
    VibeMode.NORMAL: "Ask confirmation before each tool execution",
    VibeMode.AUTO: "Auto-approve all tool executions",
    VibeMode.YOLO: "Maximum speed, minimal output, auto-approve all",
    VibeMode.ARCHITECT: "High-level design focus - Read only",
}

# Mode personalities for flavor
MODE_PERSONALITIES: dict[VibeMode, str] = {
    VibeMode.PLAN: "The Wise Mentor",
    VibeMode.NORMAL: "The Professional",
    VibeMode.AUTO: "The Expert",
    VibeMode.YOLO: "The Speedrunner",
    VibeMode.ARCHITECT: "The Visionary",
}

# Tips for each mode - shown when mode changes
MODE_TIPS: dict[VibeMode, list[str]] = {
    VibeMode.PLAN: [
        "💡 I'll research and plan before making any changes",
        "📋 Press Shift+Tab to switch to NORMAL/AUTO when ready to execute",
        "🔍 Try 'list all files in src/' or 'show me the config'",
    ],
    VibeMode.NORMAL: [
        "💡 I'll ask before each file modification",
        "✅ Reply Y/n/always when prompted for tool approval",
        "⚡ Use Shift+Tab to switch to AUTO for faster execution",
    ],
    VibeMode.AUTO: [
        "💡 Tools are auto-approved - I'll execute without asking",
        "⚠️ I'll still explain what I'm doing",
        "🛑 Press Ctrl+C to interrupt if needed",
    ],
    VibeMode.YOLO: [
        "🚀 MAXIMUM SPEED - minimal output, instant execution",
        "⚡ I'll chain actions rapidly without commentary",
        "🛑 Press Ctrl+C to interrupt, or Shift+Tab for safer mode",
    ],
    VibeMode.ARCHITECT: [
        "🏛️ High-level design focus - I'll think in systems",
        "📐 I'll create diagrams and propose architectures",
        "📋 No file modifications - design only",
    ],
}

# =============================================================================
# TOOL CATEGORIZATION
# =============================================================================

# Read-only tools that are always allowed regardless of mode
READONLY_TOOLS: frozenset[str] = frozenset({
    # File reading
    "read_file",
    "grep",
    "list_files",
    "find_files",
    "view_file",
    "search_files",
    "get_file_contents",
    "read",
    # Git read operations
    "git_status",
    "git_log",
    "git_diff",
    "git_show",
    "git_branch",
    # Todo/task reading
    "todo_read",
    "list_todos",
    "get_todos",
    # Context/info tools
    "get_time",
    "get_context",
    "get_cwd",
    "get_working_directory",
    # MCP read patterns
    "mcp_read",
    "mcp_get",
    "mcp_list",
    "mcp_search",
})

# Write tools that require permission
WRITE_TOOLS: frozenset[str] = frozenset({
    "write_file",
    "create_file",
    "delete_file",
    "remove_file",
    "edit_file",
    "patch_file",
    "search_replace",
    "modify_file",
    "todo_write",
    "todo_create",
    "todo_update",
})

# Bash commands that are safe (read-only)
READONLY_BASH_COMMANDS: frozenset[str] = frozenset({
    # Basic file viewing
    "ls",
    "cat",
    "head",
    "tail",
    "find",
    "grep",
    "egrep",
    "fgrep",
    "wc",
    "file",
    "which",
    "whereis",
    "pwd",
    "echo",
    "date",
    "whoami",
    "tree",
    "less",
    "more",
    "stat",
    "du",
    "df",
    "env",
    "printenv",
    "hostname",
    "uname",
    "id",
    "groups",
    "type",
    "command",
    # Git (base command - further analysis needed for subcommands)
    "git",
})

# Safe git subcommands that are read-only
SAFE_GIT_SUBCOMMANDS: frozenset[str] = frozenset({
    "status",
    "log",
    "diff",
    "show",
    "branch",
    "tag",
    "describe",
    "ls-files",
    "ls-tree",
    "ls-remote",
    "remote",
    "config",
    "help",
    "version",
    "reflog",
    "shortlog",
    "blame",
    "annotate",
    "grep",
    "rev-parse",
    "rev-list",
    "cat-file",
    "fsck",
    "count-objects",
})

# =============================================================================
# SECURITY PATTERNS
# =============================================================================

# Bash patterns that indicate write operations
# FIX A.2: Expanded Security Patterns (60+ entries)
# Acts as a firewall against write operations in Read-Only modes
WRITE_BASH_PATTERNS: tuple[str, ...] = (
    # 1. File Modification
    r"\brm[\s$]",
    r"\brmdir\b",
    r"\bmv[\s$]",
    r"\bcp[\s$]",
    r"\btouch[\s$]",
    r"\bmkdir[\s$]",
    r"\btruncate[\s$]",
    r"\bshred[\s$]",
    # 2. Redirection & Piping (Write)
    r">",  # Classic redirect
    r">>",  # Append redirect
    r"\|\&",  # Coprocess pipe (often used for evasion)
    r"tee\b",  # Write to file pipe
    # 3. In-place Edits
    r"\bsed\s+.*-i",  # sed --in-place
    r"\bawk\s+.*-i",  # awk --in-place
    r"\bperl\s+.*-i",  # perl --in-place
    # 4. Permissions & Ownership
    r"\bchmod[\s$]",
    r"\bchown[\s$]",
    r"\bchgrp[\s$]",
    r"\bchattr[\s$]",
    # 5. Dangerous Execution / Shell Manipulation
    r"\beval[\s$]",
    r"\bsource[\s$]",
    r"\b\.[\s$]",  # Source alias .
    r"\bexec[\s$]",
    r"\bdd[\s$]",  # Data duplicator (destroyer)
    r"\bmknod[\s$]",
    r"\bmkfifo[\s$]",
    # 6. Evasion Techniques
    r"\$\{IFS\}",  # Internal Field Separator abuse
    r"\$IFS",
    r">\s*\(",  # Process substitution output
    r"<\s*\(",  # Process substitution input (can be abused)
    # 7. Git Dangerous Operations
    r"\bgit\s+commit\b",
    r"\bgit\s+push\b",
    r"\bgit\s+checkout\b",  # Can modify files
    r"\bgit\s+reset\b",  # Can modify files
    r"\bgit\s+rebase\b",
    r"\bgit\s+merge\b",
    r"\bgit\s+stash\b",
    r"\bgit\s+cherry-pick\b",
    r"\bgit\s+clean\b",
    r"\bgit\s+apply\b",
    r"\bgit\s+rm\b",
    r"\bgit\s+mv\b",
    r"\bgit\s+init\b",
    r"\bgit\s+clone\b",
    r"\bgit\s+restore\b",
    r"\bgit\s+switch\b",
    r"\bgit\s+pull\b",  # Modifies workspace
    r"\bgit\s+config\s+.*core\.editor",  # Config modification
    # 8. Network Downloads (File Writing)
    r"\bcurl\s+.*-[oO#]",  # Output to file
    r"\bwget\s+.*-O",  # Output to file
    # 9. Package Managers (Install/Remove)
    r"\bpip\s+(?:install|uninstall)\b",
    r"\bpip3\s+(?:install|uninstall)\b",
    r"\buv\s+(?:pip|add|remove)\b",
    r"\bnpm\s+(?:install|i|add|remove|update)\b",
    r"\byarn\s+(?:add|remove)\b",
    r"\bpnpm\s+(?:add|remove)\b",
    r"\bapt\s+(?:install|remove|purge)\b",
    r"\bapt-get\s+(?:install|remove|purge)\b",
    r"\byum\s+(?:install|remove)\b",
    r"\bdnf\s+(?:install|remove)\b",
    r"\bbrew\s+(?:install|uninstall)\b",
    r"\bpacman\s+-[RS]",
    r"\bgem\s+(?:install|uninstall)\b",
    r"\bcargo\s+(?:install|uninstall)\b",
    # 10. Dangerous Privileges
    r"\bsudo[\s$]",
    r"\bsu[\s$]",
    r"\bdoas[\s$]",
)

# Maximum length for command display in error messages
MAX_COMMAND_DISPLAY_LEN: int = 100

# =============================================================================
# MODE CONFIGURATIONS
# =============================================================================

# Pre-built configurations for each mode
MODE_CONFIGS: dict[VibeMode, ModeConfig] = {
    VibeMode.PLAN: ModeConfig(
        auto_approve=False,
        read_only=True,
        emoji=MODE_EMOJIS[VibeMode.PLAN],
        description=MODE_DESCRIPTIONS[VibeMode.PLAN],
        personality=MODE_PERSONALITIES[VibeMode.PLAN],
    ),
    VibeMode.NORMAL: ModeConfig(
        auto_approve=False,
        read_only=False,
        emoji=MODE_EMOJIS[VibeMode.NORMAL],
        description=MODE_DESCRIPTIONS[VibeMode.NORMAL],
        personality=MODE_PERSONALITIES[VibeMode.NORMAL],
    ),
    VibeMode.AUTO: ModeConfig(
        auto_approve=True,
        read_only=False,
        emoji=MODE_EMOJIS[VibeMode.AUTO],
        description=MODE_DESCRIPTIONS[VibeMode.AUTO],
        personality=MODE_PERSONALITIES[VibeMode.AUTO],
    ),
    VibeMode.YOLO: ModeConfig(
        auto_approve=True,
        read_only=False,
        emoji=MODE_EMOJIS[VibeMode.YOLO],
        description=MODE_DESCRIPTIONS[VibeMode.YOLO],
        personality=MODE_PERSONALITIES[VibeMode.YOLO],
    ),
    VibeMode.ARCHITECT: ModeConfig(
        auto_approve=False,
        read_only=True,
        emoji=MODE_EMOJIS[VibeMode.ARCHITECT],
        description=MODE_DESCRIPTIONS[VibeMode.ARCHITECT],
        personality=MODE_PERSONALITIES[VibeMode.ARCHITECT],
    ),
}
