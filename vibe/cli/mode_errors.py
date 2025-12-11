"""ChefChat Mode System - Error Handling & Edge Cases
===================================================

Comprehensive error handling for the mode system with:
- Detection methods for each scenario
- User-friendly error messages
- Recovery and fallback strategies
- Logging for debugging

Usage:
    from vibe.cli.mode_errors import (
        ModeError,
        handle_mode_conflict,
        handle_tool_block,
        safe_mode_operation,
    )
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import auto
from functools import wraps
import logging
from typing import TYPE_CHECKING, Any, TypeVar

from vibe.core.compatibility import StrEnum

if TYPE_CHECKING:
    from vibe.modes import ModeManager, VibeMode

# Configure module logger
logger = logging.getLogger("chefchat.modes")

# Type variable for decorators
T = TypeVar("T")

# Constants
_LOG_ARG_MAX_LENGTH = 100  # Max length before truncating args in logs


# =============================================================================
# ERROR TYPES
# =============================================================================


class ModeErrorType(StrEnum):
    """Categories of mode-related errors."""

    # User conflicts
    WRITE_IN_READONLY = auto()  # User tried to write in PLAN/ARCHITECT
    TOOL_BLOCKED = auto()  # Tool execution blocked by mode
    APPROVAL_REQUIRED = auto()  # Action needs user approval

    # System issues
    MODE_CORRUPTION = auto()  # Mode state became invalid
    PROMPT_TOO_LONG = auto()  # System prompt exceeded limit
    KEYBIND_FAILED = auto()  # Shift+Tab not working

    # Model issues
    MODEL_NONCOMPLIANCE = auto()  # Model ignored mode instructions
    INVALID_APPROVAL = auto()  # Model didn't recognize approval signal

    # Configuration
    PERSISTENCE_FAILED = auto()  # Couldn't save mode state
    CONFIG_INVALID = auto()  # Invalid mode configuration


@dataclass
class ModeError:
    """Structured error information for mode-related issues."""

    error_type: ModeErrorType
    message: str
    user_message: str
    recovery_hint: str
    context: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_display_message(self) -> str:
        """Format error for display to user."""
        return f"""## ⚠️ {self.error_type.value.replace("_", " ").title()}

{self.user_message}

### 💡 What you can do:

{self.recovery_hint}

---
*If this keeps happening, check the logs or report an issue.*
"""

    def log(self, level: int = logging.WARNING) -> None:
        """Log this error with context."""
        logger.log(
            level,
            "ModeError[%s]: %s | context=%s",
            self.error_type.value,
            self.message,
            self.context,
        )


# =============================================================================
# ERROR MESSAGES - User-friendly messages with solutions
# =============================================================================


def create_write_blocked_error(
    tool_name: str, mode: VibeMode, args: dict[str, Any] | None = None
) -> ModeError:
    """Create error for write operation blocked in read-only mode."""
    from vibe.modes import MODE_CONFIGS

    config = MODE_CONFIGS[mode]

    return ModeError(
        error_type=ModeErrorType.WRITE_IN_READONLY,
        message=f"Tool '{tool_name}' blocked in {mode.value} mode",
        user_message=f"""The tool **`{tool_name}`** cannot run in **{config.emoji} {mode.value.upper()}** mode.

This mode is **read-only** to protect your codebase while you're planning or designing.""",
        recovery_hint="""1. **Press `Shift+Tab`** to switch to a mode that allows writes:
   - ⚡ **AUTO** - Auto-approve all tools
   - ✋ **NORMAL** - Confirm each tool individually
   - 🚀 **YOLO** - Maximum speed, no confirmations
2. **Add to plan** - Let me document this in the implementation plan instead

> **Note:** Read-only mode blocks writes intentionally. Switch modes to execute.""",
        context={"tool_name": tool_name, "mode": mode.value, "args": args},
    )


def create_tool_blocked_error(tool_name: str, mode: VibeMode, reason: str) -> ModeError:
    """Create error for any tool blocking scenario."""
    from vibe.modes import MODE_CONFIGS

    config = MODE_CONFIGS[mode]

    return ModeError(
        error_type=ModeErrorType.TOOL_BLOCKED,
        message=f"Tool '{tool_name}' blocked: {reason}",
        user_message=f"""**`{tool_name}`** was blocked in **{config.emoji} {mode.value.upper()}** mode.

{reason}""",
        recovery_hint="""**Options:**
- Press **`Shift+Tab`** to switch to a writeable mode
- Type **`/modes`** to see all available modes""",
        context={"tool_name": tool_name, "mode": mode.value, "reason": reason},
    )


def create_mode_corruption_error(
    details: str, current_state: dict[str, Any] | None = None
) -> ModeError:
    """Create error for corrupted mode state."""
    return ModeError(
        error_type=ModeErrorType.MODE_CORRUPTION,
        message=f"Mode state corruption detected: {details}",
        user_message="""**The mode system encountered an unexpected state.**

This shouldn't happen in normal use. The mode has been reset to NORMAL for safety.""",
        recovery_hint="""1. **Press `Shift+Tab`** to cycle to your desired mode
2. **Type `/chef`** to check current mode status
3. **Type `/reload`** to reload configuration

If this keeps happening, please report it!""",
        context={"details": details, "state": current_state or {}},
    )


def create_prompt_too_long_error(
    prompt_length: int, max_length: int, mode: VibeMode
) -> ModeError:
    """Create error for system prompt exceeding limits."""
    return ModeError(
        error_type=ModeErrorType.PROMPT_TOO_LONG,
        message=f"System prompt too long: {prompt_length} > {max_length}",
        user_message=f"""**The system prompt is too long!**

Current length: **{prompt_length:,}** characters
Maximum allowed: **{max_length:,}** characters

This can happen with very large project contexts in verbose modes.""",
        recovery_hint="""1. **Switch to YOLO mode** (🚀) - Uses minimal prompts
2. **Clear history** with `/clear`
3. **Reduce project context** in config
4. **Use `/compact`** to summarize conversation""",
        context={
            "prompt_length": prompt_length,
            "max_length": max_length,
            "mode": mode.value,
        },
    )


def create_model_noncompliance_error(
    expected_behavior: str, actual_behavior: str, mode: VibeMode
) -> ModeError:
    """Create error when model ignores mode instructions."""
    return ModeError(
        error_type=ModeErrorType.MODEL_NONCOMPLIANCE,
        message=f"Model noncompliance: expected '{expected_behavior}', got '{actual_behavior}'",
        user_message=f"""**The model didn't follow the mode instructions!**

I was supposed to: **{expected_behavior}**
But I did: **{actual_behavior}**

This can happen when:
- The model is overloaded
- Instructions are unclear
- Context window is too full""",
        recovery_hint="""1. **Remind me** - Say "Remember, you're in {mode.value.upper()} mode"
2. **Switch modes** with `Shift+Tab` and back again
3. **Clear context** with `/clear` and try again
4. **Try a different model** with `/config`""",
        context={
            "expected": expected_behavior,
            "actual": actual_behavior,
            "mode": mode.value,
        },
    )


def create_keybind_failed_error(key_combo: str = "Shift+Tab") -> ModeError:
    """Create error when keybinding doesn't work."""
    return ModeError(
        error_type=ModeErrorType.KEYBIND_FAILED,
        message=f"Keybinding '{key_combo}' not working",
        user_message=f"""**`{key_combo}` doesn't seem to be working!**

This can happen with certain terminal emulators or SSH connections.""",
        recovery_hint="""**Alternative ways to change modes:**

1. **Type `/modes`** to see current mode and options
2. **Ask me directly**: "Switch to YOLO mode" or "Go to PLAN mode"
3. **Check terminal**: Some terminals intercept Shift+Tab
4. **Try different terminal**: iTerm2, Alacritty, or native terminal

**Terminal-specific fixes:**
- **tmux**: May need to configure key passthrough
- **VSCode**: Check keyboard shortcuts settings
- **SSH**: Try `ssh -t` for proper TTY""",
        context={"key_combo": key_combo},
    )


# =============================================================================
# RECOVERY STRATEGIES
# =============================================================================


def reset_to_safe_mode(mode_manager: ModeManager) -> VibeMode:
    """Reset to NORMAL mode as a safe fallback.

    Args:
        mode_manager: The ModeManager to reset

    Returns:
        The new mode (NORMAL)
    """
    from vibe.modes import VibeMode

    logger.warning(
        "Resetting to NORMAL mode from %s due to error", mode_manager.current_mode.value
    )
    mode_manager.set_mode(VibeMode.NORMAL)
    return VibeMode.NORMAL


def validate_mode_state(mode_manager: ModeManager) -> tuple[bool, ModeError | None]:
    """Validate that mode state is consistent.

    Args:
        mode_manager: The ModeManager to validate

    Returns:
        Tuple of (is_valid, error_if_invalid)
    """
    from vibe.modes import MODE_CONFIGS, VibeMode

    try:
        state = mode_manager.state
        mode = state.current_mode

        # Check mode is valid
        if mode not in VibeMode:
            return False, create_mode_corruption_error(
                f"Invalid mode value: {mode}", state.to_dict()
            )

        # Check config consistency
        config = MODE_CONFIGS[mode]
        if state.auto_approve != config.auto_approve:
            logger.warning(
                "Auto-approve mismatch: state=%s, config=%s",
                state.auto_approve,
                config.auto_approve,
            )
            # Auto-fix
            state.auto_approve = config.auto_approve

        if state.read_only_tools != config.read_only:
            logger.warning(
                "Read-only mismatch: state=%s, config=%s",
                state.read_only_tools,
                config.read_only,
            )
            # Auto-fix
            state.read_only_tools = config.read_only

        return True, None

    except Exception as e:
        logger.exception("Mode state validation failed")
        return False, create_mode_corruption_error(str(e))


# =============================================================================
# DECORATORS FOR SAFE OPERATIONS
# =============================================================================


def safe_mode_operation(
    fallback_mode: VibeMode | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator for safe mode operations with automatic recovery.

    Args:
        fallback_mode: Mode to fall back to on error (default: NORMAL)

    Usage:
        @safe_mode_operation()
        def risky_mode_function(self):
            ...
    """
    from vibe.modes import VibeMode

    _fallback = fallback_mode or VibeMode.NORMAL

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return func(*args, **kwargs)
            except Exception:
                logger.exception(
                    "Safe mode operation failed in %s, falling back to %s",
                    func.__name__,
                    _fallback.value,
                )
                # Try to find mode_manager in args
                mode_manager = None
                for arg in args:
                    if hasattr(arg, "mode_manager"):
                        mode_manager = arg.mode_manager
                        break
                    if hasattr(arg, "set_mode"):
                        mode_manager = arg
                        break

                if mode_manager:
                    mode_manager.set_mode(_fallback)

                raise

        return wrapper

    return decorator


# =============================================================================
# LOGGING HELPERS
# =============================================================================


def log_mode_transition(
    old_mode: VibeMode, new_mode: VibeMode, trigger: str = "user"
) -> None:
    """Log a mode transition.

    Args:
        old_mode: Previous mode
        new_mode: New mode
        trigger: What triggered the transition ('user', 'auto', 'error')
    """
    logger.info(
        "Mode transition: %s -> %s (trigger=%s)",
        old_mode.value,
        new_mode.value,
        trigger,
    )


def log_tool_block(
    tool_name: str,
    mode: VibeMode,
    args: dict[str, Any] | None = None,
    reason: str = "read-only mode",
) -> None:
    """Log a tool being blocked.

    Args:
        tool_name: Name of the blocked tool
        mode: Current mode
        args: Tool arguments (sanitized)
        reason: Why it was blocked
    """
    # Sanitize args - don't log full file contents
    safe_args = {}
    if args:
        for key, value in args.items():
            if isinstance(value, str) and len(value) > _LOG_ARG_MAX_LENGTH:
                safe_args[key] = f"<{len(value)} chars>"
            else:
                safe_args[key] = value

    logger.warning(
        "Tool blocked: %s in %s mode | reason=%s | args=%s",
        tool_name,
        mode.value,
        reason,
        safe_args,
    )


def log_mode_error(error: ModeError) -> None:
    """Log a mode error with full context.

    Args:
        error: The ModeError to log
    """
    logger.error(
        "ModeError[%s]: %s | context=%s | timestamp=%s",
        error.error_type.value,
        error.message,
        error.context,
        error.timestamp.isoformat(),
    )


# =============================================================================
# GRACEFUL DEGRADATION
# =============================================================================


def create_fallback_mode_manager() -> ModeManager:
    """Create a minimal ModeManager when normal creation fails.

    This is used as a last resort to keep the app running.

    Returns:
        A basic ModeManager in NORMAL mode
    """
    from vibe.modes import ModeManager, VibeMode

    logger.warning("Creating fallback ModeManager due to initialization failure")
    return ModeManager(initial_mode=VibeMode.NORMAL)


def check_mode_system_health(mode_manager: ModeManager | None) -> dict[str, Any]:
    """Check the health of the mode system.

    Returns:
        Dict with health status and any issues found
    """
    health = {
        "healthy": True,
        "issues": [],
        "mode": None,
        "auto_approve": None,
        "read_only": None,
    }

    if mode_manager is None:
        health["healthy"] = False
        health["issues"].append("ModeManager is None")
        return health

    try:
        health["mode"] = mode_manager.current_mode.value
        health["auto_approve"] = mode_manager.auto_approve
        health["read_only"] = mode_manager.read_only_tools

        # Validate state
        is_valid, error = validate_mode_state(mode_manager)
        if not is_valid:
            health["healthy"] = False
            health["issues"].append(
                error.message if error else "Unknown validation error"
            )

    except Exception as e:
        health["healthy"] = False
        health["issues"].append(str(e))
        logger.exception("Mode system health check failed")

    return health


# =============================================================================
# USER CONFUSION HELPERS
# =============================================================================


def explain_current_mode(mode_manager: ModeManager) -> str:
    """Generate a clear explanation of the current mode.

    Useful when user seems confused about why something isn't working.
    """
    from vibe.modes import MODE_CONFIGS

    mode = mode_manager.current_mode
    config = MODE_CONFIGS[mode]

    explanation = f"""## 📍 You're Currently in {config.emoji} {mode.value.upper()} Mode

**What this means:**
"""

    if config.read_only:
        explanation += """- 🔒 **Read-only**: I can read files but cannot write or modify them
- 📋 This is a planning/design mode - I'll help you think through changes first
"""
    elif config.auto_approve:
        explanation += """- 🤖 **Auto-approve**: All tool executions happen automatically
- ⚡ Maximum speed - I don't wait for confirmation
"""
    else:
        explanation += """- ✋ **Confirm each**: I'll ask before running write operations
- 🛡️ Safe mode - you control what gets executed
"""

    explanation += f"""
**To change modes:**
- Press **`Shift+Tab`** to cycle through modes
- Type **`/modes`** to see all options
- Say **"Switch to [MODE] mode"** to change directly

**Current mode description:**
{config.description}
"""

    return explanation


def suggest_mode_for_task(task_description: str) -> str:
    """Suggest the best mode for a given task.

    Args:
        task_description: What the user wants to do

    Returns:
        Suggestion message
    """
    task_lower = task_description.lower()

    # Planning/research tasks
    planning_keywords = [
        "plan",
        "think",
        "explore",
        "research",
        "understand",
        "analyze",
    ]
    if any(kw in task_lower for kw in planning_keywords):
        return """💡 **Suggestion:** Consider using **📋 PLAN mode** for this task.

It's great for:
- Understanding existing code
- Creating implementation plans
- Exploring without changing anything

Press `Shift+Tab` until you see 📋 PLAN"""

    # Design/architecture tasks
    design_keywords = ["design", "architect", "structure", "diagram", "pattern"]
    if any(kw in task_lower for kw in design_keywords):
        return """💡 **Suggestion:** Consider using **🏛️ ARCHITECT mode** for this task.

It's great for:
- High-level system design
- Creating architecture diagrams
- Discussing patterns and trade-offs

Press `Shift+Tab` until you see 🏛️ ARCHITECT"""

    # Quick fix tasks
    quick_keywords = ["quick", "fast", "just", "simple", "quick fix", "hotfix"]
    if any(kw in task_lower for kw in quick_keywords):
        return """💡 **Suggestion:** Consider using **🚀 YOLO mode** for quick tasks.

It's great for:
- Quick fixes you're confident about
- Shipping under time pressure
- When you trust the changes

Press `Shift+Tab` until you see 🚀 YOLO"""

    # Default to AUTO for most implementation work
    return """💡 **Suggestion:** **⚡ AUTO mode** is good for most coding tasks.

- Auto-approves tool execution
- Still explains what's being done
- Good balance of speed and visibility

Press `Shift+Tab` until you see ⚡ AUTO"""


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "ModeError",
    "ModeErrorType",
    "check_mode_system_health",
    "create_fallback_mode_manager",
    "create_keybind_failed_error",
    "create_mode_corruption_error",
    "create_model_noncompliance_error",
    "create_prompt_too_long_error",
    "create_tool_blocked_error",
    "create_write_blocked_error",
    "explain_current_mode",
    "log_mode_error",
    "log_mode_transition",
    "log_tool_block",
    "reset_to_safe_mode",
    "safe_mode_operation",
    "suggest_mode_for_task",
    "validate_mode_state",
]
