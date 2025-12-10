"""ChefChat Mode Manager
=====================

A comprehensive multi-mode system for the ChefChat CLI.

Provides 5 operational modes that change how the assistant behaves:
- 📋 PLAN: Read-only, creates detailed plans before execution
- ✋ NORMAL: Asks confirmation for each tool
- ⚡ AUTO: Auto-approves all tools
- 🚀 YOLO: Ultra-fast, minimal output, auto-approve
- 🏛️ ARCHITECT: High-level design focus, read-only

Usage:
    from vibe.cli.mode_manager import ModeManager, VibeMode

    # Create manager with initial mode
    manager = ModeManager(initial_mode=VibeMode.NORMAL)

    # Cycle through modes (Shift+Tab behavior)
    old_mode, new_mode = manager.cycle_mode()

    # Check tool permissions
    if manager.should_approve_tool("write_file"):
        # Tool is auto-approved
        pass

    # Get system prompt injection
    prompt = inject_mode_into_system_prompt(base_prompt, manager)

Author: ChefChat Team
Version: 2.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

try:
    from enum import StrEnum, auto
except ImportError:
    from enum import Enum, auto

    class StrEnum(str, Enum):
        pass


import re
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prompt_toolkit.key_binding import KeyBindings


# =============================================================================
# ENUMS AND CONSTANTS
# =============================================================================


class VibeMode(StrEnum):
    """The five operational modes of ChefChat.

    Each mode changes tool execution permissions, communication style,
    and system prompt behavior.
    """

    PLAN = auto()  # 📋 Research & Planning - Read-only
    NORMAL = auto()  # ✋ Safe & Steady - Ask for each tool
    AUTO = auto()  # ⚡ Trust & Execute - Auto-approve all
    YOLO = auto()  # 🚀 Move Fast - Maximum speed, minimal output
    ARCHITECT = auto()  # 🏛️ Design Mode - High-level, read-only


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

# Bash patterns that indicate write operations
# Bash patterns that indicate write operations
WRITE_BASH_PATTERNS: tuple[str, ...] = (
    # =========================================================================
    # FIX A.2: Expanded Security Patterns (60+ entries)
    # acts as a firewall against write operations in Read-Only modes
    # =========================================================================
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
# DATA CLASSES
# =============================================================================


@dataclass
class ModeConfig:
    """Configuration for a single mode."""

    auto_approve: bool
    read_only: bool
    emoji: str
    description: str
    personality: str


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


@dataclass
class ModeState:
    """Tracks the current mode state and history.

    Attributes:
        current_mode: The active operational mode
        auto_approve: Whether tools are auto-approved
        read_only_tools: Whether write operations are blocked
        started_at: When this mode was activated
        mode_history: Log of mode transitions with timestamps
    """

    current_mode: VibeMode
    auto_approve: bool = False
    read_only_tools: bool = True
    started_at: datetime = field(default_factory=datetime.now)
    mode_history: list[tuple[VibeMode, datetime]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialize from mode config."""
        config = MODE_CONFIGS[self.current_mode]
        self.auto_approve = config.auto_approve
        self.read_only_tools = config.read_only
        if not self.mode_history:
            self.mode_history = [(self.current_mode, self.started_at)]

    def to_dict(self) -> dict[str, Any]:
        """Serialize state for logging/debugging."""
        return {
            "mode": self.current_mode.value,
            "auto_approve": self.auto_approve,
            "read_only": self.read_only_tools,
            "started_at": self.started_at.isoformat(),
            "transitions": len(self.mode_history),
        }


# =============================================================================
# MODE MANAGER CLASS
# =============================================================================


class ModeManager:
    """Central manager for the ChefChat mode system.

    Handles mode cycling, transitions, tool permission checks,
    and system prompt injection.

    Example:
        >>> manager = ModeManager(initial_mode=VibeMode.NORMAL)
        >>> print(manager.get_mode_indicator())
        ✋ NORMAL
        >>> old, new = manager.cycle_mode()
        >>> print(f"{old} -> {new}")
        normal -> auto
    """

    # Class-level constants
    CYCLE_ORDER: ClassVar[tuple[VibeMode, ...]] = MODE_CYCLE_ORDER

    def __init__(self, initial_mode: VibeMode = VibeMode.NORMAL) -> None:
        """Initialize the mode manager.

        Args:
            initial_mode: Starting mode (default: NORMAL for safety)
        """
        self.state = ModeState(current_mode=initial_mode)
        self._compiled_write_patterns: list[re.Pattern[str]] = [
            re.compile(pattern) for pattern in WRITE_BASH_PATTERNS
        ]

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def current_mode(self) -> VibeMode:
        """Get the current mode."""
        return self.state.current_mode

    @property
    def auto_approve(self) -> bool:
        """Whether tools should be auto-approved."""
        return self.state.auto_approve

    @property
    def read_only_tools(self) -> bool:
        """Whether write operations are blocked."""
        return self.state.read_only_tools

    @property
    def config(self) -> ModeConfig:
        """Get the configuration for the current mode."""
        return MODE_CONFIGS[self.state.current_mode]

    # -------------------------------------------------------------------------
    # Mode Transitions
    # -------------------------------------------------------------------------

    def cycle_mode(self) -> tuple[VibeMode, VibeMode]:
        """Cycle to the next mode (Shift+Tab behavior).

        Returns:
            Tuple of (old_mode, new_mode)
        """
        old_mode = self.state.current_mode
        try:
            current_idx = self.CYCLE_ORDER.index(old_mode)
            next_idx = (current_idx + 1) % len(self.CYCLE_ORDER)
        except ValueError:
            # Mode not in cycle order, start from beginning
            next_idx = 0

        new_mode = self.CYCLE_ORDER[next_idx]
        self.set_mode(new_mode)
        return old_mode, new_mode

    def set_mode(self, mode: VibeMode) -> None:
        """Set a specific mode.

        Args:
            mode: The mode to switch to
        """
        config = MODE_CONFIGS[mode]
        now = datetime.now()

        self.state.current_mode = mode
        self.state.auto_approve = config.auto_approve
        self.state.read_only_tools = config.read_only
        self.state.started_at = now
        self.state.mode_history.append((mode, now))

    # -------------------------------------------------------------------------
    # Tool Permission Checks
    # -------------------------------------------------------------------------

    def should_approve_tool(self, tool_name: str) -> bool:
        """Determine if a tool should be automatically approved.

        Args:
            tool_name: Name of the tool being called

        Returns:
            True if tool should be auto-approved, False if confirmation needed
        """
        # Auto-approve mode approves everything
        if self.state.auto_approve:
            return True

        # In read-only mode, only approve read-only tools
        if self.state.read_only_tools:
            return tool_name in READONLY_TOOLS

        # In normal mode (not auto, not read-only), nothing is auto-approved
        return False

    def is_write_operation(
        self, tool_name: str, args: dict[str, Any] | None = None
    ) -> bool:
        """Detect if an operation would write to files.

        Args:
            tool_name: Name of the tool
            args: Tool arguments (for bash command analysis)

        Returns:
            True if this is a write operation
        """
        # Check known write tools
        if tool_name in WRITE_TOOLS:
            return True

        # Check bash/shell commands
        if tool_name in {"bash", "shell", "run_command", "execute_command"}:
            return self._is_write_bash_command(args)

        # Check for read-only tools
        if tool_name in READONLY_TOOLS:
            return False

        # Unknown tools are assumed to be potentially write operations
        return True

    def _is_write_bash_command(self, args: dict[str, Any] | None) -> bool:  # noqa: PLR0911
        """Check if a bash command is a write operation."""
        if not args:
            return False

        # Get command from various possible arg names
        command = (
            args.get("command")
            or args.get("cmd")
            or args.get("CommandLine")
            or args.get("commandLine")
            or ""
        )

        if not command:
            return False

        command = str(command).strip()
        parts = command.split()

        if not parts:
            return False

        base_cmd = parts[0]

        # Check for write patterns FIRST (redirects, rm, etc.)
        # This catches "echo hi > file" even though echo is readonly
        for pattern in self._compiled_write_patterns:
            if pattern.search(command):
                return True

        # Special handling for git commands
        if base_cmd == "git" and len(parts) > 1:
            subcommand = parts[1]
            # Safe git subcommands are read-only
            if subcommand in SAFE_GIT_SUBCOMMANDS:
                return False
            # Unknown git commands are assumed writes
            return True

        # Known read-only commands are safe
        if base_cmd in READONLY_BASH_COMMANDS:
            return False

        # Unknown commands - be cautious, assume write
        return True

    def should_block_tool(
        self, tool_name: str, args: dict[str, Any] | None = None
    ) -> tuple[bool, str | None]:
        """Check if a tool should be blocked in the current mode.

        Args:
            tool_name: Name of the tool being called
            args: Tool arguments

        Returns:
            Tuple of (blocked: bool, reason: str | None)
        """
        # If not in read-only mode, nothing is blocked
        if not self.state.read_only_tools:
            return False, None

        # Check if this is a write operation
        if not self.is_write_operation(tool_name, args):
            return False, None

        # Block and provide helpful message
        mode_name = self.state.current_mode.value.upper()
        emoji = self.config.emoji

        reason = f"""⛔ Tool '{tool_name}' blocked in {emoji} {mode_name} mode

This operation would modify files. Current mode is read-only for safety.

**Options:**
1. Press Shift+Tab to switch to NORMAL or AUTO mode
2. Let me add this to the implementation plan instead

What would you like to do?"""

        return True, reason

    # -------------------------------------------------------------------------
    # Display Methods
    # -------------------------------------------------------------------------

    def get_mode_indicator(self) -> str:
        """Get a display string for the current mode.

        Returns:
            String like "📋 PLAN" or "🚀 YOLO"
        """
        emoji = self.config.emoji
        name = self.state.current_mode.value.upper()
        return f"{emoji} {name}"

    def get_mode_description(self) -> str:
        """Get the short description of current mode.

        Returns:
            One-line description of mode behavior
        """
        return self.config.description

    def get_transition_message(self, old_mode: VibeMode, new_mode: VibeMode) -> str:
        """Get the message to display when transitioning modes.

        Args:
            old_mode: The mode being left
            new_mode: The mode being entered

        Returns:
            Formatted transition message
        """
        new_config = MODE_CONFIGS[new_mode]
        return (
            f"🔄 Mode: {old_mode.value.upper()} → {new_mode.value.upper()}\n"
            f"{new_config.emoji} {new_mode.value.upper()}: "
            f"{new_config.description}"
        )

    # -------------------------------------------------------------------------
    # System Prompt Injection
    # -------------------------------------------------------------------------

    def get_system_prompt_modifier(self) -> str:
        """Get mode-specific system prompt injection.

        This XML block should be prepended to the system prompt
        to inform the LLM about the current mode's rules.

        Returns:
            XML-formatted mode instruction block
        """
        mode = self.state.current_mode

        if mode == VibeMode.PLAN:
            return """<active_mode>📋 PLAN MODE</active_mode>
<mode_rules>
You are in PLAN MODE - "Measure twice, cut once"

STRICT RULES:
- You MAY ONLY use read-only tools: read_file, grep, bash (ls/cat/grep only)
- You MUST NOT write, modify, or delete any files under any circumstances
- You MUST create detailed implementation plans before making any changes
- You MUST wait for explicit approval ("approved", "go ahead", "execute") before switching to execution
- Think out loud, show your reasoning, be thorough

COMMUNICATION STYLE:
- Use emoji indicators: 🔍 (researching), 📋 (planning), 💭 (thinking)
- Be verbose and pedagogical - help the user understand
- Use the structured PLAN FORMAT for proposals
- Ask clarifying questions proactively
- Validate assumptions with "Correct me if I'm wrong, but..."

When the user approves your plan, acknowledge and begin execution.
</mode_rules>"""

        elif mode == VibeMode.NORMAL:
            return """<active_mode>✋ NORMAL MODE</active_mode>
<mode_rules>
You are in NORMAL MODE - "Safe and steady"

RULES:
- Read operations are auto-approved
- Each write operation requires user confirmation before execution
- Explain what you're about to do before doing it
- Be thorough but not overwhelming

COMMUNICATION STYLE:
- Use emoji indicators: ✋ (waiting), ✅ (done), ⚠️ (warning)
- Be concise but complete
- Confirm before risky operations
- Explain rationale for important decisions
</mode_rules>"""

        elif mode == VibeMode.AUTO:
            return """<active_mode>⚡ AUTO MODE</active_mode>
<mode_rules>
You are in AUTO MODE - "Trust and execute"

RULES:
- All tools are auto-approved - execute without waiting for confirmation
- Still explain what you're doing and why
- Think before acting, but don't ask permission
- Be confident but not reckless

COMMUNICATION STYLE:
- Use emoji indicators: ⚡ (executing), ✅ (success), 🔧 (fixing)
- Confident and efficient
- Keep momentum while maintaining quality
- Explain your choices but don't wait for input
</mode_rules>"""

        elif mode == VibeMode.YOLO:
            return """<active_mode>🚀 YOLO MODE</active_mode>
<mode_rules>
You are in YOLO MODE - "Move fast, ship faster"

RULES:
- ALL tools are auto-approved INSTANTLY
- MINIMIZE output - only the absolute essentials
- No verbose explanations unless something goes wrong
- Execute rapidly and efficiently
- Trust your instincts, but maintain code quality

COMMUNICATION STYLE:
- ULTRA-CONCISE - pure signal, zero fluff
- Format: "✓ [action]" for success
- Format: "✗ [error]" for failure
- Only become verbose when errors occur
- Chain actions without commentary

EXAMPLE OUTPUT:
✓ read api/routes.py
✓ found auth pattern
✓ write auth/middleware.py
✓ update routes.py
✓ tests pass
Done. Auth middleware added.
</mode_rules>"""

        elif mode == VibeMode.ARCHITECT:
            return """<active_mode>🏛️ ARCHITECT MODE</active_mode>
<mode_rules>
You are in ARCHITECT MODE - "Design the cathedral"

STRICT RULES:
- HIGH-LEVEL DESIGN focus only - you are designing, not building
- You MAY ONLY use read-only tools to gather context
- You MUST NOT modify any files
- Think in systems, patterns, and abstractions
- Use mermaid diagrams for visualization
- Present multiple architectural options with trade-offs
- Consider: scalability, maintainability, extensibility, security

COMMUNICATION STYLE:
- Use emoji indicators: 🏛️ (designing), 📐 (structuring), 💭 (thinking)
- Abstract and conceptual
- Focus on "what" and "why", not "how"
- Use the structured ARCHITECTURE FORMAT
- Ask about non-functional requirements
</mode_rules>"""

        return ""


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def setup_mode_keybindings(kb: KeyBindings, mode_manager: ModeManager) -> None:
    """Setup keyboard bindings for mode cycling.

    Adds Shift+Tab binding to cycle through modes.

    Args:
        kb: prompt_toolkit KeyBindings instance
        mode_manager: ModeManager to control

    Usage:
        from prompt_toolkit.key_binding import KeyBindings
        kb = KeyBindings()
        setup_mode_keybindings(kb, mode_manager)
    """
    from prompt_toolkit.keys import Keys

    @kb.add(Keys.BackTab)  # Shift+Tab
    def cycle_mode_handler(event: Any) -> None:
        """Shift+Tab: Cycle through modes."""
        old_mode, new_mode = mode_manager.cycle_mode()

        # Get display information
        indicator = mode_manager.get_mode_indicator()
        description = mode_manager.get_mode_description()
        tips = MODE_TIPS.get(new_mode, [])

        # Write feedback to terminal
        output = event.app.output
        output.write(
            f"\n\n🔄 Mode: {old_mode.value.upper()} → {new_mode.value.upper()}\n"
        )
        output.write(f"{indicator}: {description}\n")

        # Show tips for the new mode
        if tips:
            output.write("\n")
            for tip in tips:
                output.write(f"  {tip}\n")
        output.write("\n")
        output.flush()


def get_mode_banner(mode_manager: ModeManager) -> str:
    """Generate a startup banner for the current mode.

    Args:
        mode_manager: ModeManager instance

    Returns:
        ASCII art banner with mode info
    """
    indicator = mode_manager.get_mode_indicator()
    description = mode_manager.get_mode_description()

    # Pad strings for alignment
    indicator_padded = f"{indicator:60}"
    description_padded = f"{description:60}"

    return f"""
╔════════════════════════════════════════════════════════════════╗
║  {indicator_padded} ║
║  {description_padded} ║
║                                                                ║
║  💡 Press Shift+Tab to cycle modes                            ║
║  🍳 Type /chef for kitchen status                             ║
╚════════════════════════════════════════════════════════════════╝
"""


def inject_mode_into_system_prompt(base_prompt: str, mode_manager: ModeManager) -> str:
    """Inject mode-specific instructions into the system prompt.

    Args:
        base_prompt: The original system prompt
        mode_manager: ModeManager with current mode state

    Returns:
        System prompt with mode instructions prepended
    """
    mode_modifier = mode_manager.get_system_prompt_modifier()

    if not mode_modifier:
        return base_prompt

    return f"{mode_modifier}\n\n{base_prompt}"


def mode_from_auto_approve(auto_approve: bool) -> VibeMode:
    """Convert legacy auto_approve boolean to a VibeMode.

    Used for backwards compatibility with existing CLI flags.

    Args:
        auto_approve: The --auto-approve flag value

    Returns:
        VibeMode.AUTO if True, VibeMode.NORMAL if False
    """
    return VibeMode.AUTO if auto_approve else VibeMode.NORMAL


# =============================================================================
# MODE-AWARE TOOL EXECUTOR
# =============================================================================


@runtime_checkable
class ToolExecutorProtocol(Protocol):
    """Protocol for tool executors."""

    async def __call__(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool and return the result."""
        ...


class ModeAwareToolExecutor:
    """Wrapper around tool execution that respects mode permissions.

    - Blocks write tools in read-only modes (PLAN, ARCHITECT)
    - Returns helpful error messages with suggestions
    - Truncates output in YOLO mode for speed

    Example:
        original_executor = your_tool_executor
        wrapped = ModeAwareToolExecutor(mode_manager, original_executor)
        result = await wrapped.execute_tool("write_file", {"path": "foo.py"})
    """

    # Maximum result length in YOLO mode
    YOLO_MAX_RESULT_LEN: ClassVar[int] = 500

    def __init__(
        self,
        mode_manager: ModeManager,
        original_executor: Callable[..., Awaitable[dict[str, Any]]],
    ) -> None:
        """Initialize the mode-aware executor.

        Args:
            mode_manager: ModeManager instance for permission checks
            original_executor: The actual tool execution function
        """
        self.mode_manager = mode_manager
        self.original_executor = original_executor

    async def execute_tool(
        self, tool_name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a tool with mode-aware logic.

        Args:
            tool_name: Name of the tool to execute
            args: Arguments for the tool

        Returns:
            Tool execution result, or error dict if blocked
        """
        # Check if tool should be blocked
        blocked, reason = self.mode_manager.should_block_tool(tool_name, args)

        if blocked:
            return {
                "error": True,
                "blocked": True,
                "message": reason,
                "tool": tool_name,
                "mode": self.mode_manager.current_mode.value,
            }

        # Execute the tool
        result = await self.original_executor(tool_name, args)

        # In YOLO mode, truncate large results
        if self.mode_manager.current_mode == VibeMode.YOLO:
            result = self._truncate_for_yolo(result)

        return result

    def _truncate_for_yolo(self, result: dict[str, Any]) -> dict[str, Any]:
        """Truncate result for YOLO mode's minimal output."""
        if not isinstance(result, dict):
            return result

        truncated = {}
        for key, value in result.items():
            if isinstance(value, str) and len(value) > self.YOLO_MAX_RESULT_LEN:
                truncated[key] = value[: self.YOLO_MAX_RESULT_LEN] + "... [truncated]"
            else:
                truncated[key] = value

        return truncated


# =============================================================================
# EXPORTS
# =============================================================================


__all__ = [
    "MODE_CONFIGS",
    "MODE_CYCLE_ORDER",
    "READONLY_TOOLS",
    "WRITE_TOOLS",
    "ModeAwareToolExecutor",
    "ModeConfig",
    "ModeManager",
    "ModeState",
    "VibeMode",
    "get_mode_banner",
    "inject_mode_into_system_prompt",
    "mode_from_auto_approve",
    "setup_mode_keybindings",
]
